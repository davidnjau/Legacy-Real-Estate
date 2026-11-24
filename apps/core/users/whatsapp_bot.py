"""Conversational WhatsApp flows (Phase 2).

Routes inbound webhook messages: ID-number verification links a WhatsApp
number to saved records, then role-scoped menus serve tenant self-service,
landlord approvals, and caretaker maintenance, move-in handover, and inspection
workflows.

Session replies here are free-form interactive messages, allowed because an
inbound customer message always opens Meta's 24-hour service window. Outbound
notifications outside that window stay on the Phase 1 template queue.
"""

import hashlib
import json
import logging
import uuid
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from botocore.exceptions import BotoCoreError
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from users import daraja
from users.email import email_delivery_is_configured, send_email
from users.models import (
    AuditLog,
    ConversationState,
    Invoice,
    Landlord,
    MaintenanceQuotation,
    MaintenanceTicket,
    MaintenanceTicketComment,
    MoveInChecklistItem,
    MoveInPackage,
    MoveOutNotice,
    Payment,
    PaymentAllocation,
    Property,
    PropertyInspection,
    Receipt,
    RentStkRequest,
    Tenancy,
    Tenant,
    TenantCredit,
    UtilityConfiguration,
    WhatsAppContact,
)
from users.object_storage import delete_private_object, upload_private_object
from users.payment_allocations import (
    apply_tenant_credit,
    available_tenant_credit,
    invoice_outstanding_amount,
    lock_tenant_invoice,
)
from users.roles import CARETAKER_ROLE, has_role

logger = logging.getLogger(__name__)

MAX_VERIFICATION_ATTEMPTS = 5
VERIFICATION_LOCK_HOURS = 24
MAX_TICKET_PHOTOS = 5
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_HANDOVER_PHOTOS = 10
MAX_EXIT_INSPECTION_PHOTOS = 10
ALLOWED_PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png"}
# Conversations idle beyond this restart from the greeting on return.
SESSION_TTL_MINUTES = 5

ROLE_TENANT = "tenant"
ROLE_LANDLORD = "landlord"
ROLE_CARETAKER = "caretaker"
ROLE_LABELS = {
    ROLE_TENANT: "Tenant",
    ROLE_LANDLORD: "Landlord",
    ROLE_CARETAKER: "Caretaker",
}

CATEGORY_ROWS = [
    (f"cat_{value}", label) for value, label in MaintenanceTicket.CATEGORY_CHOICES
]


# ---------------------------------------------------------------------------
# Cloud API session messaging and media retrieval
# ---------------------------------------------------------------------------


def _post_message(message: dict) -> None:
    if settings.WHATSAPP_BACKEND == "users.whatsapp.ConsoleWhatsAppBackend":
        logger.warning("Development-only WhatsApp session message: %s", message)
        return
    body = json.dumps({"messaging_product": "whatsapp", **message}).encode("utf-8")
    request = Request(
        "https://graph.facebook.com/"
        f"{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages",
        data=body,
        headers={
            "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.WHATSAPP_TIMEOUT_SECONDS):
            pass
    except (HTTPError, URLError, TimeoutError):
        logger.exception("WhatsApp session message could not be sent.")


SESSION_HELP = (
    "ℹ️ Type *cancel* to exit, *menu* to start over. "
    f"Chats close after {SESSION_TTL_MINUTES} minutes of inactivity."
)
MENU_FOOTER = f"cancel exits · menu restarts · closes after {SESSION_TTL_MINUTES} idle min"


def _identity_line(wa_id: str) -> str:
    contact = (
        WhatsAppContact.objects.filter(wa_id=wa_id, verified_at__isnull=False)
        .select_related("tenant", "user", "conversation_state")
        .first()
    )
    if contact is None:
        return ""
    state = getattr(contact, "conversation_state", None)
    role = ROLE_LABELS.get(state.active_role) if state else None
    name = ""
    if contact.tenant_id:
        name = contact.tenant.name.split()[0]
    elif contact.user_id:
        profile = getattr(contact.user, "profile", None)
        if profile is not None and profile.name:
            name = profile.name.split()[0]
        else:
            landlord = Landlord.objects.filter(portal_user_id=contact.user_id).first()
            if landlord:
                name = landlord.name.split()[0]
    if name and role:
        return f"👤 {name} · {role}"
    if name:
        return f"👤 {name}"
    return ""


def send_text(wa_id: str, body: str) -> None:
    identity = _identity_line(wa_id)
    footer = (identity + "\n" if identity else "") + SESSION_HELP
    _post_message({
        "to": wa_id,
        "type": "text",
        "text": {"body": f"{body}\n\n{footer}"},
    })


def send_buttons(wa_id: str, body: str, buttons: list[tuple[str, str]]) -> None:
    identity = _identity_line(wa_id)
    _post_message({
        "to": wa_id,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": f"{identity}\n\n{body}" if identity else body},
            "footer": {"text": MENU_FOOTER},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": option_id, "title": title}}
                    for option_id, title in buttons
                ],
            },
        },
    })


def send_list(wa_id: str, body: str, button: str, rows: list[tuple[str, str, str]]) -> None:
    identity = _identity_line(wa_id)
    _post_message({
        "to": wa_id,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": f"{identity}\n\n{body}" if identity else body},
            "footer": {"text": MENU_FOOTER},
            "action": {
                "button": button,
                "sections": [{
                    "title": "Options",
                    "rows": [
                        {"id": row_id, "title": title, "description": description}
                        for row_id, title, description in rows
                    ],
                }],
            },
        },
    })


def _fetch_media(media_id: str) -> tuple[bytes, str]:
    """Resolve a webhook media id to its bytes and content type.

    Meta returns a CDN URL that expires within minutes, so both requests
    happen back to back and the URL is never stored.
    """
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}
    lookup = Request(
        "https://graph.facebook.com/"
        f"{settings.WHATSAPP_API_VERSION}/{media_id}",
        headers=headers,
    )
    with urlopen(lookup, timeout=settings.WHATSAPP_TIMEOUT_SECONDS) as response:
        descriptor = json.loads(response.read())
    download = Request(descriptor["url"], headers=headers)
    with urlopen(download, timeout=settings.WHATSAPP_TIMEOUT_SECONDS) as response:
        return response.read(), descriptor.get("mime_type", "")


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def _normalise_id_number(value: str) -> str:
    return "".join(value.split()).upper()


def _match_identities(id_number: str):
    """Match an ID to the roles allowed on WhatsApp: tenant, landlord,
    caretaker. Other staff (directors, accountants, officers) are
    deliberately not matched — the channel is gated to these three."""
    from django.db.models import Q

    from users.models import UserProfile

    normalised = _normalise_id_number(id_number)
    # Tolerate separator mix-ups: LL_889900 and LL889900 match LL-889900.
    variants = {
        normalised,
        normalised.replace("_", "-"),
        normalised.replace("-", "_"),
    }

    def _match(field):
        query = Q()
        for variant in variants:
            query |= Q(**{f"{field}__iexact": variant})
        return query

    tenant = Tenant.objects.filter(
        _match("national_id_or_passport")
    ).order_by("-created_at").first()
    landlord = Landlord.objects.filter(
        _match("id_or_registration_number")
    ).order_by("-created_at").first()
    caretaker_user = None
    profile = (
        UserProfile.objects.filter(_match("id_or_registration_number"))
        .select_related("user")
        .first()
    )
    if profile is not None and has_role(profile.user, CARETAKER_ROLE):
        caretaker_user = profile.user
    return tenant, landlord, caretaker_user


def _roles(contact: WhatsAppContact) -> list[str]:
    roles = []
    if contact.tenant_id:
        roles.append(ROLE_TENANT)
    if contact.user_id and Landlord.objects.filter(portal_user_id=contact.user_id).exists():
        roles.append(ROLE_LANDLORD)
    if contact.user_id and has_role(contact.user, CARETAKER_ROLE):
        roles.append(ROLE_CARETAKER)
    return roles


def _audit(action: str, values: dict) -> None:
    AuditLog.objects.create(
        user=None,
        action=action,
        entity="WhatsAppContact",
        new_values=values,
    )


# ---------------------------------------------------------------------------
# Flows
# ---------------------------------------------------------------------------


def _prompt_for_id(contact: WhatsAppContact, state: ConversationState) -> None:
    state.state = {"flow": "verify", "step": "awaiting_id"}
    _save_state(state)
    send_text(
        contact.wa_id,
        "Welcome to Legacy Real Estate! 🔐 To link this WhatsApp number to "
        "your account, please reply with your national ID number (or company "
        "registration number).",
    )


def _handle_verification_reply(contact, state, text):
    now = timezone.now()
    if contact.locked_until and contact.locked_until > now:
        send_text(
            contact.wa_id,
            "Too many failed attempts. Please try again later or contact the office.",
        )
        return
    candidate = _normalise_id_number(text)
    # Greetings and chatter must not burn verification attempts — only
    # plausible ID numbers (6+ characters including at least one digit)
    # count toward the lockout.
    if (
        not candidate
        or len(candidate) < 6
        or len(candidate) > 100
        or not any(ch.isdigit() for ch in candidate)
    ):
        send_text(
            contact.wa_id,
            "Please reply with just your national ID number (or company "
            "registration number) to verify your account.",
        )
        return
    tenant, landlord, caretaker_user = _match_identities(candidate)
    if tenant is None and landlord is None and caretaker_user is None:
        contact.failed_verifications += 1
        if contact.failed_verifications >= MAX_VERIFICATION_ATTEMPTS:
            contact.locked_until = now + timedelta(hours=VERIFICATION_LOCK_HOURS)
            contact.failed_verifications = 0
            message = (
                "That ID number does not match our records. Verification is "
                "paused for 24 hours — please contact the office if you need help."
            )
        else:
            message = (
                "That ID number does not match our records. Please check it and try again."
            )
        contact.save(update_fields=["failed_verifications", "locked_until", "updated_at"])
        _audit("whatsapp.verification_failed", {"contact_id": contact.pk})
        send_text(contact.wa_id, message)
        return

    contact.tenant = tenant
    contact.user = (
        (tenant.portal_user if tenant else None)
        or (landlord.portal_user if landlord else None)
        or caretaker_user
    )
    # Whichever ID was used, surface every identity on the same portal user —
    # a landlord who is also a tenant gets both roles from one verification.
    if contact.tenant is None and contact.user is not None:
        contact.tenant = (
            Tenant.objects.filter(portal_user=contact.user)
            .order_by("-created_at")
            .first()
        )
    contact.id_number_hash = hashlib.sha256(candidate.lower().encode("utf-8")).hexdigest()
    contact.verified_at = now
    contact.failed_verifications = 0
    contact.locked_until = None
    contact.save(update_fields=[
        "tenant",
        "user",
        "id_number_hash",
        "verified_at",
        "failed_verifications",
        "locked_until",
        "updated_at",
    ])
    _audit("whatsapp.contact_verified", {
        "contact_id": contact.pk,
        "tenant_id": tenant.pk if tenant else None,
        "landlord_id": landlord.pk if landlord else None,
        "caretaker_user_id": caretaker_user.pk if caretaker_user else None,
    })
    if tenant:
        full_name = tenant.name
    elif landlord:
        full_name = landlord.name
    else:
        full_name = caretaker_user.profile.name
    name = full_name.split()[0]
    send_text(contact.wa_id, f"✅ Thank you {name}, your number is now verified.")
    _show_entry(contact, state)


def _show_entry(contact, state):
    roles = _roles(contact)
    if len(roles) > 1:
        state.active_role = ""
        state.state = {"flow": "choose_role"}
        _save_state(state)
        send_buttons(
            contact.wa_id,
            "You have more than one role with us. How would you like to continue today?",
            [(f"role_{role}", ROLE_LABELS[role]) for role in roles[:3]],
        )
        return
    state.active_role = roles[0] if roles else ""
    _show_menu(contact, state)


SWITCH_ROW = ("switch_role", "Switch role", "Use a different role on this account")


def _show_menu(contact, state):
    state.state = {"flow": "menu"}
    _save_state(state)
    multi_role = len(_roles(contact)) > 1
    if state.active_role == ROLE_LANDLORD:
        rows = [
            ("l_summary", "Portfolio summary", "Properties, units and occupancy"),
            ("l_approvals", "Pending approvals", "Maintenance quotations awaiting you"),
        ]
        if multi_role:
            rows.append(SWITCH_ROW)
        send_list(
            contact.wa_id,
            "🏠 Welcome to your landlord services. What would you like to see?",
            "Landlord services",
            rows,
        )
        return
    if state.active_role == ROLE_CARETAKER:
        rows = [
            ("c_tickets", "Maintenance tickets", "View and update work at your properties"),
            ("c_report", "Report an issue", "Log an on-site maintenance issue"),
            ("c_checklists", "Checklists & inspections", "Move-in/out inspections and routine checks"),
        ]
        if multi_role:
            rows.append(SWITCH_ROW)
        send_list(
            contact.wa_id,
            "🔧 Caretaker services. What would you like to do?",
            "Caretaker menu",
            rows,
        )
        return
    if contact.tenant is None:
        send_text(
            contact.wa_id,
            "Your account is verified but has no active services on WhatsApp "
            "yet. Please contact the office for help.",
        )
        return
    rows = [
        ("t_balance", "Rent balance", "What you owe and when it's due"),
        ("t_pay", "Pay rent", "Pay via M-PESA right here"),
        ("t_receipts", "My receipts", "Your recent payment receipts"),
        ("t_issue", "Report an issue", "Repairs and maintenance, with photos"),
    ]
    if multi_role:
        rows.append(SWITCH_ROW)
    send_list(
        contact.wa_id,
        f"Hi {contact.tenant.name.split()[0]}! How can we help today?",
        "Tenant services",
        rows,
    )


def _outstanding_invoices(tenant):
    zero = Decimal("0.00")
    invoices = list(
        Invoice.objects.filter(
            tenancy__tenant=tenant,
            status=Invoice.STATUS_ISSUED,
        ).order_by("-period_start", "-id")[:12]
    )
    outstanding = []
    for invoice in invoices:
        due = invoice_outstanding_amount(invoice)
        if due > zero:
            outstanding.append((invoice, due))
    return outstanding


def _tenant_balance(contact):
    outstanding = _outstanding_invoices(contact.tenant)
    if not outstanding:
        send_text(contact.wa_id, "🎉 You are fully paid up — no outstanding balance.")
        return
    lines = [
        f"• {invoice.number} ({invoice.period_start.strftime('%b %Y')}): KES {due:,.2f}"
        for invoice, due in outstanding[:3]
    ]
    balance = sum(due for _, due in outstanding)
    body = "📋 Your rent balance\n\n" + "\n".join(lines)
    body += f"\n\nTotal outstanding: KES {balance:,.2f}"
    send_text(contact.wa_id, body)


def _tenant_receipts(contact):
    receipts = list(
        Receipt.objects.filter(tenant=contact.tenant).order_by("-issued_at")[:3]
    )
    if not receipts:
        send_text(contact.wa_id, "You have no payment receipts yet.")
        return
    lines = [
        f"• {receipt.number}: KES {receipt.amount:,.2f} "
        f"on {receipt.issued_at.strftime('%d %b %Y')}"
        for receipt in receipts
    ]
    send_text(contact.wa_id, "🧾 Your recent receipts\n\n" + "\n".join(lines))


def _start_pay_flow(contact, state):
    if not daraja.mpesa_is_configured():
        send_text(
            contact.wa_id,
            "M-PESA payments are not available right now. Please use the "
            "paybill details on your invoice.",
        )
        return
    outstanding = _outstanding_invoices(contact.tenant)
    if not outstanding:
        credit = available_tenant_credit(contact.tenant)
        message = "🎉 You are fully paid up — nothing to pay."
        if credit > 0:
            message += f"\nYour available rent credit is KES {credit:,.2f}."
        send_text(contact.wa_id, message)
        return

    invoice, due = outstanding[0]
    credit = available_tenant_credit(contact.tenant)
    if credit >= due:
        with transaction.atomic():
            invoice = lock_tenant_invoice(contact.tenant_id, invoice.pk)
            credit_applied, remaining_due = apply_tenant_credit(invoice)
        if remaining_due <= 0:
            state.state = {"flow": "menu"}
            _save_state(state)
            send_text(
                contact.wa_id,
                f"✅ Your rent credit of KES {credit_applied:,.2f} has covered "
                f"the latest outstanding invoice ({invoice.number}). "
                "No M-PESA payment is needed.",
            )
            return
        due = remaining_due
        credit = Decimal("0.00")

    amount_due = due - credit
    amount_due = amount_due.to_integral_value(rounding=ROUND_CEILING)
    state.state = {
        "flow": "pay",
        "step": "awaiting_phone",
        "invoice_id": invoice.pk,
    }
    _save_state(state)
    send_text(
        contact.wa_id,
        f"💳 *Pay Rent via M-PESA*\n\n"
        f"Your latest outstanding invoice is *{invoice.number}*.\n"
        f"Amount to pay after available rent credit: *KES {amount_due:,.0f}*.\n\n"
        "Reply with the Safaricom phone number that should receive the "
        "M-PESA PIN prompt (e.g. 0712345678). Enter your PIN on the M-PESA "
        "prompt—not in this chat. I’ll send your confirmation and receipt here "
        "after payment succeeds. Reply *cancel* to stop.",
    )


def _prompt_payment_amount(contact, state, invoice_id, phone):
    match = next(
        (
            (invoice, due)
            for invoice, due in _outstanding_invoices(contact.tenant)
            if invoice.pk == invoice_id
        ),
        None,
    )
    if match is None:
        send_text(contact.wa_id, "That invoice is no longer outstanding.")
        return
    invoice, due = match
    credit = available_tenant_credit(contact.tenant)
    amount_due = max(due - credit, Decimal("0.00")).to_integral_value(
        rounding=ROUND_CEILING
    )
    state.state = {
        "flow": "pay",
        "step": "awaiting_amount",
        "invoice_id": invoice_id,
        "phone": phone,
    }
    _save_state(state)
    send_text(
        contact.wa_id,
        f"Your latest outstanding invoice is *{invoice.number}*. "
        f"Available rent credit: *KES {credit:,.2f}*.\n"
        f"Reply *FULL* to pay the remaining *KES {amount_due:,.0f}*, or reply "
        "with the amount you want to pay in whole shillings. You may pay "
        "partially or pay extra; any amount above the invoice balance will "
        "remain as rent credit.\n\n"
        "I’ll send the M-PESA PIN prompt to the number you provided. "
        "Reply *cancel* to stop.",
    )


def _confirm_payment(contact, state, invoice_id):
    for invoice, due in _outstanding_invoices(contact.tenant):
        if invoice.pk == invoice_id:
            state.state = {
                "flow": "pay",
                "step": "awaiting_phone",
                "invoice_id": invoice_id,
            }
            _save_state(state)
            send_text(
                contact.wa_id,
                f"💳 *Pay Rent via M-PESA*\n\n"
                f"Invoice: {invoice.number}\n"
                f"Amount Due: KES {due:,.2f}\n\n"
                "Reply with the Safaricom phone number that should receive the "
                "M-PESA PIN prompt (e.g. 0712345678). After the prompt arrives, "
                "enter your PIN in the M-PESA prompt—not in this chat. "
                "I’ll send your confirmation and receipt here after payment succeeds. "
                "Reply *cancel* to stop.",
            )
            return
    send_text(contact.wa_id, "That invoice is no longer outstanding.")


def _initiate_stk(contact, state, invoice_id, phone, amount_text):
    match = [
        (invoice, due)
        for invoice, due in _outstanding_invoices(contact.tenant)
        if invoice.pk == invoice_id
    ]
    if not match:
        send_text(contact.wa_id, "That invoice is no longer outstanding.")
        return
    invoice, due = match[0]
    credit = available_tenant_credit(contact.tenant)
    amount_due = max(due - credit, Decimal("0.00"))
    if amount_text.strip().lower() in {"full", "full amount", "all"}:
        amount = amount_due.to_integral_value(rounding=ROUND_CEILING)
    else:
        try:
            amount = Decimal(amount_text.strip())
        except (InvalidOperation, TypeError, ValueError):
            send_text(
                contact.wa_id,
                "Enter a whole-shilling amount (for example 15000) or reply "
                "*FULL* for the remaining invoice balance.",
            )
            return
        if (
            not amount.is_finite()
            or amount < 0
            or amount != amount.to_integral_value()
            or (amount == 0 and amount_due > 0)
        ):
            send_text(
                contact.wa_id,
                "Enter a positive whole-shilling amount, or reply *FULL* for "
                "the remaining invoice balance.",
            )
            return

    if amount == 0:
        with transaction.atomic():
            invoice = lock_tenant_invoice(contact.tenant_id, invoice.pk)
            credit_applied, remaining_due = apply_tenant_credit(invoice)
        if remaining_due <= 0:
            state.state = {"flow": "menu"}
            _save_state(state)
            send_text(
                contact.wa_id,
                f"✅ Your rent credit of KES {credit_applied:,.2f} has covered "
                f"the latest outstanding invoice ({invoice.number}). "
                "No M-PESA payment is needed.",
            )
            return
        send_text(
            contact.wa_id,
            "Your available credit no longer covers the invoice. Reply *FULL* "
            "or enter an amount to continue.",
        )
        return

    try:
        normalized_phone = daraja.normalize_ke_phone(phone)
    except ValueError as exc:
        send_text(
            contact.wa_id,
            f"⚠️ {exc}\nPlease reply with a valid Kenyan Safaricom phone number (e.g. 0712345678).",
        )
        return

    try:
        result = daraja.stk_push(
            normalized_phone,
            amount,
            invoice.number,
            "Rent",
        )
    except Exception:
        logger.exception("Rent STK push could not be initiated.")
        send_text(
            contact.wa_id,
            "The M-PESA prompt could not be started. Reply *FULL* or enter "
            "the amount again to retry, or reply *cancel* to stop.",
        )
        return
    RentStkRequest.objects.create(
        invoice=invoice,
        tenant=contact.tenant,
        contact=contact,
        amount=amount,
        phone=normalized_phone,
        checkout_request_id=result["checkout_request_id"],
        merchant_request_id=result["merchant_request_id"],
    )
    state.state = {"flow": "menu"}
    _save_state(state)
    send_text(
        contact.wa_id,
        f"📲 An M-PESA prompt for KES {amount:,.0f} has been pushed to {normalized_phone}.\n"
        "Please enter your PIN on your handset to complete the payment — your confirmation and official receipt will arrive here.",
    )


def _send_payment_receipt_email(tenant, reference, amount):
    if not tenant.email:
        return
    if not email_delivery_is_configured():
        logger.info("Receipt email skipped: email delivery is not configured.")
        return
    subject = f"Payment received — {reference}"
    text_content = (
        f"Dear {tenant.name},\n\n"
        f"Your payment of KES {amount:,.2f} (reference {reference}) has "
        "been received. Thank you.\n\nLegacy Real Estate"
    )
    try:
        send_email(tenant.email, subject, text_content)
    except Exception:
        logger.exception("Payment receipt email could not be sent.")


def _complete_rent_payment(stk_request, receipt_number, paid_amount):
    from users.sms import (
        dispatch_next_sms_delivery,
        queue_template_sms,
        sms_backend_is_configured,
    )
    from users.views import _ensure_payment_receipt

    tenant = stk_request.tenant
    invoice = stk_request.invoice
    credit_applied = Decimal("0.00")
    new_credit = Decimal("0.00")
    with transaction.atomic():
        invoice = lock_tenant_invoice(tenant.pk, invoice.pk)
        credit_applied, outstanding = apply_tenant_credit(invoice)

        allocated_amount = min(paid_amount, outstanding)
        new_credit = paid_amount - allocated_amount
        payment = Payment.objects.create(
            tenant=tenant,
            amount=paid_amount,
            reference=receipt_number,
            source=Payment.SOURCE_MPESA,
            account_reference=invoice.number,
            payer_phone=stk_request.phone,
            received_at=timezone.now(),
            status=(
                Payment.STATUS_CREDIT
                if allocated_amount == 0
                else Payment.STATUS_PARTIALLY_ALLOCATED
                if new_credit
                else Payment.STATUS_ALLOCATED
            ),
            allocated_amount=allocated_amount,
            credit_amount=new_credit,
        )
        if allocated_amount:
            PaymentAllocation.objects.create(
                payment=payment,
                invoice=invoice,
                amount=allocated_amount,
            )
        if new_credit:
            TenantCredit.objects.create(
                tenant=tenant,
                payment=payment,
                amount=new_credit,
                remaining_amount=new_credit,
            )
        stk_request.status = RentStkRequest.STATUS_SUCCEEDED
        stk_request.result_code = "0"
        stk_request.payment = payment
        stk_request.processed_at = timezone.now()
        stk_request.save(update_fields=[
            "status", "result_code", "payment", "processed_at", "updated_at",
        ])
    # Receipt record + WhatsApp template fan-out (Phase 1 wiring)
    _ensure_payment_receipt(payment)
    if tenant.phone and sms_backend_is_configured():
        try:
            queue_template_sms(
                tenant.phone,
                "payment_receipt",
                {
                    "reference": receipt_number,
                    "amount": f"{paid_amount:,.2f}",
                    "agency_name": tenant.branch.agency.name,
                },
            )
            try:
                dispatch_next_sms_delivery()
            except Exception:
                pass
        except Exception:
            logger.exception("Receipt SMS could not be queued.")
    _send_payment_receipt_email(tenant, receipt_number, paid_amount)

    if stk_request.contact:
        try:
            confirmation = (
                f"✅ *Payment Received & Confirmed!*\n\n"
                f"Receipt No: *{receipt_number}*\n"
                f"Amount: *KES {paid_amount:,.2f}*\n"
                f"Invoice: *{invoice.number}*\n"
            )
            if credit_applied:
                confirmation += (
                    f"Rent credit applied: *KES {credit_applied:,.2f}*\n"
                )
            if new_credit:
                confirmation += (
                    f"Credit carried forward: *KES {new_credit:,.2f}*\n"
                )
            confirmation += (
                f"Tenant: *{tenant.name}*\n\n"
                "Your rent payment has been recorded and your ledger is up to date.\n"
                f"Thank you for choosing {tenant.branch.agency.name}!"
            )
            send_text(
                stk_request.contact.wa_id,
                confirmation,
            )
        except Exception:
            logger.exception("WhatsApp payment confirmation message failed.")

    AuditLog.objects.create(
        user=None,
        action="payment.rent_stk_completed",
        entity="Payment",
        new_values={
            "payment_id": payment.pk,
            "invoice_id": invoice.pk,
            "amount": str(paid_amount),
            "allocated_amount": str(allocated_amount),
            "credit_applied": str(credit_applied),
            "credit_amount": str(new_credit),
            "source": "whatsapp_stk" if stk_request.contact else "web_portal_stk",
        },
    )


def _landlord(contact):
    return Landlord.objects.filter(portal_user_id=contact.user_id).first()


def _landlord_summary(contact):
    landlord = _landlord(contact)
    if landlord is None:
        send_text(contact.wa_id, "No landlord record is linked to this number.")
        return
    from users.models import Property, Unit

    properties = Property.objects.filter(landlord=landlord)
    units = Unit.objects.filter(property__in=properties)
    occupied = units.filter(status=Unit.STATUS_OCCUPIED).count()
    pending = MaintenanceQuotation.objects.filter(
        ticket__property__landlord=landlord,
        status=MaintenanceQuotation.STATUS_SUBMITTED,
    ).count()
    send_text(
        contact.wa_id,
        "🏠 Your portfolio\n\n"
        f"Properties: {properties.count()}\n"
        f"Units: {units.count()} ({occupied} occupied)\n"
        f"Quotations awaiting approval: {pending}\n\n"
        "Full statements and remittances are on your portal dashboard.",
    )


def _landlord_approvals(contact, state):
    landlord = _landlord(contact)
    if landlord is None:
        send_text(contact.wa_id, "No landlord record is linked to this number.")
        return
    from users.views import _maintenance_approval_threshold

    quotations = [
        quotation
        for quotation in MaintenanceQuotation.objects.filter(
            ticket__property__landlord=landlord,
            status=MaintenanceQuotation.STATUS_SUBMITTED,
        ).select_related("ticket__property", "ticket__unit", "vendor")[:5]
        if quotation.amount > _maintenance_approval_threshold(quotation.ticket)
    ]
    if not quotations:
        send_text(contact.wa_id, "✅ Nothing is waiting for your approval.")
        return
    for quotation in quotations:
        unit_label = quotation.ticket.unit.code if quotation.ticket.unit else "common area"
        send_buttons(
            contact.wa_id,
            f"🔧 Quotation #{quotation.pk} — {quotation.ticket.property.name}, "
            f"{unit_label}\n\n{quotation.ticket.title}\n"
            f"Vendor: {quotation.vendor.name}\n"
            f"Amount: KES {quotation.amount:,.2f}",
            [
                (f"q_app_{quotation.pk}", "Approve"),
                (f"q_rej_{quotation.pk}", "Reject"),
            ],
        )


def _decide_quotation(contact, state, quotation_id, approved, reason=""):
    landlord = _landlord(contact)
    if landlord is None:
        send_text(contact.wa_id, "No landlord record is linked to this number.")
        return
    with transaction.atomic():
        quotation = (
            MaintenanceQuotation.objects.select_for_update(of=("self",))
            .filter(
                pk=quotation_id,
                ticket__property__landlord=landlord,
                status=MaintenanceQuotation.STATUS_SUBMITTED,
            )
            .first()
        )
        if quotation is None:
            send_text(contact.wa_id, "That quotation has already been decided.")
            return
        quotation.approved_by = contact.user if approved else None
        quotation.approved_at = timezone.now() if approved else None
        quotation.status = (
            MaintenanceQuotation.STATUS_APPROVED
            if approved
            else MaintenanceQuotation.STATUS_REJECTED
        )
        quotation.rejection_reason = "" if approved else reason
        quotation.save()
    AuditLog.objects.create(
        user=contact.user,
        action="maintenance_quotation.landlord_decided",
        entity="MaintenanceQuotation",
        new_values={
            "quotation_id": quotation.pk,
            "approved": approved,
            "source": "whatsapp",
        },
    )
    state.state = {"flow": "menu"}
    _save_state(state)
    verdict = "approved ✅" if approved else "rejected ❌"
    send_text(contact.wa_id, f"Quotation #{quotation.pk} {verdict}. The team has been notified.")


def _caretaker_properties(contact):
    if not contact.user_id or not has_role(contact.user, CARETAKER_ROLE):
        return Property.objects.none()
    profile = getattr(contact.user, "profile", None)
    if profile is None:
        return Property.objects.none()
    return Property.objects.filter(
        branch__in=profile.accessible_branches(role_names=(CARETAKER_ROLE,)),
        caretaker_assignments__caretaker=contact.user,
        caretaker_assignments__ended_at__isnull=True,
    ).distinct()


def _caretaker_tickets(contact):
    return (
        MaintenanceTicket.objects.filter(
            property__in=_caretaker_properties(contact),
        )
        .select_related("property", "unit", "assigned_to")
        .order_by("-created_at")
    )


def _caretaker_ticket_list(contact, state, offset=0):
    state.state = {"flow": "menu"}
    _save_state(state)
    ticket_query = _caretaker_tickets(contact)
    total = ticket_query.count()
    tickets = list(ticket_query[offset : offset + 10])
    if not tickets:
        send_text(contact.wa_id, "There are no maintenance tickets at your properties.")
        return
    has_more = total > offset + len(tickets)
    if has_more:
        tickets = tickets[:9]
    rows = [
        (
            f"tkt_{ticket.pk}",
            f"#{ticket.pk} {ticket.title}"[:24],
            (
                f"{ticket.property.name} · {ticket.get_status_display()} · "
                f"{ticket.unit.code if ticket.unit else 'common area'}"
            )[:72],
        )
        for ticket in tickets
    ]
    if has_more:
        rows.append((f"c_page_{offset + len(tickets)}", "More tickets", "Show the next tickets"))
    send_list(
        contact.wa_id,
        "🔧 Maintenance tickets at your properties — choose one:",
        "Maintenance tickets",
        rows,
    )


def _caretaker_ticket_detail(contact, state, ticket_id):
    state.state = {"flow": "menu"}
    _save_state(state)
    ticket = _caretaker_tickets(contact).filter(pk=ticket_id).first()
    if ticket is None:
        send_text(contact.wa_id, "That ticket is not available in your assigned properties.")
        return
    unit_label = ticket.unit.code if ticket.unit else "common area"
    assignee = (
        ticket.assigned_to.get_full_name() or ticket.assigned_to.username
        if ticket.assigned_to
        else "Unassigned"
    )
    recent_comments = list(ticket.comments.select_related("author").order_by("-created_at")[:2])
    comments_text = ""
    if recent_comments:
        comments_text = "\n\nRecent updates:\n" + "\n".join(
            f"• {comment.author.get_full_name() or comment.author.username}: {comment.body[:120]}"
            for comment in reversed(recent_comments)
        )
    buttons = []
    if ticket.status == MaintenanceTicket.STATUS_OPEN and ticket.assigned_to_id is None:
        buttons.append((f"ca_{ticket.pk}", "Assign to me"))
    elif (
        ticket.status == MaintenanceTicket.STATUS_ASSIGNED
        and ticket.assigned_to_id == contact.user_id
    ):
        buttons.append((f"cs_{ticket.pk}", "Start work"))
    elif (
        ticket.status == MaintenanceTicket.STATUS_IN_PROGRESS
        and ticket.assigned_to_id == contact.user_id
    ):
        buttons.append((f"tc_{ticket.pk}", "Complete ticket"))
    buttons.extend(
        [
            (f"cm_{ticket.pk}", "Add update"),
            ("c_tickets", "Back to tickets"),
        ]
    )
    send_buttons(
        contact.wa_id,
        f"🔧 Ticket #{ticket.pk} — {ticket.property.name}, {unit_label}\n\n"
        f"{ticket.description[:400]}\n\n"
        f"Status: {ticket.get_status_display()} · Priority: {ticket.get_priority_display()}\n"
        f"Assigned to: {assignee}{comments_text}",
        buttons,
    )


def _caretaker_update_ticket_status(contact, state, ticket_id, new_status):
    if not contact.user_id or not has_role(contact.user, CARETAKER_ROLE):
        send_text(contact.wa_id, "Your account does not have caretaker access.")
        return
    try:
        with transaction.atomic():
            ticket = (
                _caretaker_tickets(contact)
                .select_for_update(of=("self",))
                .filter(pk=ticket_id)
                .first()
            )
            if ticket is None:
                send_text(contact.wa_id, "That ticket is not available in your assigned properties.")
                return
            old_status = ticket.status
            if new_status == MaintenanceTicket.STATUS_ASSIGNED:
                if ticket.status != MaintenanceTicket.STATUS_OPEN or ticket.assigned_to_id:
                    send_text(contact.wa_id, "That ticket is no longer available to claim.")
                    return
                ticket.assigned_to = contact.user
            elif ticket.assigned_to_id != contact.user_id:
                send_text(contact.wa_id, "Only the assigned caretaker can update this ticket.")
                return
            ticket.transition_to(new_status)
            ticket.full_clean()
            ticket.save()
            AuditLog.objects.create(
                user=contact.user,
                action="maintenance_ticket.status_changed",
                entity="MaintenanceTicket",
                old_values={"status": old_status},
                new_values={
                    "ticket_id": ticket.pk,
                    "status": ticket.status,
                    "assigned_to_id": ticket.assigned_to_id,
                    "source": "whatsapp",
                },
            )
    except ValidationError:
        logger.exception("Caretaker ticket status transition was rejected.")
        send_text(contact.wa_id, "That ticket cannot move to the requested status.")
        return
    except DatabaseError:
        logger.exception("Caretaker ticket status could not be saved.")
        send_text(contact.wa_id, "The ticket could not be updated. Please try again.")
        return
    send_text(
        contact.wa_id,
        f"✅ Ticket #{ticket.pk} is now {ticket.get_status_display().lower()}.",
    )
    _caretaker_ticket_detail(contact, state, ticket.pk)


def _caretaker_start_completion(contact, state, ticket_id):
    ticket = _caretaker_tickets(contact).filter(
        pk=ticket_id,
        assigned_to=contact.user,
        status=MaintenanceTicket.STATUS_IN_PROGRESS,
    ).first()
    if ticket is None:
        send_text(contact.wa_id, "Only your in-progress tickets can be completed.")
        return
    state.state = {
        "flow": "caretaker_complete",
        "step": "fundi_name",
        "ticket_id": ticket.pk,
        "photos": [],
    }
    _save_state(state)
    send_text(
        contact.wa_id,
        f"To complete ticket #{ticket.pk}, enter the technician/fundi's name.",
    )


def _caretaker_add_completion_photo(contact, state, image):
    photos = state.state.get("photos", [])
    if len(photos) >= MAX_TICKET_PHOTOS:
        send_text(contact.wa_id, f"You can add up to {MAX_TICKET_PHOTOS} completion photos.")
        return
    media_id = image.get("id")
    if not media_id:
        send_text(contact.wa_id, "I couldn't retrieve that photo. Please try sending it again.")
        return
    digest = image.get("sha256")
    if digest and any(photo.get("sha256") == digest for photo in photos):
        send_text(contact.wa_id, "That photo has already been added.")
        return
    try:
        content, content_type = _fetch_media(media_id)
    except (HTTPError, OSError, TypeError, ValueError):
        logger.exception("Caretaker completion photo could not be downloaded.")
        send_text(contact.wa_id, "I couldn't retrieve that photo. Please try sending it again.")
        return
    content_type = content_type or image.get("mime_type", "")
    signatures = {
        "image/jpeg": (b"\xff\xd8\xff", "jpg"),
        "image/png": (b"\x89PNG\r\n\x1a\n", "png"),
        "image/webp": (b"RIFF", "webp"),
    }
    signature = signatures.get(content_type)
    if (
        signature is None
        or len(content) > MAX_PHOTO_BYTES
        or not content.startswith(signature[0])
        or (content_type == "image/webp" and content[8:12] != b"WEBP")
    ):
        send_text(
            contact.wa_id,
            "Send a valid JPEG, PNG, or WebP completion photo (maximum 5 MB).",
        )
        return
    if sum(photo.get("size_bytes", 0) for photo in photos) + len(content) > 20 * 1024 * 1024:
        send_text(contact.wa_id, "The completion photos exceed the combined 20 MB limit.")
        return
    ticket_id = state.state.get("ticket_id")
    key = f"maintenance-tickets/completion/{ticket_id}/{uuid.uuid4().hex}.{signature[1]}"
    try:
        upload_private_object(key, content, content_type)
    except (BotoCoreError, ImproperlyConfigured, OSError, ValueError):
        logger.exception("Caretaker completion photo could not be stored.")
        send_text(contact.wa_id, "That photo could not be saved. Please try again.")
        return
    photos.append(
        {
            "storage_key": key,
            "content_type": content_type,
            "size_bytes": len(content),
            "name": f"Completion photo {len(photos) + 1}.{signature[1]}",
            "evidence_type": "completion",
            "sha256": digest or "",
        }
    )
    state.state["photos"] = photos
    _save_state(state)
    send_text(
        contact.wa_id,
        f"📷 Added completion photo {len(photos)}. Send another photo or reply *done*.",
    )


def _caretaker_complete_ticket(contact, state):
    draft = state.state
    photos = draft.get("photos", [])
    if not photos:
        send_text(contact.wa_id, "At least one completion photo is required.")
        return
    try:
        repair_cost = Decimal(draft.get("repair_cost", ""))
    except (InvalidOperation, TypeError):
        send_text(contact.wa_id, "Enter a valid non-negative repair cost.")
        return
    if (
        not repair_cost.is_finite()
        or repair_cost < 0
        or repair_cost > Decimal("9999999999.99")
        or repair_cost != repair_cost.quantize(Decimal("0.01"))
    ):
        send_text(contact.wa_id, "Enter a repair cost from 0 to 9,999,999,999.99 with at most 2 decimals.")
        return
    fundi_name = draft.get("fundi_name", "").strip()
    if not fundi_name or len(fundi_name) > 255:
        send_text(contact.wa_id, "Enter a valid technician/fundi name before completing the ticket.")
        return
    if not contact.user_id or not has_role(contact.user, CARETAKER_ROLE):
        send_text(contact.wa_id, "Your account does not have caretaker access.")
        return
    try:
        with transaction.atomic():
            ticket = (
                _caretaker_tickets(contact)
                .select_for_update(of=("self",))
                .filter(
                    pk=draft.get("ticket_id"),
                    assigned_to=contact.user,
                    status=MaintenanceTicket.STATUS_IN_PROGRESS,
                )
                .first()
            )
            if ticket is None:
                send_text(contact.wa_id, "That ticket is no longer assigned to you or in progress.")
                return
            if not isinstance(ticket.photos, list):
                raise ValidationError({"photos": "Ticket photos must be a list."})
            if len(ticket.photos) + len(photos) > 10:
                send_text(contact.wa_id, "This ticket has reached the 10-photo limit.")
                return
            old_status = ticket.status
            ticket.photos = [
                *ticket.photos,
                *[
                    {key: value for key, value in photo.items() if key != "sha256"}
                    for photo in photos
                ],
            ]
            ticket.resolution_fundi_name = fundi_name
            ticket.repair_cost = repair_cost
            ticket.transition_to(MaintenanceTicket.STATUS_COMPLETED)
            ticket.full_clean()
            ticket.save()
            AuditLog.objects.create(
                user=contact.user,
                action="maintenance_ticket.status_changed",
                entity="MaintenanceTicket",
                old_values={"status": old_status},
                new_values={
                    "ticket_id": ticket.pk,
                    "status": ticket.status,
                    "resolution_fundi_name": ticket.resolution_fundi_name,
                    "repair_cost": str(ticket.repair_cost),
                    "completion_photo_count": len(photos),
                    "source": "whatsapp",
                },
            )
            _notify_caretaker_ticket_completion(contact, ticket)
    except ValidationError:
        logger.exception("Caretaker completion data failed ticket validation.")
        send_text(contact.wa_id, "The completion details were rejected. Review them and try again.")
        return
    except (ImproperlyConfigured, ValueError):
        logger.exception("Caretaker completion notification could not be queued.")
        send_text(
            contact.wa_id,
            "The ticket was not completed because required notifications could not be queued. "
            "Please contact the office.",
        )
        return
    except DatabaseError:
        logger.exception("Caretaker ticket completion could not be saved.")
        send_text(contact.wa_id, "The ticket could not be completed. Please try again.")
        return

    state.state = {"flow": "menu"}
    _save_state(state)
    send_text(contact.wa_id, f"✅ Ticket #{ticket.pk} completed with {len(photos)} photo(s).")
    _show_menu(contact, state)


def _notify_caretaker_ticket_completion(contact, ticket):
    from django.db.models import Q

    from users.sms import queue_template_sms
    from users.views import (
        BRANCH_MANAGER_ROLE,
        SUPER_ADMIN_ROLE,
        _queue_property_staff_sms,
    )
    from users.whatsapp import queue_template_whatsapp_if_enabled

    today = timezone.localdate()
    tenancy = None
    if ticket.unit_id:
        tenancy = (
            Tenancy.objects.filter(
                unit_id=ticket.unit_id,
                status=Tenancy.STATUS_ACTIVE,
                start_date__lte=today,
            )
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
            .select_related("tenant")
            .first()
        )
    context = {
        "property_name": ticket.property.name,
        "unit_label": f"Unit {ticket.unit.code}" if ticket.unit_id else "Common area",
        "title": ticket.title,
        "fundi_name": ticket.resolution_fundi_name or "Not recorded",
        "repair_cost": (
            str(ticket.repair_cost) if ticket.repair_cost is not None else "Not recorded"
        ),
    }
    notified_staff = _queue_property_staff_sms(
        ticket.property,
        (BRANCH_MANAGER_ROLE, SUPER_ADMIN_ROLE, "Property Officer / Agent"),
        "maintenance_ticket_completed_staff",
        context,
        contact.user,
        None,
    )
    if not notified_staff:
        raise ValueError("No scoped property staff member is available for notification.")
    if tenancy:
        if not tenancy.tenant.phone:
            raise ValueError(f"Tenant {tenancy.tenant_id} has no phone number for notification.")
        queue_template_whatsapp_if_enabled(
            tenancy.tenant.phone,
            "maintenance_ticket_completed_tenant",
            context,
            actor=contact.user,
        )
        queue_template_sms(
            tenancy.tenant.phone,
            "maintenance_ticket_completed_tenant",
            context,
            actor=contact.user,
        )


def _caretaker_start_comment(contact, state, ticket_id):
    ticket = _caretaker_tickets(contact).filter(pk=ticket_id).first()
    if ticket is None:
        send_text(contact.wa_id, "That ticket is not available in your assigned properties.")
        return
    state.state = {
        "flow": "caretaker_comment",
        "step": "body",
        "ticket_id": ticket.pk,
    }
    _save_state(state)
    recent_comments = list(ticket.comments.select_related("author").order_by("-created_at")[:3])
    if recent_comments:
        updates = "\n".join(
            f"• {comment.author.get_full_name() or comment.author.username}: {comment.body[:120]}"
            for comment in reversed(recent_comments)
        )
        send_text(contact.wa_id, f"Recent updates:\n{updates}\n\nReply with your update.")
    else:
        send_text(contact.wa_id, "Reply with your update for the ticket.")


def _caretaker_add_comment(contact, state, body):
    body = body.strip()
    if not body or len(body) > 5000:
        send_text(contact.wa_id, "Enter an update of 1 to 5,000 characters.")
        return
    if not contact.user_id or not has_role(contact.user, CARETAKER_ROLE):
        send_text(contact.wa_id, "Your account does not have caretaker access.")
        return
    ticket = _caretaker_tickets(contact).filter(pk=state.state.get("ticket_id")).first()
    if ticket is None:
        send_text(contact.wa_id, "That ticket is no longer available in your assigned properties.")
        return
    comment = MaintenanceTicketComment(ticket=ticket, author=contact.user, body=body)
    try:
        with transaction.atomic():
            comment.full_clean()
            comment.save()
            AuditLog.objects.create(
                user=contact.user,
                action="maintenance_ticket.comment_added",
                entity="MaintenanceTicketComment",
                new_values={
                    "ticket_id": ticket.pk,
                    "comment_id": comment.pk,
                    "source": "whatsapp",
                },
            )
    except (ValidationError, DatabaseError):
        logger.exception("Caretaker ticket update could not be saved.")
        send_text(contact.wa_id, "Your update could not be saved. Please try again.")
        return
    state.state = {"flow": "menu"}
    _save_state(state)
    send_text(contact.wa_id, f"✅ Your update was added to ticket #{ticket.pk}.")
    _caretaker_ticket_detail(contact, state, ticket.pk)


def _caretaker_property_page(contact, state, offset=0):
    properties = _caretaker_properties(contact).order_by("name", "pk")
    total = properties.count()
    page = list(properties[offset : offset + 10])
    if not page:
        send_text(contact.wa_id, "No active properties are assigned to your caretaker account.")
        return
    has_more = total > offset + len(page)
    if has_more:
        page = page[:9]
    rows = [
        (f"c_property_{property_obj.pk}", property_obj.name[:24], "Report an on-site issue")
        for property_obj in page
    ]
    if has_more:
        rows.append((f"c_property_page_{offset + len(page)}", "More properties", "Show the next properties"))
    state.state = {
        "flow": "caretaker_create",
        "step": "property",
        "property_offset": offset,
    }
    _save_state(state)
    send_list(contact.wa_id, "Choose the property for this issue.", "Properties", rows)


def _caretaker_prompt_category(contact, state, property_id):
    state.state = {
        "flow": "caretaker_create",
        "step": "category",
        "property_id": property_id,
    }
    _save_state(state)
    send_list(
        contact.wa_id,
        "Choose the issue category.",
        "Categories",
        [
            (row[0], row[0].removeprefix("cat_").title(), "")
            for row in CATEGORY_ROWS
        ],
    )


def _caretaker_start_report(contact, state):
    properties = _caretaker_properties(contact).order_by("name", "pk")
    if not properties.exists():
        send_text(contact.wa_id, "No active properties are assigned to your caretaker account.")
        return
    if properties.count() == 1:
        _caretaker_prompt_category(contact, state, properties.values_list("pk", flat=True).first())
    else:
        _caretaker_property_page(contact, state)


def _caretaker_create_ticket(contact, state):
    if not contact.user_id or not has_role(contact.user, CARETAKER_ROLE):
        send_text(contact.wa_id, "Your account does not have caretaker access.")
        return
    draft = state.state
    property_obj = _caretaker_properties(contact).filter(pk=draft.get("property_id")).first()
    if property_obj is None:
        send_text(contact.wa_id, "That property is no longer assigned to your account.")
        return
    ticket = MaintenanceTicket(
        property=property_obj,
        reported_by=contact.user,
        title=draft.get("title", "").strip(),
        description=draft.get("description", "").strip(),
        category=draft.get("category", ""),
        priority=draft.get("priority", ""),
    )
    try:
        with transaction.atomic():
            ticket.full_clean()
            ticket.save()
            AuditLog.objects.create(
                user=contact.user,
                action="maintenance_ticket.created",
                entity="MaintenanceTicket",
                new_values={
                    "ticket_id": ticket.pk,
                    "property_id": property_obj.pk,
                    "source": "whatsapp",
                },
            )
    except (ValidationError, DatabaseError):
        logger.exception("Caretaker on-site ticket could not be created.")
        send_text(contact.wa_id, "The issue could not be logged. Please try again.")
        return
    state.state = {"flow": "menu"}
    _save_state(state)
    send_text(contact.wa_id, f"✅ Issue logged as ticket #{ticket.pk}.")
    _caretaker_ticket_detail(contact, state, ticket.pk)


def _caretaker_move_in_handovers(contact):
    return (
        MoveInPackage.objects.filter(
            handover_status=MoveInPackage.HANDOVER_STATUS_PENDING,
            tenancy__status=Tenancy.STATUS_ACTIVE,
            tenancy__unit__property__in=_caretaker_properties(contact),
        )
        .filter(
            Q(assigned_caretaker=contact.user)
            | Q(assigned_caretaker__isnull=True)
        )
        .select_related("tenancy__tenant", "tenancy__unit__property")
        .order_by("tenancy__start_date", "tenancy__unit__code", "pk")
    )


def _caretaker_move_out_inspections(contact):
    return (
        PropertyInspection.objects.filter(
            property__in=_caretaker_properties(contact),
            responsible_staff=contact.user,
            status__in=(
                PropertyInspection.STATUS_SCHEDULED,
                PropertyInspection.STATUS_IN_PROGRESS,
            ),
            move_out_notice__status=MoveOutNotice.STATUS_ACCEPTED,
            move_out_notice__tenancy__status=Tenancy.STATUS_ACTIVE,
        )
        .filter(
            Q(inspection_type__iexact="move_out")
            | Q(inspection_type__iexact="exit")
        )
        .select_related("property", "unit", "move_out_notice__tenancy__tenant")
        .order_by("scheduled_for", "pk")
    )


def _caretaker_routine_inspections(contact):
    inspections = (
        PropertyInspection.objects.filter(
            property__in=_caretaker_properties(contact),
            responsible_staff=contact.user,
            status__in=(
                PropertyInspection.STATUS_SCHEDULED,
                PropertyInspection.STATUS_IN_PROGRESS,
            ),
        )
        .select_related("property", "unit")
        .order_by("scheduled_for", "pk")
    )
    return [
        inspection
        for inspection in inspections
        if inspection.inspection_type.lower() not in {"move_out", "exit"}
    ]


def _caretaker_checklist_menu(contact, state):
    state.state = {"flow": "caretaker_checklists"}
    _save_state(state)
    send_list(
        contact.wa_id,
        "📋 Choose a checklist or inspection task.",
        "Field checklists",
        [
            ("c_handovers", "Move-in handovers", "Record keys, condition, readings and photos"),
            (
                "c_move_out_inspections",
                "Move-out inspections",
                "Record final readings, condition and damage",
            ),
            ("c_routine_inspections", "Routine inspections", "Complete assigned inspection checklists"),
            ("c_checklist_back", "Back to menu", "Return to caretaker services"),
        ],
    )


def _caretaker_handover_list(contact, state, offset=0):
    state.state = {"flow": "menu"}
    _save_state(state)
    query = _caretaker_move_in_handovers(contact)
    total = query.count()
    packages = list(query[offset : offset + 10])
    if not packages:
        send_text(contact.wa_id, "There are no pending move-in handovers for your properties.")
        return
    has_more = total > offset + len(packages)
    if has_more:
        packages = packages[:9]
    rows = [
        (
            f"c_handover_{package.pk}",
            f"{package.tenancy.unit.property.name} · {package.tenancy.unit.code}"[:24],
            f"Tenant: {package.tenancy.tenant.name}"[:72],
        )
        for package in packages
    ]
    if has_more:
        rows.append(
            (
                f"c_handover_page_{offset + len(packages)}",
                "More handovers",
                "Show the next pending handovers",
            )
        )
    send_list(
        contact.wa_id,
        "Pending move-in handovers — choose a unit:",
        "Move-in handovers",
        rows,
    )


def _caretaker_routine_inspection_list(contact, state, offset=0):
    state.state = {"flow": "menu"}
    _save_state(state)
    inspections = _caretaker_routine_inspections(contact)
    page = inspections[offset : offset + 10]
    if not page:
        send_text(contact.wa_id, "There are no assigned routine inspections awaiting completion.")
        return
    has_more = len(inspections) > offset + len(page)
    if has_more:
        page = page[:9]
    rows = [
        (
            f"c_routine_{inspection.pk}",
                inspection.title[:24],
                (
                    f"{inspection.property.name}"
                    f"{f' · {inspection.unit.code}' if inspection.unit_id else ''}"
                )[:72],
        )
        for inspection in page
    ]
    if has_more:
        rows.append(
            (
                f"c_routine_page_{offset + len(page)}",
                "More inspections",
                "Show the next assigned inspections",
            )
        )
    send_list(
        contact.wa_id,
        "Assigned routine inspections — choose one:",
        "Routine inspections",
        rows,
    )


def _caretaker_handover_for_contact(contact, package_id):
    return _caretaker_move_in_handovers(contact).filter(pk=package_id).first()


def _caretaker_start_handover(contact, state, package_id):
    package = _caretaker_handover_for_contact(contact, package_id)
    if package is None:
        send_text(contact.wa_id, "That move-in handover is no longer available.")
        return
    if timezone.localdate() < package.tenancy.start_date:
        send_text(
            contact.wa_id,
            f"This inspection is scheduled for {package.tenancy.start_date.isoformat()}. "
            "Please complete it on the move-in date.",
        )
        return
    from users.views import _move_in_condition_requirements, _move_in_meter_requirements

    conditions = _move_in_condition_requirements(package.tenancy.unit)
    meter_requirements = _move_in_meter_requirements(package)
    unconfigured = [item["name"] for item in meter_requirements if not item["configured"]]
    if unconfigured:
        send_text(
            contact.wa_id,
            "A Property Officer must configure the meter identifier for "
            f"{', '.join(unconfigured)} before this handover can be submitted.",
        )
        return
    state.state = {
        "flow": "caretaker_handover",
        "step": "keys",
        "package_id": package.pk,
        "keys_issued": None,
        "handover_inventory": (
            [
                dict(item) if isinstance(item, dict) else item
                for item in package.handover_inventory
            ]
            if isinstance(package.handover_inventory, list)
            else []
        ),
        "inventory_index": 0,
        "condition_requirements": conditions,
        "conditions": [],
        "condition_index": 0,
        "meter_requirements": meter_requirements,
        "readings": {},
        "reading_index": 0,
        "photo_hashes": [],
    }
    _save_state(state)
    send_text(
        contact.wa_id,
        f"Starting the move-in handover for {package.tenancy.unit.property.name}, "
        f"unit {package.tenancy.unit.code} ({package.tenancy.tenant.name}).\n"
        "How many keys and access devices were issued? Enter a whole number from 0 to 32,767.",
    )


def _caretaker_prompt_handover_inventory(contact, state):
    draft = state.state
    inventory = draft.get("handover_inventory", [])
    index = draft.get("inventory_index", 0)
    if index >= len(inventory):
        draft["condition_index"] = 0
        _save_state(state)
        _caretaker_prompt_handover_condition(contact, state)
        return
    item = inventory[index]
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("name"), str)
        or isinstance(item.get("property_quantity"), bool)
        or not isinstance(item.get("property_quantity"), int)
    ):
        send_text(
            contact.wa_id,
            "This handover's inventory is not configured correctly. Please contact the Property Officer.",
        )
        return
    draft["step"] = "inventory"
    _save_state(state)
    send_text(
        contact.wa_id,
        f"Item {index + 1} of {len(inventory)}: {item['name']}. "
        f"Expected quantity: {item['property_quantity']}. "
        "How many were issued? Enter a whole number from 0 to 32,767.",
    )


def _caretaker_prompt_handover_condition(contact, state):
    draft = state.state
    requirements = draft.get("condition_requirements", [])
    index = draft.get("condition_index", 0)
    if index >= len(requirements):
        _caretaker_prompt_handover_reading(contact, state)
        return
    condition = requirements[index]
    draft["step"] = "condition"
    _save_state(state)
    send_buttons(
        contact.wa_id,
        f"Condition {index + 1} of {len(requirements)}: "
        f"{condition['room']} — {condition['area']}",
        [
            ("ch_condition_good", "Good"),
            ("ch_condition_attention", "Needs attention"),
            ("ch_condition_na", "Not applicable"),
        ],
    )


def _caretaker_prompt_handover_reading(contact, state):
    draft = state.state
    requirements = draft.get("meter_requirements", [])
    index = draft.get("reading_index", 0)
    if index >= len(requirements):
        draft["step"] = "photos"
        _save_state(state)
        package = _caretaker_handover_for_contact(contact, draft.get("package_id"))
        photos_count = len(package.handover_photos) if package else 0
        has_water_meter = any(
            requirement["utility_type"] == UtilityConfiguration.UTILITY_WATER
            for requirement in requirements
        )
        has_water_meter_photo = bool(package) and any(
            isinstance(photo, dict) and photo.get("evidence_type") == "water_meter"
            for photo in package.handover_photos
        )
        if has_water_meter and not has_water_meter_photo:
            prompt = (
                "First send a clear photo of the water meter label and its reading. "
                "After that, send any condition photos. "
            )
        else:
            prompt = "Send at least one move-in condition or meter photo. "
        send_text(
            contact.wa_id,
            f"{prompt}{photos_count} photo(s) are already attached. "
            "Send more photos, then reply *done* (up to 10 total).",
        )
        return
    requirement = requirements[index]
    draft["step"] = "reading"
    _save_state(state)
    meter_label = f" · meter {requirement['meter']}" if requirement.get("meter") else ""
    send_text(
        contact.wa_id,
        f"Opening reading {index + 1} of {len(requirements)} for "
        f"{requirement['name']}{meter_label}. Enter a non-negative value "
        "with at most 4 decimal places.",
    )


def _caretaker_add_handover_condition(contact, state, status):
    draft = state.state
    index = draft.get("condition_index", 0)
    requirements = draft.get("condition_requirements", [])
    if index >= len(requirements):
        _caretaker_prompt_handover_reading(contact, state)
        return
    condition = requirements[index]
    if status == "needs_attention":
        draft["pending_condition"] = condition
        draft["step"] = "condition_note"
        _save_state(state)
        send_text(
            contact.wa_id,
            f"Describe the issue with {condition['room']} — {condition['area']} "
            "(1 to 2,000 characters).",
        )
        return
    draft.setdefault("conditions", []).append(
        {**condition, "status": status, "notes": ""}
    )
    draft["condition_index"] = index + 1
    state.state = draft
    _save_state(state)
    _caretaker_prompt_handover_condition(contact, state)


def _caretaker_store_handover_photo(contact, state, image):
    media_id = image.get("id")
    if not media_id:
        send_text(contact.wa_id, "I couldn't retrieve that photo. Please try sending it again.")
        return
    digest = image.get("sha256", "")
    if digest and digest in state.state.get("photo_hashes", []):
        send_text(contact.wa_id, "That photo has already been added.")
        return
    package = _caretaker_handover_for_contact(contact, state.state.get("package_id"))
    if package is None:
        send_text(contact.wa_id, "That move-in handover is no longer available.")
        return
    photos = package.handover_photos if isinstance(package.handover_photos, list) else []
    if len(photos) >= MAX_HANDOVER_PHOTOS:
        send_text(contact.wa_id, "This handover already has the maximum of 10 photos.")
        return
    from users.views import _move_in_meter_requirements

    requires_water_meter_photo = any(
        requirement["utility_type"] == UtilityConfiguration.UTILITY_WATER
        for requirement in _move_in_meter_requirements(package)
    )
    already_has_water_meter_photo = any(
        isinstance(photo, dict) and photo.get("evidence_type") == "water_meter"
        for photo in photos
    )
    evidence_type = (
        "water_meter"
        if requires_water_meter_photo and not already_has_water_meter_photo
        else "condition"
    )
    try:
        content, _ = _fetch_media(media_id)
    except (HTTPError, OSError, TypeError, ValueError):
        logger.exception("Caretaker handover photo could not be downloaded.")
        send_text(contact.wa_id, "I couldn't retrieve that photo. Please try sending it again.")
        return
    signatures = (
        (b"\xff\xd8\xff", "image/jpeg", "jpg"),
        (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
        (b"RIFF", "image/webp", "webp"),
    )
    match = next(
        (
            (content_type, extension)
            for signature, content_type, extension in signatures
            if content.startswith(signature)
            and (content_type != "image/webp" or content[8:12] == b"WEBP")
        ),
        None,
    )
    if match is None or not content or len(content) > MAX_PHOTO_BYTES:
        send_text(contact.wa_id, "Send a valid JPEG, PNG, or WebP photo no larger than 5 MB.")
        return
    content_type, extension = match
    key = f"move-in/{package.pk}/{uuid.uuid4().hex}.{extension}"
    try:
        upload_private_object(key, content, content_type)
    except (BotoCoreError, ImproperlyConfigured, OSError, ValueError):
        logger.exception("Caretaker handover photo could not be stored.")
        send_text(contact.wa_id, "That photo could not be saved. Please try again.")
        return

    try:
        with transaction.atomic():
            locked_package = (
                _caretaker_move_in_handovers(contact)
                .select_for_update(of=("self",))
                .filter(pk=package.pk)
                .first()
            )
            if locked_package is None:
                _cleanup_handover_photo(key)
                send_text(contact.wa_id, "That move-in handover is no longer editable.")
                return
            if not isinstance(locked_package.handover_photos, list):
                raise ValidationError({"handover_photos": "Handover photos must be a list."})
            if len(locked_package.handover_photos) >= MAX_HANDOVER_PHOTOS:
                _cleanup_handover_photo(key)
                send_text(contact.wa_id, "The handover photo limit has been reached.")
                return
            locked_package.handover_photos.append(
                {
                    "storage_key": key,
                    "evidence_type": evidence_type,
                    "content_type": content_type,
                    "size_bytes": len(content),
                    "name": f"Handover photo {len(locked_package.handover_photos) + 1}.{extension}",
                }
            )
            locked_package.save(update_fields=["handover_photos", "updated_at"])
            AuditLog.objects.create(
                user=contact.user,
                action="move_in_handover.photos_uploaded",
                entity="MoveInPackage",
                new_values={
                    "id": locked_package.pk,
                    "photo_count": len(locked_package.handover_photos),
                    "source": "whatsapp",
                },
            )
    except (BotoCoreError, DatabaseError, ImproperlyConfigured, OSError, ValidationError):
        _cleanup_handover_photo(key)
        logger.exception("Caretaker handover photo could not be attached.")
        send_text(contact.wa_id, "That photo could not be attached. Please try again.")
        return
    if digest:
        state.state.setdefault("photo_hashes", []).append(digest)
    _save_state(state)
    send_text(
        contact.wa_id,
        f"📷 Handover photo added ({len(locked_package.handover_photos)} total).",
    )


def _cleanup_handover_photo(key):
    try:
        delete_private_object(key)
    except (BotoCoreError, ImproperlyConfigured, OSError):
        logger.exception("Could not remove incomplete handover photo object %s.", key)


def _caretaker_finish_handover(contact, state):
    draft = state.state
    package = _caretaker_handover_for_contact(contact, draft.get("package_id"))
    if package is None:
        send_text(contact.wa_id, "That move-in handover is no longer available.")
        return
    if not package.handover_photos:
        send_text(contact.wa_id, "At least one handover photo is required before submission.")
        return
    inventory = draft.get("handover_inventory", [])
    if (
        not isinstance(inventory, list)
        or len(inventory) != len(package.handover_inventory)
        or any(
            not isinstance(item, dict)
            or isinstance(item.get("issued_quantity"), bool)
            or not isinstance(item.get("issued_quantity"), int)
            or not 0 <= item["issued_quantity"] <= 32767
            for item in inventory
        )
    ):
        send_text(contact.wa_id, "Record an issued quantity for every configured handover item.")
        return
    from users.views import _move_in_handover_photo_error

    photo_error = _move_in_handover_photo_error(package)
    if photo_error:
        send_text(contact.wa_id, photo_error)
        return
    if (
        len(draft.get("conditions", [])) != len(draft.get("condition_requirements", []))
        or len(draft.get("readings", {})) != len(draft.get("meter_requirements", []))
    ):
        send_text(contact.wa_id, "Complete all condition entries and opening readings before submitting.")
        return
    draft["step"] = "confirm"
    _save_state(state)
    send_buttons(
        contact.wa_id,
        f"Review move-in handover for {package.tenancy.unit.property.name}, "
        f"unit {package.tenancy.unit.code}\n"
        f"Keys/access devices: {draft['keys_issued']}\n"
        f"Inventory items recorded: {len(inventory)}\n"
        f"Condition entries: {len(draft['conditions'])}\n"
        f"Opening readings: {len(draft['readings'])}\n"
        f"Photos: {len(package.handover_photos)}",
        [("ch_handover_submit", "Submit handover"), ("ch_handover_cancel", "Cancel")],
    )


def _caretaker_submit_handover(contact, state):
    from users.views import (
        MOVE_IN_CONDITION_STATUSES,
        MOVE_IN_METER_TYPES,
        _move_in_condition_requirements,
        _move_in_handover_photo_error,
        _move_in_meter_requirements,
        _validated_move_in_handover_inventory,
    )

    draft = state.state
    if not contact.user_id or not has_role(contact.user, CARETAKER_ROLE):
        send_text(contact.wa_id, "Your account does not have caretaker access.")
        return
    try:
        with transaction.atomic():
            package = (
                _caretaker_move_in_handovers(contact)
                .select_for_update(of=("self",))
                .filter(pk=draft.get("package_id"))
                .first()
            )
            if package is None:
                send_text(contact.wa_id, "That handover is no longer pending or assigned to you.")
                return
            if timezone.localdate() < package.tenancy.start_date:
                send_text(
                    contact.wa_id,
                    f"This inspection is scheduled for {package.tenancy.start_date.isoformat()}. "
                    "Please complete it on the move-in date.",
                )
                return
            keys_issued = draft.get("keys_issued")
            if isinstance(keys_issued, bool) or not isinstance(keys_issued, int) or not 0 <= keys_issued <= 32767:
                send_text(contact.wa_id, "The key count must be a whole number from 0 to 32,767.")
                return
            try:
                normalized_inventory = _validated_move_in_handover_inventory(
                    package,
                    draft.get("handover_inventory"),
                )
            except ValueError as exc:
                send_text(contact.wa_id, str(exc))
                return
            required_conditions = _move_in_condition_requirements(package.tenancy.unit)
            raw_conditions = draft.get("conditions")
            if not isinstance(raw_conditions, list) or len(raw_conditions) != len(required_conditions):
                send_text(contact.wa_id, "Complete every room and fixture condition before submitting.")
                return
            normalized_conditions = []
            seen_conditions = set()
            required_keys = {(item["room"], item["area"]) for item in required_conditions}
            for item in raw_conditions:
                if not isinstance(item, dict):
                    raise ValueError("A condition entry is invalid.")
                room, area = item.get("room"), item.get("area")
                status, notes = item.get("status"), item.get("notes", "")
                condition_key = (room, area)
                if (
                    not isinstance(room, str)
                    or not isinstance(area, str)
                    or condition_key not in required_keys
                    or condition_key in seen_conditions
                    or not isinstance(status, str)
                    or status not in MOVE_IN_CONDITION_STATUSES
                    or not isinstance(notes, str)
                    or len(notes) > 2000
                    or (status == "needs_attention" and not notes.strip())
                ):
                    raise ValueError("Every condition must have a supported status and required notes.")
                seen_conditions.add(condition_key)
                normalized_conditions.append(
                    {"room": room, "area": area, "status": status, "notes": notes.strip()}
                )
            if seen_conditions != required_keys:
                send_text(contact.wa_id, "Complete every room and fixture condition before submitting.")
                return

            requirements = _move_in_meter_requirements(package)
            if any(not item["configured"] for item in requirements):
                raise ValueError("A Property Officer must configure missing meter identifiers first.")
            readings = draft.get("readings")
            required_types = {item["utility_type"] for item in requirements}
            if not isinstance(readings, dict) or set(readings) != required_types:
                send_text(contact.wa_id, "Submit exactly one opening reading for every metered utility.")
                return
            normalized_readings = []
            requirements_by_type = {item["utility_type"]: item for item in requirements}
            for utility_type, value in readings.items():
                if utility_type not in MOVE_IN_METER_TYPES:
                    raise ValueError("An opening meter type is invalid.")
                try:
                    decimal_value = Decimal(str(value))
                except (InvalidOperation, TypeError, ValueError) as exc:
                    raise ValueError("Opening readings must be valid numbers.") from exc
                if (
                    not decimal_value.is_finite()
                    or decimal_value < 0
                    or decimal_value.as_tuple().exponent < -4
                    or decimal_value.adjusted() >= 10
                ):
                    raise ValueError("Opening readings must be non-negative with at most 4 decimals.")
                requirement = requirements_by_type[utility_type]
                normalized_readings.append(
                    {
                        "utility_type": utility_type,
                        "current_value": format(decimal_value, "f"),
                        "name": requirement["name"],
                        "meter": requirement["meter"],
                    }
                )
            photo_error = _move_in_handover_photo_error(package)
            if photo_error:
                send_text(contact.wa_id, photo_error)
                return

            tenancy = Tenancy.objects.select_for_update().select_related(
                "tenant", "unit__property"
            ).get(pk=package.tenancy_id)
            package.keys_issued = keys_issued
            package.handover_inventory = normalized_inventory
            package.condition_checklist = normalized_conditions
            package.initial_meter_readings = normalized_readings
            package.handover_status = MoveInPackage.HANDOVER_STATUS_SUBMITTED
            package.handover_submitted_at = timezone.now()
            package.handover_submitted_by = contact.user
            package.save(
                update_fields=[
                    "keys_issued",
                    "handover_inventory",
                    "condition_checklist",
                    "initial_meter_readings",
                    "handover_status",
                    "handover_submitted_at",
                    "handover_submitted_by",
                    "updated_at",
                ]
            )
            checklist_notes = {
                "Record opening meter readings": json.dumps(normalized_readings),
                "Inspect and document unit condition": json.dumps(normalized_conditions),
                "Hand over keys and access devices": f"{keys_issued} key(s) issued.",
            }
            for title, notes in checklist_notes.items():
                item, _ = MoveInChecklistItem.objects.get_or_create(
                    package=package,
                    title=title,
                    defaults={
                        "status": MoveInChecklistItem.STATUS_COMPLETED,
                        "notes": notes,
                        "completed_at": package.handover_submitted_at,
                    },
                )
                if item.status != MoveInChecklistItem.STATUS_COMPLETED:
                    item.status = MoveInChecklistItem.STATUS_COMPLETED
                    item.notes = notes
                    item.completed_at = package.handover_submitted_at
                    item.save(update_fields=["status", "notes", "completed_at"])
            AuditLog.objects.create(
                user=contact.user,
                action="move_in_handover.submitted",
                entity="MoveInPackage",
                new_values={
                    "id": package.pk,
                    "tenancy_id": package.tenancy_id,
                    "keys_issued": keys_issued,
                    "handover_inventory": normalized_inventory,
                    "condition_checklist": normalized_conditions,
                    "initial_meter_readings": normalized_readings,
                    "photo_count": len(package.handover_photos),
                    "source": "whatsapp",
                },
            )
            from users.views import (
                BRANCH_MANAGER_ROLE,
                SUPER_ADMIN_ROLE,
                _queue_property_staff_sms,
            )

            _queue_property_staff_sms(
                tenancy.unit.property,
                (BRANCH_MANAGER_ROLE, SUPER_ADMIN_ROLE, "Property Officer / Agent"),
                "move_in_handover_submitted",
                {
                    "tenant_name": tenancy.tenant.name,
                    "property_name": tenancy.unit.property.name,
                    "unit_code": tenancy.unit.code,
                },
                contact.user,
                None,
            )
    except (ImproperlyConfigured, ValueError):
        logger.exception("Caretaker move-in handover could not be submitted.")
        send_text(
            contact.wa_id,
            "The handover was not submitted because its data or required notification could not be validated. "
            "Please review the details or contact the office.",
        )
        return
    except (DatabaseError, ValidationError):
        logger.exception("Caretaker move-in handover could not be saved.")
        send_text(contact.wa_id, "The handover could not be submitted. Please try again.")
        return
    state.state = {"flow": "menu"}
    _save_state(state)
    send_text(
        contact.wa_id,
        f"✅ Move-in handover for {tenancy.unit.property.name}, unit {tenancy.unit.code} "
        "was submitted for Property Officer review.",
    )
    _show_menu(contact, state)


def _caretaker_move_out_inspection_list(contact, state, offset=0):
    state.state = {"flow": "menu"}
    _save_state(state)
    if offset < 0:
        send_text(contact.wa_id, "That move-out inspection page is invalid.")
        return
    inspections = _caretaker_move_out_inspections(contact)
    page = list(inspections[offset : offset + 10])
    if not page:
        send_text(
            contact.wa_id,
            "There are no assigned move-out inspections awaiting completion.",
        )
        return
    has_more = inspections.count() > offset + len(page)
    if has_more:
        page = page[:9]
    rows = [
        (
            f"c_exit_{inspection.pk}",
            f"{inspection.property.name} · {inspection.unit.code}"[:24],
            (
                "Move-out: "
                f"{inspection.move_out_notice.proposed_move_out_date.isoformat()} · "
                f"{inspection.move_out_notice.tenancy.tenant.name}"
            )[:72],
        )
        for inspection in page
        if inspection.unit_id and inspection.move_out_notice_id
    ]
    if has_more:
        rows.append(
            (
                f"c_exit_page_{offset + len(page)}",
                "More inspections",
                "Show the next assigned move-out inspections",
            )
        )
    if not rows:
        send_text(
            contact.wa_id,
            "There are no valid assigned move-out inspections to complete.",
        )
        return
    send_list(
        contact.wa_id,
        "Assigned move-out inspections — choose a unit:",
        "Move-out inspections",
        rows,
    )


def _caretaker_move_out_inspection_for_contact(contact, inspection_id):
    return _caretaker_move_out_inspections(contact).filter(pk=inspection_id).first()


def _caretaker_start_move_out_inspection(contact, state, inspection_id):
    inspection = _caretaker_move_out_inspection_for_contact(contact, inspection_id)
    if (
        inspection is None
        or inspection.unit_id is None
        or inspection.move_out_notice_id is None
    ):
        send_text(
            contact.wa_id,
            "That assigned move-out inspection is no longer available.",
        )
        return
    proposed_date = inspection.move_out_notice.proposed_move_out_date
    if timezone.localdate() < proposed_date:
        send_text(
            contact.wa_id,
            f"This inspection is scheduled for {proposed_date.isoformat()}. "
            "Please complete it on or after that date.",
        )
        return
    checklist = inspection.checklist if isinstance(inspection.checklist, list) else []
    if not checklist:
        send_text(
            contact.wa_id,
            "This move-out inspection has no exit checklist. "
            "Please contact the Property Officer.",
        )
        return
    state.state = {
        "flow": "caretaker_exit_inspection",
        "step": "actual_date",
        "inspection_id": inspection.pk,
        "checklist": checklist,
        "checklist_index": 0,
        "final_readings": [],
        "reading_index": 0,
        "damage_items": [],
        "maintenance_required": False,
        "findings": "",
        "recommendations": "",
        "photo_hashes": [],
    }
    _save_state(state)
    send_text(
        contact.wa_id,
        f"Starting the move-out inspection for {inspection.property.name}, "
        f"unit {inspection.unit.code} "
        f"({inspection.move_out_notice.tenancy.tenant.name}).\n"
        f"Proposed move-out date: {proposed_date.isoformat()}.\n"
        "Enter the actual move-out date as YYYY-MM-DD.",
    )


def _caretaker_prompt_move_out_checklist(contact, state):
    checklist = state.state.get("checklist", [])
    index = state.state.get("checklist_index", 0)
    if index >= len(checklist):
        state.state["step"] = "final_reading"
        state.state["reading_index"] = 0
        _save_state(state)
        _caretaker_prompt_move_out_reading(contact, state)
        return
    entry = checklist[index]
    if not isinstance(entry, dict) or not isinstance(entry.get("title"), str):
        send_text(
            contact.wa_id,
            "This exit checklist is invalid. Please contact the Property Officer.",
        )
        return
    state.state["step"] = "checklist"
    _save_state(state)
    send_buttons(
        contact.wa_id,
        f"Exit checklist {index + 1} of {len(checklist)}:\n{entry['title']}",
        [
            (f"coi_complete_{index}", "Mark complete"),
            (f"coi_keep_{index}", "Already complete"),
        ],
    )


def _caretaker_prompt_move_out_reading(contact, state):
    requirements = state.state.get("meter_requirements", [])
    index = state.state.get("reading_index", 0)
    if index >= len(requirements):
        state.state["step"] = "damage_prompt"
        _save_state(state)
        send_buttons(
            contact.wa_id,
            "Record any damage found during the move-out inspection?",
            [("coi_damage_yes", "Add damage"), ("coi_damage_no", "No damage")],
        )
        return
    requirement = requirements[index]
    state.state["step"] = "final_reading"
    _save_state(state)
    meter_label = f" ({requirement['meter']})" if requirement.get("meter") else ""
    send_text(
        contact.wa_id,
        f"Enter the final {requirement['name']} meter reading{meter_label} "
        "(a non-negative number, up to 4 decimal places).",
    )


def _caretaker_prompt_move_out_damage(contact, state):
    state.state["step"] = "damage_item"
    _save_state(state)
    send_text(
        contact.wa_id,
        "Enter one damage item as *description | amount* "
        "(for example: Broken window | 600), "
        "or reply *skip* if there is no damage.",
    )


def _caretaker_prompt_move_out_photos(contact, state):
    state.state["step"] = "photos"
    _save_state(state)
    send_text(
        contact.wa_id,
        "Send optional move-out evidence photos, then reply *done* or *skip*. "
        f"You can attach up to {MAX_EXIT_INSPECTION_PHOTOS} photos.",
    )


def _caretaker_add_move_out_photo(contact, state, image):
    media_id = image.get("id")
    if not media_id:
        send_text(
            contact.wa_id,
            "I couldn't retrieve that photo. Please try sending it again.",
        )
        return
    digest = image.get("sha256", "")
    if digest and digest in state.state.get("photo_hashes", []):
        send_text(contact.wa_id, "That photo has already been added.")
        return
    inspection = _caretaker_move_out_inspection_for_contact(
        contact,
        state.state.get("inspection_id"),
    )
    if inspection is None:
        send_text(
            contact.wa_id,
            "That assigned move-out inspection is no longer available.",
        )
        return
    photos = inspection.photos if isinstance(inspection.photos, list) else []
    if len(photos) >= MAX_EXIT_INSPECTION_PHOTOS:
        send_text(
            contact.wa_id,
            "This inspection already has the maximum of 10 photos.",
        )
        return
    try:
        content, _ = _fetch_media(media_id)
    except (HTTPError, OSError, TypeError, ValueError):
        logger.exception("Caretaker move-out inspection photo could not be downloaded.")
        send_text(
            contact.wa_id,
            "I couldn't retrieve that photo. Please try sending it again.",
        )
        return
    signatures = (
        (b"\xff\xd8\xff", "image/jpeg", "jpg"),
        (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    )
    match = next(
        ((content_type, extension) for signature, content_type, extension in signatures
         if content.startswith(signature)),
        None,
    )
    if match is None or not content or len(content) > MAX_PHOTO_BYTES:
        send_text(contact.wa_id, "Send a valid JPEG or PNG photo no larger than 5 MB.")
        return
    content_type, extension = match
    key = f"inspections/move-out/{inspection.pk}/{uuid.uuid4().hex}.{extension}"
    try:
        upload_private_object(key, content, content_type)
    except (BotoCoreError, ImproperlyConfigured, OSError, ValueError):
        logger.exception("Caretaker move-out inspection photo could not be stored.")
        send_text(contact.wa_id, "That photo could not be saved. Please try again.")
        return

    try:
        with transaction.atomic():
            locked = (
                _caretaker_move_out_inspections(contact)
                .select_for_update(of=("self",))
                .filter(pk=inspection.pk)
                .first()
            )
            if locked is None or locked.status == PropertyInspection.STATUS_COMPLETED:
                _cleanup_move_out_photo(key)
                send_text(
                    contact.wa_id,
                    "That move-out inspection is no longer editable.",
                )
                return
            if not isinstance(locked.photos, list):
                raise ValidationError({"photos": "Inspection photos must be a list."})
            if len(locked.photos) >= MAX_EXIT_INSPECTION_PHOTOS:
                _cleanup_move_out_photo(key)
                send_text(contact.wa_id, "The inspection photo limit has been reached.")
                return
            locked.photos.append(
                {
                    "storage_key": key,
                    "content_type": content_type,
                    "size_bytes": len(content),
                    "name": (
                        f"Exit inspection evidence {len(locked.photos) + 1}.{extension}"
                    ),
                }
            )
            locked.full_clean()
            locked.save(update_fields=["photos", "updated_at"])
            AuditLog.objects.create(
                user=contact.user,
                action="property_inspection.photos_added",
                entity="PropertyInspection",
                new_values={
                    "inspection_id": locked.pk,
                    "photo_count": len(locked.photos),
                    "source": "whatsapp",
                },
            )
    except (
        BotoCoreError,
        DatabaseError,
        ImproperlyConfigured,
        OSError,
        ValidationError,
    ):
        _cleanup_move_out_photo(key)
        logger.exception("Caretaker move-out inspection photo could not be attached.")
        send_text(contact.wa_id, "That photo could not be attached. Please try again.")
        return
    if digest:
        state.state.setdefault("photo_hashes", []).append(digest)
    _save_state(state)
    send_text(
        contact.wa_id,
        f"📷 Move-out evidence photo added ({len(locked.photos)} total).",
    )


def _cleanup_move_out_photo(key):
    try:
        delete_private_object(key)
    except (BotoCoreError, ImproperlyConfigured, OSError):
        logger.exception(
            "Could not remove incomplete move-out inspection photo object %s.",
            key,
        )


def _caretaker_finish_move_out_inspection(contact, state):
    draft = state.state
    checklist = draft.get("checklist", [])
    if not checklist or any(
        not isinstance(item, dict)
        or item.get("status") not in {"complete", "completed"}
        for item in checklist
    ):
        send_text(
            contact.wa_id,
            "Complete every exit checklist item before submitting.",
        )
        return
    inspection = _caretaker_move_out_inspection_for_contact(
        contact,
        draft.get("inspection_id"),
    )
    if inspection is None:
        send_text(
            contact.wa_id,
            "That assigned move-out inspection is no longer available.",
        )
        return
    draft["step"] = "confirm"
    _save_state(state)
    send_buttons(
        contact.wa_id,
        f"Submit move-out inspection: {inspection.title}\n"
        f"{inspection.property.name} · Unit {inspection.unit.code}\n"
        f"Actual move-out date: {draft.get('actual_move_out_date')}\n"
        f"Final readings: {len(draft.get('final_readings', []))}\n"
        f"Damage items: {len(draft.get('damage_items', []))}\n"
        f"Photos: {len(inspection.photos)}\n"
        f"Maintenance required: {'Yes' if draft.get('maintenance_required') else 'No'}",
        [("coi_submit", "Submit inspection"), ("coi_cancel", "Cancel")],
    )


def _caretaker_submit_move_out_inspection(contact, state):
    if not contact.user_id or not has_role(contact.user, CARETAKER_ROLE):
        send_text(contact.wa_id, "Your account does not have caretaker access.")
        return
    inspection = _caretaker_move_out_inspection_for_contact(
        contact,
        state.state.get("inspection_id"),
    )
    if inspection is None:
        send_text(
            contact.wa_id,
            "That assigned move-out inspection is no longer available.",
        )
        return
    payload = {
        "actual_move_out_date": state.state.get("actual_move_out_date"),
        "checklist": state.state.get("checklist", []),
        "final_readings": state.state.get("final_readings", []),
        "damage_items": state.state.get("damage_items", []),
        "maintenance_required": state.state.get("maintenance_required", False),
        "findings": state.state.get("findings", ""),
        "recommendations": state.state.get("recommendations", ""),
    }
    # Keep web and WhatsApp completion on the same validation, audit, and notification path.
    from django.test import RequestFactory

    from users.views import complete_inspection

    request = RequestFactory().post(
        f"/api/v1/inspections/{inspection.pk}/complete",
        data=json.dumps(payload),
        content_type="application/json",
    )
    request.user = contact.user
    request.audit_source = "whatsapp"
    response = complete_inspection(request, inspection.pk)
    result = json.loads(response.content.decode("utf-8"))
    if response.status_code != 200:
        detail = result.get("detail", "The inspection could not be submitted.")
        send_text(
            contact.wa_id,
            f"⚠️ {detail}\nThe task remains pending. Cancel and reopen it "
            "to correct entered details.",
        )
        return
    state.state = {"flow": "menu"}
    _save_state(state)
    send_text(
        contact.wa_id,
        f"✅ Move-out inspection for {inspection.property.name}, "
        f"unit {inspection.unit.code} "
        "was submitted to the property team.",
    )
    _show_menu(contact, state)


def _caretaker_start_routine_inspection(contact, state, inspection_id):
    inspection = next(
        (
            item
            for item in _caretaker_routine_inspections(contact)
            if item.pk == inspection_id
        ),
        None,
    )
    if inspection is None:
        send_text(contact.wa_id, "That assigned routine inspection is no longer available.")
        return
    checklist = inspection.checklist if isinstance(inspection.checklist, list) else []
    state.state = {
        "flow": "caretaker_routine",
        "step": "checklist",
        "inspection_id": inspection.pk,
        "checklist": checklist,
        "checklist_index": 0,
        "findings": inspection.findings,
        "recommendations": inspection.recommendations,
    }
    _save_state(state)
    _caretaker_prompt_routine_checklist(contact, state)


def _routine_checklist_item_complete(item):
    return (
        isinstance(item, dict)
        and isinstance(item.get("status"), str)
        and item.get("status") in {"complete", "completed"}
    )


def _caretaker_prompt_routine_checklist(contact, state):
    draft = state.state
    checklist = draft.get("checklist", [])
    index = draft.get("checklist_index", 0)
    if index >= len(checklist):
        if not checklist:
            draft["step"] = "findings"
            _save_state(state)
            send_text(
                contact.wa_id,
                "This inspection has no checklist items. Add your findings before submitting "
                "(up to 10,000 characters).",
            )
        else:
            draft["step"] = "findings"
            _save_state(state)
            send_text(
                contact.wa_id,
                "All checklist items are complete. Add findings (up to 10,000 characters), "
                "or reply *skip* if there are none.",
            )
        return
    entry = checklist[index]
    if not isinstance(entry, dict) or not isinstance(entry.get("title"), str):
        send_text(contact.wa_id, "This inspection has an invalid checklist. Please contact the office.")
        return
    draft["step"] = "checklist"
    _save_state(state)
    if _routine_checklist_item_complete(entry):
        buttons = [
            (f"cri_keep_{index}", "Keep complete"),
            (f"cri_pending_{index}", "Mark incomplete"),
        ]
    else:
        buttons = [
            (f"cri_complete_{index}", "Mark complete"),
            (f"cri_pending_{index}", "Leave incomplete"),
        ]
    send_buttons(
        contact.wa_id,
        f"Inspection checklist {index + 1} of {len(checklist)}:\n{entry['title']}",
        buttons,
    )


def _caretaker_finish_routine_inspection(contact, state):
    draft = state.state
    checklist = draft.get("checklist", [])
    if any(
        not _routine_checklist_item_complete(item)
        for item in checklist
    ):
        pending_index = next(
            index
            for index, item in enumerate(checklist)
            if not _routine_checklist_item_complete(item)
        )
        draft["checklist_index"] = pending_index
        _save_state(state)
        send_text(
            contact.wa_id,
            "Every routine inspection checklist item must be complete before submitting.",
        )
        _caretaker_prompt_routine_checklist(contact, state)
        return
    if not checklist and not draft.get("findings", "").strip():
        send_text(contact.wa_id, "Add findings before submitting an inspection that has no checklist items.")
        return
    inspection = next(
        (
            item
            for item in _caretaker_routine_inspections(contact)
            if item.pk == draft.get("inspection_id")
        ),
        None,
    )
    if inspection is None:
        send_text(contact.wa_id, "That assigned routine inspection is no longer available.")
        return
    draft["step"] = "confirm"
    _save_state(state)
    send_buttons(
        contact.wa_id,
        f"Submit inspection: {inspection.title}\n"
        f"{inspection.property.name}"
        f"{f' · Unit {inspection.unit.code}' if inspection.unit_id else ''}\n"
        f"Checklist items: {len(checklist)}\n"
        f"Findings: {'provided' if draft.get('findings', '').strip() else 'none'}\n"
        f"Recommendations: {'provided' if draft.get('recommendations', '').strip() else 'none'}",
        [("cri_submit", "Submit inspection"), ("cri_cancel", "Cancel")],
    )


def _caretaker_submit_routine_inspection(contact, state):
    draft = state.state
    if not contact.user_id or not has_role(contact.user, CARETAKER_ROLE):
        send_text(contact.wa_id, "Your account does not have caretaker access.")
        return
    raw_checklist = draft.get("checklist")
    findings = draft.get("findings", "")
    recommendations = draft.get("recommendations", "")
    if (
        not isinstance(raw_checklist, list)
        or not isinstance(findings, str)
        or len(findings) > 10000
        or not isinstance(recommendations, str)
        or len(recommendations) > 10000
        or (not raw_checklist and not findings.strip())
    ):
        send_text(contact.wa_id, "Add valid findings and complete all checklist items before submitting.")
        return
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("title"), str)
        or not isinstance(item.get("status"), str)
        or item.get("status") not in {"complete", "completed"}
        for item in raw_checklist
    ):
        send_text(contact.wa_id, "Complete every routine inspection checklist item before submitting.")
        return

    try:
        with transaction.atomic():
            inspection = (
                PropertyInspection.objects.select_for_update(of=("self",))
                .filter(
                    pk=draft.get("inspection_id"),
                    property__in=_caretaker_properties(contact),
                    responsible_staff=contact.user,
                    status__in=(
                        PropertyInspection.STATUS_SCHEDULED,
                        PropertyInspection.STATUS_IN_PROGRESS,
                    ),
                )
                .exclude(inspection_type__iexact="move_out")
                .exclude(inspection_type__iexact="exit")
                .select_related("property", "unit")
                .first()
            )
            if inspection is None:
                send_text(contact.wa_id, "That assigned routine inspection is no longer editable.")
                return
            if not isinstance(inspection.checklist, list) or len(raw_checklist) != len(inspection.checklist):
                send_text(contact.wa_id, "The checklist changed. Reopen the assigned inspection and try again.")
                return
            for original, updated in zip(inspection.checklist, raw_checklist):
                if (
                    not isinstance(original, dict)
                    or original.get("title") != updated.get("title")
                ):
                    send_text(contact.wa_id, "The checklist changed. Reopen the assigned inspection and try again.")
                    return
            old_status = inspection.status
            inspection.checklist = raw_checklist
            inspection.findings = findings.strip()
            inspection.recommendations = recommendations.strip()
            inspection.status = PropertyInspection.STATUS_COMPLETED
            inspection.completed_at = timezone.now()
            inspection.full_clean()
            inspection.save()
            AuditLog.objects.create(
                user=contact.user,
                action="property_inspection.completed",
                entity="PropertyInspection",
                old_values={"status": old_status},
                new_values={
                    "inspection_id": inspection.pk,
                    "status": inspection.status,
                    "checklist": inspection.checklist,
                    "findings": inspection.findings,
                    "recommendations": inspection.recommendations,
                    "source": "whatsapp",
                },
            )
    except (DatabaseError, ValidationError):
        logger.exception("Caretaker routine inspection could not be completed.")
        send_text(contact.wa_id, "The inspection could not be submitted. Please try again.")
        return
    state.state = {"flow": "menu"}
    _save_state(state)
    send_text(
        contact.wa_id,
        f"✅ Inspection “{inspection.title}” at {inspection.property.name} was completed.",
    )
    _show_menu(contact, state)


def _handle_caretaker_checklist_message(contact, state, message, choice, text, kind):
    if state.active_role != ROLE_CARETAKER:
        return False
    flow = state.state.get("flow", "")
    step = state.state.get("step", "")
    lowered = text.lower().strip()

    if choice == "c_checklists":
        _caretaker_checklist_menu(contact, state)
        return True
    if choice == "c_checklist_back":
        _show_menu(contact, state)
        return True
    if choice == "c_handovers":
        _caretaker_handover_list(contact, state)
        return True
    if choice == "c_move_out_inspections":
        _caretaker_move_out_inspection_list(contact, state)
        return True
    if choice == "c_routine_inspections":
        _caretaker_routine_inspection_list(contact, state)
        return True
    if choice.startswith("c_handover_page_"):
        try:
            offset = int(choice.removeprefix("c_handover_page_"))
        except ValueError:
            send_text(contact.wa_id, "That handover page is invalid.")
        else:
            _caretaker_handover_list(contact, state, offset)
        return True
    if choice.startswith("c_exit_page_"):
        try:
            offset = int(choice.removeprefix("c_exit_page_"))
        except ValueError:
            send_text(contact.wa_id, "That move-out inspection page is invalid.")
        else:
            _caretaker_move_out_inspection_list(contact, state, offset)
        return True
    if choice.startswith("c_exit_"):
        try:
            inspection_id = int(choice.removeprefix("c_exit_"))
        except ValueError:
            send_text(contact.wa_id, "That move-out inspection selection is invalid.")
        else:
            _caretaker_start_move_out_inspection(contact, state, inspection_id)
        return True
    if choice.startswith("c_handover_"):
        try:
            package_id = int(choice.removeprefix("c_handover_"))
        except ValueError:
            send_text(contact.wa_id, "That handover selection is invalid.")
        else:
            _caretaker_start_handover(contact, state, package_id)
        return True
    if choice.startswith("c_routine_page_"):
        try:
            offset = int(choice.removeprefix("c_routine_page_"))
        except ValueError:
            send_text(contact.wa_id, "That inspection page is invalid.")
        else:
            _caretaker_routine_inspection_list(contact, state, offset)
        return True
    if choice.startswith("c_routine_"):
        try:
            inspection_id = int(choice.removeprefix("c_routine_"))
        except ValueError:
            send_text(contact.wa_id, "That inspection selection is invalid.")
        else:
            _caretaker_start_routine_inspection(contact, state, inspection_id)
        return True

    if flow == "caretaker_checklists":
        if any(term in lowered for term in ("handover", "move in", "move-in")):
            _caretaker_handover_list(contact, state)
        elif any(term in lowered for term in ("move out", "move-out", "exit")):
            _caretaker_move_out_inspection_list(contact, state)
        elif "inspection" in lowered or "routine" in lowered:
            _caretaker_routine_inspection_list(contact, state)
        else:
            _caretaker_checklist_menu(contact, state)
        return True

    if flow == "caretaker_handover":
        if lowered in {"cancel", "stop"} or choice == "ch_handover_cancel":
            state.state = {"flow": "menu"}
            _save_state(state)
            send_text(contact.wa_id, "Move-in handover cancelled.")
            _caretaker_checklist_menu(contact, state)
            return True
        if step == "keys":
            value = text.strip()
            if (
                not value.isascii()
                or not value.isdigit()
                or len(value) > 5
                or int(value) > 32767
            ):
                send_text(contact.wa_id, "Enter a whole number from 0 to 32,767, or reply *cancel*.")
                return True
            state.state["keys_issued"] = int(value)
            state.state["inventory_index"] = 0
            _save_state(state)
            _caretaker_prompt_handover_inventory(contact, state)
            return True
        if step == "inventory":
            value = text.strip()
            if (
                not value.isascii()
                or not value.isdigit()
                or len(value) > 5
                or int(value) > 32767
            ):
                send_text(contact.wa_id, "Enter a whole number from 0 to 32,767, or reply *cancel*.")
                return True
            inventory = state.state.get("handover_inventory", [])
            index = state.state.get("inventory_index", 0)
            if (
                not isinstance(inventory, list)
                or not isinstance(index, int)
                or index < 0
                or index >= len(inventory)
                or not isinstance(inventory[index], dict)
            ):
                send_text(contact.wa_id, "That handover inventory step expired. Please reopen the checklist.")
                _caretaker_checklist_menu(contact, state)
                return True
            inventory[index]["issued_quantity"] = int(value)
            state.state["inventory_index"] = index + 1
            _save_state(state)
            _caretaker_prompt_handover_inventory(contact, state)
            return True
        if step == "condition":
            status_by_choice = {
                "ch_condition_good": "good",
                "ch_condition_attention": "needs_attention",
                "ch_condition_na": "not_applicable",
            }
            status = status_by_choice.get(choice)
            if status is None:
                status = {
                    "good": "good",
                    "needs attention": "needs_attention",
                    "not applicable": "not_applicable",
                }.get(lowered)
            if status is None:
                send_text(contact.wa_id, "Choose Good, Needs attention, or Not applicable.")
                return True
            _caretaker_add_handover_condition(contact, state, status)
            return True
        if step == "condition_note":
            note = text.strip()
            if not note or len(note) > 2000:
                send_text(contact.wa_id, "Enter a note of 1 to 2,000 characters.")
                return True
            condition = state.state.pop("pending_condition", None)
            if not isinstance(condition, dict):
                send_text(contact.wa_id, "That condition entry expired. Please restart the handover.")
                _caretaker_checklist_menu(contact, state)
                return True
            state.state.setdefault("conditions", []).append(
                {**condition, "status": "needs_attention", "notes": note}
            )
            state.state["condition_index"] = state.state.get("condition_index", 0) + 1
            _save_state(state)
            _caretaker_prompt_handover_condition(contact, state)
            return True
        if step == "reading":
            requirements = state.state.get("meter_requirements", [])
            index = state.state.get("reading_index", 0)
            if index >= len(requirements):
                _caretaker_prompt_handover_reading(contact, state)
                return True
            try:
                reading = Decimal(text.strip())
                if (
                    not reading.is_finite()
                    or reading < 0
                    or reading.as_tuple().exponent < -4
                    or reading.adjusted() >= 10
                ):
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                send_text(contact.wa_id, "Enter a non-negative reading with at most 4 decimal places.")
                return True
            utility_type = requirements[index]["utility_type"]
            state.state.setdefault("readings", {})[utility_type] = format(reading, "f")
            state.state["reading_index"] = index + 1
            _save_state(state)
            _caretaker_prompt_handover_reading(contact, state)
            return True
        if step == "photos":
            if kind == "image":
                _caretaker_store_handover_photo(contact, state, message.get("image", {}))
                return True
            if lowered == "done":
                _caretaker_finish_handover(contact, state)
                return True
            send_text(contact.wa_id, "Send a handover photo, reply *done* to review, or reply *cancel*.")
            return True
        if step == "confirm":
            if choice == "ch_handover_submit":
                _caretaker_submit_handover(contact, state)
            elif choice == "ch_handover_cancel":
                state.state = {"flow": "menu"}
                _save_state(state)
                send_text(contact.wa_id, "Move-in handover cancelled.")
                _caretaker_checklist_menu(contact, state)
            else:
                send_text(contact.wa_id, "Choose Submit handover or Cancel.")
            return True
        send_text(contact.wa_id, "That handover step expired. Please reopen the checklist.")
        _caretaker_checklist_menu(contact, state)
        return True

    if flow == "caretaker_routine":
        if lowered in {"cancel", "stop"} or choice == "cri_cancel":
            state.state = {"flow": "menu"}
            _save_state(state)
            send_text(contact.wa_id, "Routine inspection cancelled.")
            _caretaker_checklist_menu(contact, state)
            return True
        if step == "checklist":
            index = state.state.get("checklist_index", 0)
            expected_complete = f"cri_complete_{index}"
            expected_keep = f"cri_keep_{index}"
            expected_pending = f"cri_pending_{index}"
            if choice not in {expected_complete, expected_keep, expected_pending}:
                send_text(contact.wa_id, "Choose a checklist status, or reply *cancel*.")
                return True
            checklist = state.state.get("checklist", [])
            if index >= len(checklist) or not isinstance(checklist[index], dict):
                send_text(contact.wa_id, "That checklist item is no longer available.")
                return True
            checklist[index]["status"] = (
                "pending" if choice == expected_pending else "complete"
            )
            state.state["checklist"] = checklist
            state.state["checklist_index"] = index + 1
            _save_state(state)
            _caretaker_prompt_routine_checklist(contact, state)
            return True
        if step == "findings":
            findings = "" if lowered in {"skip", "none"} else text.strip()
            if len(findings) > 10000 or (not findings and not state.state.get("checklist")):
                send_text(
                    contact.wa_id,
                    "Findings are required for an inspection with no checklist "
                    "and must be no more than 10,000 characters.",
                )
                return True
            state.state["findings"] = findings
            state.state["step"] = "recommendations"
            _save_state(state)
            send_text(
                contact.wa_id,
                "Add recommendations (up to 10,000 characters), or reply *skip* if there are none.",
            )
            return True
        if step == "recommendations":
            recommendations = "" if lowered in {"skip", "none"} else text.strip()
            if len(recommendations) > 10000:
                send_text(contact.wa_id, "Recommendations must be no more than 10,000 characters.")
                return True
            state.state["recommendations"] = recommendations
            _save_state(state)
            _caretaker_finish_routine_inspection(contact, state)
            return True
        if step == "confirm":
            if choice == "cri_submit":
                _caretaker_submit_routine_inspection(contact, state)
            else:
                send_text(contact.wa_id, "Choose Submit inspection or Cancel.")
            return True
        send_text(contact.wa_id, "That inspection step expired. Please reopen the checklist.")
        _caretaker_checklist_menu(contact, state)
        return True

    if flow == "caretaker_exit_inspection":
        if choice == "coi_cancel":
            state.state = {"flow": "menu"}
            _save_state(state)
            send_text(
                contact.wa_id,
                "Move-out inspection cancelled; the assigned task remains pending.",
            )
            _caretaker_checklist_menu(contact, state)
            return True
        if step == "actual_date":
            try:
                actual_date = date.fromisoformat(text.strip())
            except ValueError:
                send_text(
                    contact.wa_id,
                    "Enter the actual move-out date as YYYY-MM-DD.",
                )
                return True
            inspection = _caretaker_move_out_inspection_for_contact(
                contact,
                state.state.get("inspection_id"),
            )
            if inspection is None:
                send_text(
                    contact.wa_id,
                    "That assigned move-out inspection is no longer available.",
                )
                _caretaker_checklist_menu(contact, state)
                return True
            if actual_date < inspection.move_out_notice.tenancy.start_date:
                send_text(
                    contact.wa_id,
                    "The actual move-out date cannot precede the tenancy start.",
                )
                return True
            if actual_date < inspection.move_out_notice.proposed_move_out_date:
                send_text(
                    contact.wa_id,
                    "The actual move-out date cannot be before "
                    "the proposed move-out date.",
                )
                return True
            if actual_date > timezone.localdate():
                send_text(
                    contact.wa_id,
                    "The actual move-out date cannot be in the future.",
                )
                return True
            from users.views import _move_out_utility_configs

            requirements = []
            for utility in _move_out_utility_configs(inspection.unit, actual_date):
                meter = utility.metadata.get("meter") or inspection.unit.meters.get(
                    utility.utility_type
                )
                if not isinstance(meter, str) or not meter.strip():
                    send_text(
                        contact.wa_id,
                        f"The Property Officer must configure the {utility.name} "
                        "meter before this "
                        "inspection can be submitted.",
                    )
                    return True
                requirements.append(
                    {
                        "utility_type": utility.utility_type,
                        "name": utility.name,
                        "meter": meter.strip(),
                    }
                )
            state.state.update(
                {
                    "step": "checklist",
                    "actual_move_out_date": actual_date.isoformat(),
                    "meter_requirements": requirements,
                }
            )
            _save_state(state)
            _caretaker_prompt_move_out_checklist(contact, state)
            return True
        if step == "checklist":
            index = state.state.get("checklist_index", 0)
            if choice not in {f"coi_complete_{index}", f"coi_keep_{index}"}:
                send_text(
                    contact.wa_id,
                    "Choose a checklist status, or reply *cancel*.",
                )
                return True
            checklist = state.state.get("checklist", [])
            if index >= len(checklist) or not isinstance(checklist[index], dict):
                send_text(
                    contact.wa_id,
                    "That exit checklist item is no longer available.",
                )
                return True
            checklist[index]["status"] = "complete"
            state.state["checklist"] = checklist
            state.state["checklist_index"] = index + 1
            _save_state(state)
            _caretaker_prompt_move_out_checklist(contact, state)
            return True
        if step == "final_reading":
            requirements = state.state.get("meter_requirements", [])
            index = state.state.get("reading_index", 0)
            if (
                not isinstance(requirements, list)
                or not isinstance(index, int)
                or index < 0
                or index >= len(requirements)
            ):
                send_text(
                    contact.wa_id,
                    "That meter-reading step expired. Please reopen the inspection.",
                )
                _caretaker_checklist_menu(contact, state)
                return True
            try:
                reading = Decimal(text.strip())
                if (
                    not reading.is_finite()
                    or reading < 0
                    or reading.as_tuple().exponent < -4
                    or reading.adjusted() >= 10
                ):
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                send_text(
                    contact.wa_id,
                    "Enter a non-negative reading with at most 4 decimal places.",
                )
                return True
            requirement = requirements[index]
            state.state.setdefault("final_readings", []).append(
                {
                    "utility_type": requirement["utility_type"],
                    "current_value": format(reading, "f"),
                }
            )
            state.state["reading_index"] = index + 1
            _save_state(state)
            _caretaker_prompt_move_out_reading(contact, state)
            return True
        if step == "damage_prompt":
            if choice == "coi_damage_yes":
                _caretaker_prompt_move_out_damage(contact, state)
            elif choice == "coi_damage_no":
                state.state["step"] = "maintenance"
                _save_state(state)
                send_buttons(
                    contact.wa_id,
                    "Does the unit require maintenance before the "
                    "next tenant moves in?",
                    [
                        ("coi_maintenance_yes", "Yes"),
                        ("coi_maintenance_no", "No"),
                    ],
                )
            else:
                send_text(contact.wa_id, "Choose Add damage or No damage.")
            return True
        if step == "damage_item":
            if lowered in {"skip", "none", "no"}:
                state.state["step"] = "maintenance"
                _save_state(state)
                send_buttons(
                    contact.wa_id,
                    "Does the unit require maintenance before the "
                    "next tenant moves in?",
                    [
                        ("coi_maintenance_yes", "Yes"),
                        ("coi_maintenance_no", "No"),
                    ],
                )
                return True
            if "|" not in text:
                send_text(contact.wa_id, "Use *description | amount*, or reply *skip*.")
                return True
            description, amount = text.rsplit("|", 1)
            damage_items = state.state.get("damage_items", [])
            from users.views import _move_out_damage_items

            try:
                validated, _ = _move_out_damage_items(
                    [
                        *damage_items,
                        {"description": description.strip(), "amount": amount.strip()},
                    ]
                )
            except ValueError as exc:
                send_text(contact.wa_id, str(exc))
                return True
            state.state["damage_items"] = validated
            state.state["step"] = "damage_more"
            _save_state(state)
            send_buttons(
                contact.wa_id,
                f"Damage recorded ({len(validated)} "
                f"item{'s' if len(validated) != 1 else ''}).",
                [
                    ("coi_damage_add", "Add another"),
                    ("coi_damage_done", "Continue"),
                ],
            )
            return True
        if step == "damage_more":
            if choice == "coi_damage_add":
                _caretaker_prompt_move_out_damage(contact, state)
            elif choice == "coi_damage_done":
                state.state["step"] = "maintenance"
                _save_state(state)
                send_buttons(
                    contact.wa_id,
                    "Does the unit require maintenance before the "
                    "next tenant moves in?",
                    [
                        ("coi_maintenance_yes", "Yes"),
                        ("coi_maintenance_no", "No"),
                    ],
                )
            else:
                send_text(contact.wa_id, "Choose Add another or Continue.")
            return True
        if step == "maintenance":
            if choice == "coi_maintenance_yes":
                state.state["maintenance_required"] = True
            elif choice == "coi_maintenance_no":
                state.state["maintenance_required"] = False
            else:
                send_text(contact.wa_id, "Choose Yes or No.")
                return True
            state.state["step"] = "findings"
            _save_state(state)
            send_text(
                contact.wa_id,
                "Add inspection findings, or reply *skip* if there are none.",
            )
            return True
        if step == "findings":
            findings = "" if lowered in {"skip", "none"} else text.strip()
            if len(findings) > 10000:
                send_text(
                    contact.wa_id,
                    "Findings must be no more than 10,000 characters.",
                )
                return True
            state.state["findings"] = findings
            state.state["step"] = "recommendations"
            _save_state(state)
            send_text(
                contact.wa_id,
                "Add recommendations, or reply *skip* if there are none.",
            )
            return True
        if step == "recommendations":
            recommendations = "" if lowered in {"skip", "none"} else text.strip()
            if len(recommendations) > 10000:
                send_text(
                    contact.wa_id,
                    "Recommendations must be no more than 10,000 characters.",
                )
                return True
            state.state["recommendations"] = recommendations
            _caretaker_prompt_move_out_photos(contact, state)
            return True
        if step == "photos":
            if kind == "image":
                _caretaker_add_move_out_photo(contact, state, message.get("image", {}))
                return True
            if lowered in {"done", "skip"}:
                _caretaker_finish_move_out_inspection(contact, state)
                return True
            send_text(
                contact.wa_id,
                "Send a photo, reply *done* to review, or reply *skip*.",
            )
            return True
        if step == "confirm":
            if choice == "coi_submit":
                _caretaker_submit_move_out_inspection(contact, state)
            else:
                send_text(contact.wa_id, "Choose Submit inspection or Cancel.")
            return True
        send_text(
            contact.wa_id,
            "That move-out inspection step expired. Please reopen the task.",
        )
        _caretaker_checklist_menu(contact, state)
        return True

    return False


def _handle_caretaker_message(contact, state, message, choice, text, kind):
    if state.active_role != ROLE_CARETAKER:
        return False
    flow = state.state.get("flow", "")
    step = state.state.get("step", "")
    lowered = text.lower().strip()

    if lowered == "menu":
        _show_menu(contact, state)
        return True
    if choice == "c_tickets":
        _caretaker_ticket_list(contact, state)
        return True
    if choice == "c_report":
        _caretaker_start_report(contact, state)
        return True
    if choice.startswith("c_page_"):
        try:
            offset = int(choice.removeprefix("c_page_"))
        except ValueError:
            send_text(contact.wa_id, "That ticket page is invalid. Please open the ticket list again.")
        else:
            _caretaker_ticket_list(contact, state, offset)
        return True
    if choice.startswith("tkt_"):
        try:
            ticket_id = int(choice.removeprefix("tkt_"))
        except ValueError:
            send_text(contact.wa_id, "That ticket selection is invalid.")
        else:
            _caretaker_ticket_detail(contact, state, ticket_id)
        return True
    if choice.startswith(("ca_", "cs_")):
        prefix = "ca_" if choice.startswith("ca_") else "cs_"
        try:
            ticket_id = int(choice.removeprefix(prefix))
        except ValueError:
            send_text(contact.wa_id, "That ticket action is invalid.")
        else:
            new_status = (
                MaintenanceTicket.STATUS_ASSIGNED
                if prefix == "ca_"
                else MaintenanceTicket.STATUS_IN_PROGRESS
            )
            _caretaker_update_ticket_status(contact, state, ticket_id, new_status)
        return True
    if choice.startswith("tc_"):
        try:
            ticket_id = int(choice.removeprefix("tc_"))
        except ValueError:
            send_text(contact.wa_id, "That ticket action is invalid.")
        else:
            _caretaker_start_completion(contact, state, ticket_id)
        return True
    if choice.startswith("cm_"):
        try:
            ticket_id = int(choice.removeprefix("cm_"))
        except ValueError:
            send_text(contact.wa_id, "That ticket selection is invalid.")
        else:
            _caretaker_start_comment(contact, state, ticket_id)
        return True

    if _handle_caretaker_checklist_message(contact, state, message, choice, text, kind):
        return True

    if flow == "caretaker_complete":
        if step == "fundi_name":
            name = text.strip()
            if not name or len(name) > 255:
                send_text(contact.wa_id, "Enter a technician/fundi name of 1 to 255 characters.")
                return True
            state.state.update({"step": "repair_cost", "fundi_name": name})
            _save_state(state)
            send_text(contact.wa_id, "Enter the repair cost in KES (for example, 1500 or 1500.50).")
            return True
        if step == "repair_cost":
            try:
                repair_cost = Decimal(text.strip())
                if (
                    not repair_cost.is_finite()
                    or repair_cost < 0
                    or repair_cost > Decimal("9999999999.99")
                    or repair_cost != repair_cost.quantize(Decimal("0.01"))
                ):
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                send_text(contact.wa_id, "Enter a non-negative repair cost with at most 2 decimal places.")
                return True
            state.state.update({"step": "photos", "repair_cost": str(repair_cost)})
            _save_state(state)
            send_text(
                contact.wa_id,
                "Send at least one completion photo, then reply *done*. "
                "You can send up to 5 photos.",
            )
            return True
        if step == "photos":
            if kind == "image":
                _caretaker_add_completion_photo(contact, state, message.get("image", {}))
                return True
            if lowered == "done":
                if not state.state.get("photos"):
                    send_text(contact.wa_id, "At least one completion photo is required before continuing.")
                    return True
                ticket = _caretaker_tickets(contact).filter(
                    pk=state.state.get("ticket_id")
                ).first()
                if ticket is None:
                    send_text(contact.wa_id, "That ticket is no longer available.")
                    return True
                state.state["step"] = "confirm"
                _save_state(state)
                send_buttons(
                    contact.wa_id,
                    f"Review completion for ticket #{ticket.pk} — {ticket.title}\n"
                    f"Technician: {state.state['fundi_name']}\n"
                    f"Repair cost: KES {state.state['repair_cost']}\n"
                    f"Photos: {len(state.state['photos'])}",
                    [("cc_complete", "Complete ticket"), ("cc_cancel", "Cancel")],
                )
                return True
            send_text(contact.wa_id, "Send a completion photo or reply *done* when finished.")
            return True
        if step == "confirm":
            if choice == "cc_complete":
                _caretaker_complete_ticket(contact, state)
            elif choice == "cc_cancel":
                _show_menu(contact, state)
            else:
                send_text(contact.wa_id, "Choose Complete ticket or Cancel.")
            return True
        return True

    if flow == "caretaker_comment" and step == "body":
        if text:
            _caretaker_add_comment(contact, state, text)
        else:
            send_text(contact.wa_id, "Reply with a text update for the ticket.")
        return True

    if flow == "caretaker_create":
        if step == "property":
            if choice.startswith("c_property_page_"):
                try:
                    offset = int(choice.removeprefix("c_property_page_"))
                except ValueError:
                    send_text(contact.wa_id, "That property page is invalid.")
                else:
                    _caretaker_property_page(contact, state, offset)
                return True
            if choice.startswith("c_property_"):
                try:
                    property_id = int(choice.removeprefix("c_property_"))
                except ValueError:
                    send_text(contact.wa_id, "That property selection is invalid.")
                else:
                    if _caretaker_properties(contact).filter(pk=property_id).exists():
                        _caretaker_prompt_category(contact, state, property_id)
                    else:
                        send_text(contact.wa_id, "That property is no longer assigned to your account.")
                return True
            send_text(contact.wa_id, "Choose a property from the list to continue.")
            return True
        if step == "category":
            category = choice.removeprefix("cat_") if choice.startswith("cat_") else ""
            if category not in dict(MaintenanceTicket.CATEGORY_CHOICES):
                send_text(contact.wa_id, "Choose a category from the list.")
                return True
            state.state.update({"step": "priority", "category": category})
            _save_state(state)
            send_list(
                contact.wa_id,
                "Choose the issue priority.",
                "Priority",
                [
                    (f"c_priority_{value}", label, "")
                    for value, label in MaintenanceTicket.PRIORITY_CHOICES
                ],
            )
            return True
        if step == "priority":
            priority = choice.removeprefix("c_priority_") if choice.startswith("c_priority_") else ""
            if priority not in dict(MaintenanceTicket.PRIORITY_CHOICES):
                send_text(contact.wa_id, "Choose a priority from the list.")
                return True
            state.state.update({"step": "title", "priority": priority})
            _save_state(state)
            send_text(contact.wa_id, "Enter a short title for the on-site issue (up to 255 characters).")
            return True
        if step == "title":
            title = text.strip()
            if not title or len(title) > 255:
                send_text(contact.wa_id, "Enter a title of 1 to 255 characters.")
                return True
            state.state.update({"step": "description", "title": title})
            _save_state(state)
            send_text(contact.wa_id, "Describe the issue in up to 5,000 characters.")
            return True
        if step == "description":
            description = text.strip()
            if not description or len(description) > 5000:
                send_text(contact.wa_id, "Enter a description of 1 to 5,000 characters.")
                return True
            state.state.update({"step": "confirm", "description": description})
            _save_state(state)
            send_buttons(
                contact.wa_id,
                f"Log this on-site issue?\n{state.state['title']}\n{description[:300]}",
                [("cc_submit", "Submit ticket"), ("cc_cancel", "Cancel")],
            )
            return True
        if step == "confirm":
            if choice == "cc_submit":
                _caretaker_create_ticket(contact, state)
            elif choice == "cc_cancel":
                _show_menu(contact, state)
            else:
                send_text(contact.wa_id, "Choose Submit ticket or Cancel.")
            return True

    if flow == "menu":
        if "ticket" in lowered or "job" in lowered:
            _caretaker_ticket_list(contact, state)
            return True
        if "report" in lowered or "issue" in lowered:
            _caretaker_start_report(contact, state)
            return True
    return False


def _active_tenancy(contact):
    return (
        contact.tenant.tenancies.filter(status=Tenancy.STATUS_ACTIVE)
        .select_related("unit__property")
        .order_by("-start_date")
        .first()
    )


def _start_issue_flow(contact, state):
    if _active_tenancy(contact) is None:
        send_text(
            contact.wa_id,
            "An active tenancy is required to report a maintenance issue. "
            "Please contact the office if this seems wrong.",
        )
        return
    if contact.tenant.portal_user_id is None:
        send_text(
            contact.wa_id,
            "Please activate your tenant portal account first — ask the office "
            "for your activation link — then report issues right here.",
        )
        return
    state.state = {"flow": "issue", "step": "category"}
    _save_state(state)
    send_list(
        contact.wa_id,
        "🔧 What kind of issue are you reporting?",
        "Pick a category",
        [(row_id, label, "") for row_id, label in CATEGORY_ROWS],
    )


def _issue_set_category(contact, state, category):
    state.state = {"flow": "issue", "step": "description", "category": category}
    _save_state(state)
    send_text(contact.wa_id, "Briefly describe the issue in one message.")


def _issue_set_description(contact, state, description):
    description = description.strip()
    if not description or len(description) > 5000:
        send_text(contact.wa_id, "Please describe the issue in up to 5000 characters.")
        return
    state.state = {
        **state.state,
        "step": "photos",
        "description": description,
        "photos": [],
        "hashes": [],
    }
    _save_state(state)
    send_text(
        contact.wa_id,
        f"📷 Now send up to {MAX_TICKET_PHOTOS} photos of the issue. "
        "Reply *done* when finished, or *skip* if you have none.",
    )


def _issue_add_photo(contact, state, image):
    draft = state.state
    if len(draft.get("photos", [])) >= MAX_TICKET_PHOTOS:
        send_text(contact.wa_id, "Photo limit reached — reply *done* to submit.")
        return
    sha256 = image.get("sha256", "")
    if sha256 and sha256 in draft.get("hashes", []):
        return
    try:
        content, content_type = _fetch_media(image["id"])
    except Exception:
        logger.exception("WhatsApp media download failed.")
        send_text(contact.wa_id, "That photo could not be received — please resend it.")
        return
    content_type = content_type or image.get("mime_type", "")
    extension = ALLOWED_PHOTO_TYPES.get(content_type)
    if extension is None or len(content) > MAX_PHOTO_BYTES:
        send_text(contact.wa_id, "Photos must be JPEG or PNG and at most 5 MB.")
        return
    key = f"maintenance/tenant/{contact.tenant.pk}/{uuid.uuid4().hex}.{extension}"
    try:
        upload_private_object(key, content, content_type)
    except Exception:
        logger.exception("WhatsApp maintenance photo upload failed.")
        send_text(contact.wa_id, "That photo could not be stored — please resend it.")
        return
    draft.setdefault("photos", []).append({
        "storage_key": key,
        "content_type": content_type,
        "size_bytes": len(content),
        "name": f"Evidence {len(draft.get('photos', [])) + 1}.{extension}",
    })
    if sha256:
        draft.setdefault("hashes", []).append(sha256)
    state.state = draft
    _save_state(state)
    send_text(contact.wa_id, f"📷 Photo {len(draft['photos'])} received.")


def _issue_submit(contact, state):
    draft = state.state
    tenancy = _active_tenancy(contact)
    if tenancy is None:
        send_text(contact.wa_id, "An active tenancy is required to submit this request.")
        return
    description = draft.get("description", "")
    ticket = MaintenanceTicket(
        property=tenancy.unit.property,
        unit=tenancy.unit,
        reported_by=contact.tenant.portal_user,
        title=description[:252] + ("..." if len(description) > 255 else ""),
        description=description,
        category=draft.get("category", MaintenanceTicket.CATEGORY_OTHER),
        priority=MaintenanceTicket.PRIORITY_NORMAL,
        photos=draft.get("photos", []),
    )
    try:
        ticket.full_clean()
        with transaction.atomic():
            ticket.save()
    except Exception:
        logger.exception("WhatsApp maintenance ticket could not be saved.")
        send_text(
            contact.wa_id,
            "Your request could not be saved — please try again shortly.",
        )
        return
    AuditLog.objects.create(
        user=contact.tenant.portal_user,
        action="maintenance_ticket.tenant_created",
        entity="MaintenanceTicket",
        new_values={"ticket_id": ticket.pk, "source": "whatsapp"},
    )
    state.state = {"flow": "menu"}
    _save_state(state)
    photo_count = len(ticket.photos)
    photo_summary = (
        "No photos attached"
        if photo_count == 0
        else f"{photo_count} photo{'s' if photo_count != 1 else ''} attached"
    )
    description_preview = description[:200] + ("…" if len(description) > 200 else "")
    send_text(
        contact.wa_id,
        "✅ Thanks — your maintenance request has been logged for review.\n\n"
        "📋 Request summary:\n"
        f"• Ticket: #{ticket.pk}\n"
        f"• Location: {ticket.property.name}, unit {tenancy.unit.code}\n"
        f"• Category: {ticket.get_category_display()}\n"
        f"• Priority: {ticket.get_priority_display()}\n"
        f"• Issue: {description_preview}\n"
        f"• Photos: {photo_summary}\n\n"
        "Our property team will review it and coordinate the next steps. "
        "Keep the ticket number for reference.",
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def _save_state(state):
    state.expires_at = timezone.now() + timedelta(minutes=SESSION_TTL_MINUTES)
    state.save()


def _interactive_choice(message):
    interactive = message.get("interactive", {})
    reply = interactive.get("list_reply") or interactive.get("button_reply") or {}
    return reply.get("id", "")


def handle_inbound_message(message: dict) -> None:
    """Route one inbound message, serialized per contact.

    The contact row lock is held for the whole routing pass so concurrent
    gunicorn workers cannot interleave two messages from the same person —
    e.g. "done" must not read a draft while a photo upload is still saving.
    """
    wa_id = message.get("from", "")
    if not wa_id:
        return
    with transaction.atomic():
        contact, _ = WhatsAppContact.objects.select_for_update().get_or_create(
            wa_id=wa_id
        )
        state, _ = ConversationState.objects.get_or_create(contact=contact)
        _route_message(contact, state, message)


def _auto_link_phone_verified_tenant(contact) -> bool:
    phone = str(contact.wa_id or "").lstrip("+")
    if not phone.isdigit():
        return False
    candidates = list(
        Tenant.objects.filter(
            phone=phone,
            phone_verified_at__isnull=False,
            portal_user__is_active=True,
            tenancies__status=Tenancy.STATUS_ACTIVE,
        )
        .distinct()
        .select_related("portal_user")
        .order_by("id")[:2]
    )
    if len(candidates) != 1:
        return False
    tenant = candidates[0]
    contact.tenant = tenant
    contact.user = tenant.portal_user
    contact.verified_at = timezone.now()
    contact.failed_verifications = 0
    contact.locked_until = None
    contact.save(update_fields=[
        "tenant",
        "user",
        "verified_at",
        "failed_verifications",
        "locked_until",
        "updated_at",
    ])
    _audit("whatsapp.contact_phone_verified", {
        "contact_id": contact.pk,
        "tenant_id": tenant.pk,
    })
    return True


def _route_message(contact, state, message: dict) -> None:
    message_id = message.get("id", "")
    if message_id and state.last_message_id == message_id:
        return
    state.last_message_id = message_id
    state.save(update_fields=["last_message_id", "updated_at"])
    # Sessions idle past the TTL restart from the greeting: any in-progress
    # flow is discarded and the role must be chosen afresh.
    if state.expires_at and state.expires_at < timezone.now():
        state.state = {}
        state.active_role = ""

    kind = message.get("type")
    text = message.get("text", {}).get("body", "").strip() if kind == "text" else ""
    choice = _interactive_choice(message) if kind == "interactive" else ""
    flow = state.state.get("flow", "")
    step = state.state.get("step", "")

    if not contact.verified_at and not _auto_link_phone_verified_tenant(contact):
        if flow == "verify" and step == "awaiting_id" and text:
            _handle_verification_reply(contact, state, text)
        else:
            _prompt_for_id(contact, state)
        return

    # "cancel"/"stop" abandons whatever flow is in progress from any step,
    # including a draft maintenance ticket, and falls back to the role
    # chooser (multi-role contacts) or the menu (single role).
    if text and text.lower().strip() in {"cancel", "stop"}:
        state.state = {}
        state.active_role = ""
        _save_state(state)
        _show_entry(contact, state)
        return

    if choice.startswith("role_"):
        selected = choice.removeprefix("role_")
        if selected in _roles(contact):
            state.active_role = selected
            _show_menu(contact, state)
            return
    if choice == "switch_role" or (text and text.lower().strip() == "switch"):
        _show_entry(contact, state)
        return
    if not state.active_role:
        _show_entry(contact, state)
        return

    if _handle_caretaker_message(contact, state, message, choice, text, kind):
        return

    if choice == "t_balance":
        _tenant_balance(contact)
        return
    if choice == "t_receipts":
        _tenant_receipts(contact)
        return
    if choice == "t_issue":
        _start_issue_flow(contact, state)
        return
    if choice == "t_pay":
        _start_pay_flow(contact, state)
        return
    if choice.startswith("inv_") and flow == "pay":
        _confirm_payment(contact, state, int(choice.removeprefix("inv_")))
        return
    if choice.startswith("stk_") and flow == "pay":
        _confirm_payment(contact, state, int(choice.removeprefix("stk_")))
        return
    if flow == "pay" and step == "awaiting_phone" and text:
        inv_id = state.state.get("invoice_id")
        if inv_id:
            try:
                phone = daraja.normalize_ke_phone(text.strip())
            except ValueError as exc:
                send_text(contact.wa_id, f"⚠️ {exc}\nPlease enter a valid Safaricom number.")
                return
            _prompt_payment_amount(contact, state, int(inv_id), phone)
            return
    if flow == "pay" and step == "awaiting_amount" and text:
        inv_id = state.state.get("invoice_id")
        phone = state.state.get("phone")
        if inv_id and phone:
            _initiate_stk(
                contact,
                state,
                int(inv_id),
                phone,
                amount_text=text,
            )
            return
    if choice == "pay_cancel":
        _show_menu(contact, state)
        return
    if choice == "l_summary":
        _landlord_summary(contact)
        return
    if choice == "l_approvals":
        _landlord_approvals(contact, state)
        return
    if choice.startswith("q_app_"):
        _decide_quotation(contact, state, int(choice.removeprefix("q_app_")), True)
        return
    if choice.startswith("q_rej_"):
        state.state = {
            "flow": "reject_quote",
            "quotation_id": int(choice.removeprefix("q_rej_")),
        }
        _save_state(state)
        send_text(contact.wa_id, "Briefly state the reason for rejecting this quotation.")
        return
    if flow == "reject_quote" and text:
        _decide_quotation(
            contact, state, state.state.get("quotation_id"), False, reason=text[:500]
        )
        return
    if choice.startswith("cat_") and flow == "issue" and step == "category":
        _issue_set_category(contact, state, choice.removeprefix("cat_"))
        return

    if flow == "issue":
        if step == "description" and text:
            _issue_set_description(contact, state, text)
            return
        if step == "photos":
            if kind == "image":
                _issue_add_photo(contact, state, message.get("image", {}))
                return
            lowered = text.lower()
            if lowered in {"done", "skip"}:
                _issue_submit(contact, state)
                return
            send_text(
                contact.wa_id,
                "Send photos of the issue, or reply *done* to submit "
                "(*skip* if you have no photos).",
            )
            return

    lowered = text.lower()
    if state.active_role == ROLE_TENANT:
        if "pay" in lowered:
            _start_pay_flow(contact, state)
            return
        if "balance" in lowered or "rent" in lowered:
            _tenant_balance(contact)
            return
        if "receipt" in lowered:
            _tenant_receipts(contact)
            return
        if any(word in lowered for word in ("issue", "repair", "broken", "leak", "fix")):
            _start_issue_flow(contact, state)
            return
    _show_menu(contact, state)


# ---------------------------------------------------------------------------
# Daraja STK callback
# ---------------------------------------------------------------------------


@csrf_exempt
def receive_rent_stk_callback(request):
    try:
        payload = json.loads(request.body)
        callback = payload["Body"]["stkCallback"]
        checkout_request_id = callback["CheckoutRequestID"]
        result_code = int(callback["ResultCode"])
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError):
        return JsonResponse({"error": "Invalid callback payload."}, status=400)

    callback_items = {}
    callback_receipt = ""
    callback_amount = Decimal("0")
    callback_phone = ""
    validation_error = ""
    callback_merchant_request_id = str(
        callback.get("MerchantRequestID", "")
    ).strip()
    if result_code == 0:
        try:
            callback_items = {
                item["Name"]: item["Value"]
                for item in callback["CallbackMetadata"]["Item"]
            }
            callback_amount = Decimal(str(callback_items["Amount"]))
            callback_receipt = str(callback_items["MpesaReceiptNumber"]).strip()
            callback_phone = daraja.normalize_ke_phone(
                str(callback_items["PhoneNumber"])
            )
            if not callback_amount.is_finite() or not callback_receipt:
                validation_error = "M-PESA success callback is missing valid payment details."
        except (
            KeyError,
            TypeError,
            ValueError,
            InvalidOperation,
        ):
            validation_error = "M-PESA success callback is missing valid payment details."

    with transaction.atomic():
        stk_request = (
            RentStkRequest.objects.select_for_update(of=("self",))
            .filter(
                checkout_request_id=checkout_request_id,
                status=RentStkRequest.STATUS_PENDING,
                result_code="",
            )
            .select_related("tenant__branch__agency", "invoice")
            .first()
        )
        if stk_request is None:
            # Unknown or already processed — acknowledge so Daraja stops retrying.
            return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
        if result_code != 0:
            stk_request.status = RentStkRequest.STATUS_FAILED
            stk_request.result_code = str(result_code)[:20]
            stk_request.result_description = str(callback.get("ResultDesc", ""))[:500]
            stk_request.processed_at = timezone.now()
            stk_request.save(update_fields=[
                "status", "result_code", "result_description", "processed_at", "updated_at",
            ])
        elif (
            validation_error
            or callback_amount != stk_request.amount
            or callback_phone != stk_request.phone
            or (
                stk_request.merchant_request_id
                and callback_merchant_request_id != stk_request.merchant_request_id
            )
        ):
            stk_request.status = RentStkRequest.STATUS_FAILED
            stk_request.result_code = "callback_mismatch"
            stk_request.result_description = (
                validation_error
                or "M-PESA success details did not match the pending payment request."
            )
            stk_request.processed_at = timezone.now()
            stk_request.save(update_fields=[
                "status", "result_code", "result_description", "processed_at", "updated_at",
            ])
        else:
            stk_request.result_code = "processing"
            stk_request.result_description = str(callback.get("ResultDesc", ""))[:500]
            stk_request.save(update_fields=[
                "result_code", "result_description", "updated_at",
            ])

    if result_code != 0:
        if stk_request.contact:
            send_text(
                stk_request.contact.wa_id,
                "❌ The M-PESA payment was not completed "
                f"({callback.get('ResultDesc', 'cancelled')}). "
                "Reply *pay* to try again.",
            )
        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})

    if stk_request.result_code == "callback_mismatch":
        if stk_request.contact:
            send_text(
                stk_request.contact.wa_id,
                "⚠️ M-PESA reported a payment, but we could not verify its details, "
                "so your ledger has not been updated yet. If money was deducted, "
                "please do not retry; contact management with your M-PESA receipt.",
            )
        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})

    try:
        _complete_rent_payment(stk_request, callback_receipt, callback_amount)
    except Exception:
        logger.exception(
            "Rent STK callback %s could not be completed.", checkout_request_id
        )
        stk_request.refresh_from_db()
        if stk_request.status != RentStkRequest.STATUS_SUCCEEDED:
            RentStkRequest.objects.filter(
                pk=stk_request.pk,
                status=RentStkRequest.STATUS_PENDING,
                result_code="processing",
            ).update(
                status=RentStkRequest.STATUS_FAILED,
                result_code="callback_processing",
                result_description=(
                    "M-PESA reported payment, but recording it failed. If money was "
                    "deducted, do not retry; contact management."
                ),
                processed_at=timezone.now(),
            )
        if stk_request.contact and stk_request.status != RentStkRequest.STATUS_SUCCEEDED:
            send_text(
                stk_request.contact.wa_id,
                "⚠️ M-PESA reported a payment, but we could not finish recording it. "
                "If money was deducted, please do not retry; contact management.",
            )
        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
