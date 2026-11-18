"""Conversational WhatsApp flows (Phase 2).

Routes inbound webhook messages: ID-number verification links a WhatsApp
number to saved records, then role-scoped menus serve tenant self-service —
rent balance, receipts, and photo-based maintenance reporting.

Session replies here are free-form interactive messages, allowed because an
inbound customer message always opens Meta's 24-hour service window. Outbound
notifications outside that window stay on the Phase 1 template queue.
"""

import hashlib
import json
import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
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
    Payment,
    PaymentAllocation,
    Receipt,
    RentStkRequest,
    Tenancy,
    Tenant,
    WhatsAppContact,
)
from users.object_storage import upload_private_object
from users.roles import CARETAKER_ROLE, has_role

logger = logging.getLogger(__name__)

MAX_VERIFICATION_ATTEMPTS = 5
VERIFICATION_LOCK_HOURS = 24
MAX_TICKET_PHOTOS = 5
MAX_PHOTO_BYTES = 5 * 1024 * 1024
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
    normalised = _normalise_id_number(id_number)
    tenant = Tenant.objects.filter(
        national_id_or_passport__iexact=normalised
    ).order_by("-created_at").first()
    landlord = Landlord.objects.filter(
        id_or_registration_number__iexact=normalised
    ).order_by("-created_at").first()
    return tenant, landlord


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
    tenant, landlord = _match_identities(candidate)
    if tenant is None and landlord is None:
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
    contact.user = (tenant.portal_user if tenant else None) or (
        landlord.portal_user if landlord else None
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
    })
    name = (tenant.name if tenant else landlord.name).split()[0]
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
            ("c_tickets", "My assigned tickets", "Open maintenance at your properties"),
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
    allocated = {
        row["invoice_id"]: row["total"] or zero
        for row in PaymentAllocation.objects.filter(
            invoice_id__in=[invoice.pk for invoice in invoices],
        )
        .exclude(
            payment__instrument_status__in=(
                Payment.INSTRUMENT_PENDING,
                Payment.INSTRUMENT_BOUNCED,
            )
        )
        .values("invoice_id")
        .annotate(total=Sum("amount"))
        .values("invoice_id", "total")
    }
    outstanding = []
    for invoice in invoices:
        due = invoice.total - allocated.get(invoice.pk, zero)
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
        send_text(contact.wa_id, "🎉 You are fully paid up — nothing to pay.")
        return
    state.state = {"flow": "pay", "step": "choose_invoice"}
    _save_state(state)
    send_list(
        contact.wa_id,
        "💳 Which invoice would you like to pay?",
        "Pick an invoice",
        [
            (
                f"inv_{invoice.pk}",
                f"{invoice.number}"[:24],
                f"{invoice.period_start.strftime('%b %Y')} · KES {due:,.2f}",
            )
            for invoice, due in outstanding[:10]
        ],
    )


def _confirm_payment(contact, state, invoice_id):
    for invoice, due in _outstanding_invoices(contact.tenant):
        if invoice.pk == invoice_id:
            state.state = {"flow": "pay", "step": "confirm", "invoice_id": invoice_id}
            _save_state(state)
            send_buttons(
                contact.wa_id,
                f"Pay KES {due:,.2f} for invoice {invoice.number} via M-PESA? "
                "The prompt will appear on this phone.",
                [(f"stk_{invoice_id}", "Pay now"), ("pay_cancel", "Cancel")],
            )
            return
    send_text(contact.wa_id, "That invoice is no longer outstanding.")


def _initiate_stk(contact, state, invoice_id):
    match = [
        (invoice, due)
        for invoice, due in _outstanding_invoices(contact.tenant)
        if invoice.pk == invoice_id
    ]
    if not match:
        send_text(contact.wa_id, "That invoice is no longer outstanding.")
        return
    invoice, due = match[0]
    amount = due.quantize(Decimal("1"))
    try:
        result = daraja.stk_push(
            contact.wa_id,
            amount,
            invoice.number,
            "Rent",
        )
    except Exception:
        logger.exception("Rent STK push could not be initiated.")
        send_buttons(
            contact.wa_id,
            "The M-PESA request could not be started. Try again?",
            [(f"stk_{invoice_id}", "Try again"), ("pay_cancel", "Cancel")],
        )
        return
    RentStkRequest.objects.create(
        invoice=invoice,
        tenant=contact.tenant,
        contact=contact,
        amount=due,
        phone=contact.wa_id,
        checkout_request_id=result["checkout_request_id"],
        merchant_request_id=result["merchant_request_id"],
    )
    state.state = {"flow": "menu"}
    _save_state(state)
    send_text(
        contact.wa_id,
        f"📲 An M-PESA prompt for KES {due:,.2f} is on its way to this phone. "
        "Enter your PIN to complete the payment — your receipt will arrive here.",
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
    from users.sms import queue_template_sms, sms_backend_is_configured
    from users.views import _ensure_payment_receipt

    tenant = stk_request.tenant
    invoice = stk_request.invoice
    with transaction.atomic():
        payment = Payment.objects.create(
            tenant=tenant,
            amount=paid_amount,
            reference=receipt_number,
            source=Payment.SOURCE_MPESA,
            account_reference=invoice.number,
            payer_phone=stk_request.phone,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=paid_amount,
        )
        PaymentAllocation.objects.create(
            payment=payment,
            invoice=invoice,
            amount=paid_amount,
        )
        stk_request.status = RentStkRequest.STATUS_SUCCEEDED
        stk_request.payment = payment
        stk_request.processed_at = timezone.now()
        stk_request.save(update_fields=[
            "status", "payment", "processed_at", "updated_at",
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
        except Exception:
            logger.exception("Receipt SMS could not be queued.")
    _send_payment_receipt_email(tenant, receipt_number, paid_amount)
    AuditLog.objects.create(
        user=None,
        action="payment.rent_stk_completed",
        entity="Payment",
        new_values={
            "payment_id": payment.pk,
            "invoice_id": invoice.pk,
            "amount": str(paid_amount),
            "source": "whatsapp_stk",
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
    from django.db.models import Q

    from users.models import Property

    return Property.objects.filter(
        Q(caretakers=contact.user)
        | Q(
            caretaker_assignments__caretaker=contact.user,
            caretaker_assignments__ended_at__isnull=True,
        )
    ).distinct()


def _caretaker_open_tickets(contact):
    return (
        MaintenanceTicket.objects.filter(
            property__in=_caretaker_properties(contact),
            status__in=[
                MaintenanceTicket.STATUS_OPEN,
                MaintenanceTicket.STATUS_ASSIGNED,
                MaintenanceTicket.STATUS_IN_PROGRESS,
            ],
        )
        .select_related("property", "unit")
        .order_by("-created_at")
    )


def _caretaker_ticket_list(contact, state):
    tickets = list(_caretaker_open_tickets(contact)[:10])
    if not tickets:
        send_text(contact.wa_id, "✅ No open tickets at your properties right now.")
        return
    send_list(
        contact.wa_id,
        "🔧 Open tickets at your properties — pick one:",
        "Open tickets",
        [
            (
                f"tkt_{ticket.pk}",
                f"#{ticket.pk} {ticket.property.name}"[:24],
                (ticket.unit.code + " · " if ticket.unit else "") + ticket.title[:60],
            )
            for ticket in tickets
        ],
    )


def _caretaker_ticket_detail(contact, state, ticket_id):
    ticket = _caretaker_open_tickets(contact).filter(pk=ticket_id).first()
    if ticket is None:
        send_text(contact.wa_id, "That ticket is no longer open.")
        return
    unit_label = ticket.unit.code if ticket.unit else "common area"
    send_buttons(
        contact.wa_id,
        f"🔧 Ticket #{ticket.pk} — {ticket.property.name}, {unit_label}\n\n"
        f"{ticket.description[:400]}\n\n"
        f"Status: {ticket.get_status_display()} · Priority: {ticket.get_priority_display()}",
        [(f"tc_{ticket.pk}", "Mark complete"), ("c_tickets", "Back to tickets")],
    )


def _caretaker_complete_ticket(contact, state, ticket_id):
    with transaction.atomic():
        ticket = (
            _caretaker_open_tickets(contact)
            .select_for_update(of=("self",))
            .filter(pk=ticket_id)
            .first()
        )
        if ticket is None:
            send_text(contact.wa_id, "That ticket is no longer open.")
            return
        ticket.status = MaintenanceTicket.STATUS_COMPLETED
        ticket.save(update_fields=["status", "updated_at"])
    AuditLog.objects.create(
        user=contact.user,
        action="maintenance_ticket.caretaker_completed",
        entity="MaintenanceTicket",
        new_values={"ticket_id": ticket.pk, "source": "whatsapp"},
    )
    send_text(contact.wa_id, f"✅ Ticket #{ticket.pk} marked complete. Good work!")

    # Close the loop with the tenant, when the unit has an active tenancy.
    tenancy = (
        Tenancy.objects.filter(unit=ticket.unit, status=Tenancy.STATUS_ACTIVE)
        .select_related("tenant")
        .first()
        if ticket.unit
        else None
    )
    if tenancy and tenancy.tenant.phone:
        from users.sms import queue_template_sms, sms_backend_is_configured
        from users.whatsapp import queue_template_whatsapp_if_enabled

        context = {
            "property_name": ticket.property.name,
            "unit_label": ticket.unit.code,
        }
        queue_template_whatsapp_if_enabled(
            tenancy.tenant.phone, "maintenance_ticket_completed_tenant", context
        )
        if sms_backend_is_configured():
            try:
                queue_template_sms(
                    tenancy.tenant.phone, "maintenance_ticket_completed_tenant", context
                )
            except Exception:
                logger.exception("Maintenance completion SMS could not be queued.")


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
    photo_count = len(draft.get("photos", []))
    description_preview = description[:200] + ("…" if len(description) > 200 else "")
    send_text(
        contact.wa_id,
        "✅ Your maintenance request has been submitted successfully.\n\n"
        "📋 Here is what we received:\n"
        f"• Ticket: #{ticket.pk}\n"
        f"• Property: {ticket.property.name}, unit {tenancy.unit.code}\n"
        f"• Category: {ticket.get_category_display()}\n"
        f"• Priority: {ticket.get_priority_display()}\n"
        f"• Description: {description_preview}\n"
        f"• Photos: {photo_count}\n\n"
        "The caretaker will be in touch. We'll update you here once it "
        "has been attended to.",
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

    if not contact.verified_at:
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

    if choice == "c_tickets":
        _caretaker_ticket_list(contact, state)
        return
    if choice.startswith("tkt_"):
        _caretaker_ticket_detail(contact, state, int(choice.removeprefix("tkt_")))
        return
    if choice.startswith("tc_"):
        _caretaker_complete_ticket(contact, state, int(choice.removeprefix("tc_")))
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
    if choice.startswith("stk_"):
        _initiate_stk(contact, state, int(choice.removeprefix("stk_")))
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
    if state.active_role == ROLE_CARETAKER and ("ticket" in lowered or "job" in lowered):
        _caretaker_ticket_list(contact, state)
        return
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
        result_code = callback["ResultCode"]
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
        return JsonResponse({"error": "Invalid callback payload."}, status=400)

    with transaction.atomic():
        stk_request = (
            RentStkRequest.objects.select_for_update(of=("self",))
            .filter(
                checkout_request_id=checkout_request_id,
                status=RentStkRequest.STATUS_PENDING,
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

    if result_code != 0:
        send_text(
            stk_request.phone,
            "❌ The M-PESA payment was not completed "
            f"({callback.get('ResultDesc', 'cancelled')}). "
            "Reply *pay* to try again.",
        )
        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})

    items = {
        item.get("Name"): item.get("Value")
        for item in callback.get("CallbackMetadata", {}).get("Item", [])
    }
    receipt_number = str(items.get("MpesaReceiptNumber", "")) or checkout_request_id
    paid_amount = Decimal(str(items.get("Amount", stk_request.amount)))
    try:
        _complete_rent_payment(stk_request, receipt_number, paid_amount)
    except Exception:
        logger.exception(
            "Rent STK callback %s could not be completed.", checkout_request_id
        )
        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
