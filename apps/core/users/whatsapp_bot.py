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
from django.utils import timezone

from users.models import (
    AuditLog,
    ConversationState,
    Invoice,
    Landlord,
    MaintenanceTicket,
    Payment,
    PaymentAllocation,
    Receipt,
    Tenancy,
    Tenant,
    WhatsAppContact,
)
from users.object_storage import upload_private_object

logger = logging.getLogger(__name__)

MAX_VERIFICATION_ATTEMPTS = 5
VERIFICATION_LOCK_HOURS = 24
MAX_TICKET_PHOTOS = 5
MAX_PHOTO_BYTES = 5 * 1024 * 1024
ALLOWED_PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png"}
STATE_TTL_HOURS = 24

ROLE_TENANT = "tenant"
ROLE_LANDLORD = "landlord"

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


def send_text(wa_id: str, body: str) -> None:
    _post_message({"to": wa_id, "type": "text", "text": {"body": body}})


def send_buttons(wa_id: str, body: str, buttons: list[tuple[str, str]]) -> None:
    _post_message({
        "to": wa_id,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": option_id, "title": title}}
                    for option_id, title in buttons
                ],
            },
        },
    })


def send_list(wa_id: str, body: str, button: str, rows: list[tuple[str, str, str]]) -> None:
    _post_message({
        "to": wa_id,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body},
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
    if not candidate or len(candidate) > 100:
        send_text(contact.wa_id, "Please send only your ID number.")
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
        state.state = {"flow": "choose_role"}
        _save_state(state)
        send_buttons(
            contact.wa_id,
            "You have more than one role with us. How would you like to continue today?",
            [("role_tenant", "Tenant"), ("role_landlord", "Landlord")],
        )
        return
    state.active_role = roles[0] if roles else ""
    _show_menu(contact, state)


def _show_menu(contact, state):
    if state.active_role == ROLE_LANDLORD:
        state.state = {}
        _save_state(state)
        send_text(
            contact.wa_id,
            "🏠 Landlord services on WhatsApp (portfolio, approvals, remittances) "
            "are coming soon. Meanwhile, your full dashboard is on the portal.",
        )
        return
    state.state = {"flow": "menu"}
    _save_state(state)
    send_list(
        contact.wa_id,
        f"Hi {contact.tenant.name.split()[0]}! How can we help today?",
        "Tenant services",
        [
            ("t_balance", "Rent balance", "What you owe and when it's due"),
            ("t_receipts", "My receipts", "Your recent payment receipts"),
            ("t_issue", "Report an issue", "Repairs and maintenance, with photos"),
        ],
    )


def _tenant_balance(contact):
    tenant = contact.tenant
    today = timezone.localdate()
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
            created_at__date__lte=today,
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
    lines = []
    balance = zero
    for invoice in invoices:
        due = invoice.total - allocated.get(invoice.pk, zero)
        if due <= zero:
            continue
        balance += due
        if len(lines) < 3:
            lines.append(
                f"• {invoice.number} ({invoice.period_start.strftime('%b %Y')}): "
                f"KES {due:,.2f}"
            )
    if balance <= zero:
        send_text(contact.wa_id, "🎉 You are fully paid up — no outstanding balance.")
        return
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
    send_text(
        contact.wa_id,
        f"🔧 Request #{ticket.pk} created with {photo_count} photo(s). "
        "The caretaker will be in touch. We'll update you here.",
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def _save_state(state):
    state.expires_at = timezone.now() + timedelta(hours=STATE_TTL_HOURS)
    state.save()


def _interactive_choice(message):
    interactive = message.get("interactive", {})
    reply = interactive.get("list_reply") or interactive.get("button_reply") or {}
    return reply.get("id", "")


def handle_inbound_message(message: dict) -> None:
    wa_id = message.get("from", "")
    message_id = message.get("id", "")
    if not wa_id:
        return
    with transaction.atomic():
        contact, _ = WhatsAppContact.objects.select_for_update().get_or_create(
            wa_id=wa_id
        )
        state, _ = ConversationState.objects.get_or_create(contact=contact)
        if message_id and state.last_message_id == message_id:
            return
        state.last_message_id = message_id
        state.save(update_fields=["last_message_id", "updated_at"])
    if state.expires_at and state.expires_at < timezone.now():
        state.state = {}
        state.active_role = state.active_role or ""

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

    if choice in {"role_tenant", "role_landlord"}:
        state.active_role = ROLE_TENANT if choice == "role_tenant" else ROLE_LANDLORD
        _show_menu(contact, state)
        return
    if not state.active_role:
        _show_entry(contact, state)
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
