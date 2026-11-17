import base64
import hashlib
import hmac
import importlib
import json
import logging
from datetime import timedelta
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from users.models import AuditLog, WhatsAppDelivery

logger = logging.getLogger(__name__)

# Meta webhook error codes that silently stop all business-initiated delivery
# and therefore need staff attention the moment they appear.
ACCOUNT_LEVEL_ERROR_CODES = {131042, 131049}


class WhatsAppBackend(Protocol):
    def send(self, phone: str, payload: dict[str, Any]) -> str | None: ...


# Internal template name -> approved Meta template and its named body
# parameters. Parameter values are format strings rendered against the same
# context dicts the SMS templates use, so call sites queue both channels with
# one context. Meta rules for this WABA: named lowercase variables only, and a
# body may not start or end with a variable.
WHATSAPP_TEMPLATES = {
    "payment_receipt": {
        "meta_name": "payment_confirmation",
        "params": {
            "amount": "{amount}",
            "invoice_ref": "{reference}",
        },
    },
    "invoice_reminder": {
        "meta_name": "invoice_reminder",
        "params": {
            "amount": "{amount}",
            "agency_name": "{agency_name}",
        },
    },
    "maintenance_ticket_completed_tenant": {
        "meta_name": "maintenance_completed",
        "params": {
            "property_name": "{property_name}",
            "unit_label": "{unit_label}",
        },
    },
}


class ConsoleWhatsAppBackend:
    def send(self, phone, payload):
        if not settings.DEBUG:
            raise ImproperlyConfigured(
                "The console WhatsApp backend is available only when DEBUG is enabled."
            )
        logger.warning("Development-only WhatsApp to %s: %s", phone, payload)


class CloudAPIWhatsAppBackend:
    def send(self, phone, payload):
        access_token = settings.WHATSAPP_ACCESS_TOKEN
        phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID
        if not access_token or not phone_number_id:
            raise ImproperlyConfigured(
                "WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID must be configured."
            )
        if not isinstance(phone, str) or not phone.strip():
            raise ValueError("A recipient phone number is required.")
        recipient = phone.strip().lstrip("+")
        if not recipient.isdigit() or not 8 <= len(recipient) <= 15:
            raise ValueError(
                "The WhatsApp recipient must be a valid international phone number."
            )

        body = json.dumps({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "template",
            "template": {
                "name": payload["template"],
                "language": {"code": payload["language"]},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {
                                "type": "text",
                                "parameter_name": name,
                                "text": value,
                            }
                            for name, value in payload["params"].items()
                        ],
                    }
                ] if payload["params"] else [],
            },
        }).encode("utf-8")
        request = Request(
            "https://graph.facebook.com/"
            f"{settings.WHATSAPP_API_VERSION}/{phone_number_id}/messages",
            data=body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=settings.WHATSAPP_TIMEOUT_SECONDS) as response:
                response_body = response.read()
        except HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8"))["error"]["message"]
            except Exception:
                pass
            raise RuntimeError(
                f"Meta rejected the WhatsApp request (HTTP {exc.code}): {detail}"
            ) from None
        except URLError:
            raise RuntimeError("The WhatsApp Cloud API could not be reached.") from None
        except TimeoutError:
            raise RuntimeError("The WhatsApp Cloud API request timed out.") from None

        try:
            result = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        messages = result.get("messages") or []
        if messages and isinstance(messages[0], dict):
            return messages[0].get("id")
        return None


def whatsapp_backend_is_configured():
    backend_path = getattr(settings, "WHATSAPP_BACKEND", "")
    if not backend_path:
        return False
    if backend_path == "users.whatsapp.CloudAPIWhatsAppBackend":
        return bool(
            settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID
        )
    return True


def send_whatsapp(phone, payload):
    backend_path = getattr(settings, "WHATSAPP_BACKEND", "")
    if not backend_path:
        raise ImproperlyConfigured(
            "WHATSAPP_BACKEND must be configured before WhatsApp messages can be sent."
        )
    module_name, class_name = backend_path.rsplit(".", 1)
    backend_class = getattr(importlib.import_module(module_name), class_name)
    return backend_class().send(phone, payload)


def _fernet():
    secret_key = settings.SECRET_KEY
    if not secret_key:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be configured to encrypt queued WhatsApp content."
        )
    derived_key = hashlib.sha256(
        b"legacy-real-estate:whatsapp-outbox:v1:" + secret_key.encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(derived_key))


def render_whatsapp_payload(template_name: str, context: dict[str, Any]) -> dict[str, Any]:
    template = WHATSAPP_TEMPLATES.get(template_name)
    if template is None:
        raise ValueError(f"Unknown WhatsApp template: {template_name}")
    try:
        params = {
            name: value.format_map(context)
            for name, value in template["params"].items()
        }
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(
            f"Invalid context for WhatsApp template: {template_name}"
        ) from exc
    if any(not value.strip() for value in params.values()):
        raise ValueError("Rendered WhatsApp parameters cannot be empty.")
    return {
        "template": template["meta_name"],
        "language": settings.WHATSAPP_LANGUAGE_CODE,
        "params": params,
    }


def _audit_delivery(request, actor, action, delivery):
    values = {
        "delivery_id": delivery.pk,
        "template_name": delivery.template_name,
        "status": delivery.status,
        "attempts": delivery.attempts,
        "created_by_id": delivery.created_by_id,
    }
    if request is not None:
        from users.audit import record_audit_event

        record_audit_event(
            request,
            action,
            "WhatsAppDelivery",
            new_values=values,
            actor=actor,
        )
        return
    AuditLog.objects.create(
        user=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        entity="WhatsAppDelivery",
        new_values=values,
    )


def queue_template_whatsapp(
    phone: str,
    template_name: str,
    context: dict[str, Any],
    *,
    actor=None,
    request=None,
    expires_at=None,
) -> WhatsAppDelivery:
    if not isinstance(phone, str) or not phone.strip():
        raise ValueError("A recipient phone number is required.")
    if not whatsapp_backend_is_configured():
        raise ImproperlyConfigured(
            "WHATSAPP_BACKEND must be configured before WhatsApp messages can be queued."
        )
    payload = render_whatsapp_payload(template_name, context)
    fernet = _fernet()
    with transaction.atomic():
        delivery = WhatsAppDelivery.objects.create(
            template_name=template_name,
            recipient_encrypted=fernet.encrypt(phone.strip().encode("utf-8")),
            payload_encrypted=fernet.encrypt(json.dumps(payload).encode("utf-8")),
            expires_at=expires_at,
            created_by=actor if getattr(actor, "is_authenticated", False) else None,
        )
        _audit_delivery(request, actor, "whatsapp.queued", delivery)
    return delivery


def queue_template_whatsapp_if_enabled(phone, template_name, context, **kwargs):
    """Best-effort fan-out used beside existing SMS call sites.

    Returns None without raising when the channel is not configured, the
    template has no WhatsApp mapping yet, or queueing fails — the SMS path
    must never be disrupted by the WhatsApp rollout.
    """
    if not whatsapp_backend_is_configured():
        return None
    if template_name not in WHATSAPP_TEMPLATES:
        return None
    if not isinstance(phone, str) or not phone.strip():
        return None
    try:
        return queue_template_whatsapp(phone, template_name, context, **kwargs)
    except Exception:
        logger.exception(
            "WhatsApp fan-out for template %s could not be queued.", template_name
        )
        return None


def dispatch_next_whatsapp_delivery(*, now=None, max_attempts=5):
    if max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer.")
    now = now or timezone.now()
    stale_before = now - timedelta(minutes=5)
    with transaction.atomic():
        delivery = (
            WhatsAppDelivery.objects.select_for_update(skip_locked=True)
            .filter(
                Q(
                    status=WhatsAppDelivery.STATUS_QUEUED,
                    available_at__lte=now,
                )
                | Q(
                    status=WhatsAppDelivery.STATUS_SENDING,
                    locked_at__lte=stale_before,
                )
            )
            .order_by("created_at", "id")
            .first()
        )
        if delivery is None:
            return None
        if delivery.expires_at and delivery.expires_at <= now:
            delivery.status = WhatsAppDelivery.STATUS_EXPIRED
            delivery.recipient_encrypted = b""
            delivery.payload_encrypted = b""
            delivery.locked_at = None
            delivery.error_code = ""
            delivery.save(update_fields=[
                "status",
                "recipient_encrypted",
                "payload_encrypted",
                "locked_at",
                "error_code",
                "updated_at",
            ])
            _audit_delivery(None, None, "whatsapp.expired", delivery)
            return delivery
        delivery.status = WhatsAppDelivery.STATUS_SENDING
        delivery.locked_at = now
        delivery.attempts += 1
        delivery.save(update_fields=["status", "locked_at", "attempts", "updated_at"])

    fernet = _fernet()
    try:
        phone = fernet.decrypt(bytes(delivery.recipient_encrypted)).decode("utf-8")
        payload = json.loads(
            fernet.decrypt(bytes(delivery.payload_encrypted)).decode("utf-8")
        )
        provider_message_id = send_whatsapp(phone, payload)
    except Exception as exc:
        with transaction.atomic():
            delivery.refresh_from_db()
            delivery.error_code = type(exc).__name__[:100]
            delivery.locked_at = None
            if delivery.attempts >= max_attempts:
                delivery.status = WhatsAppDelivery.STATUS_FAILED
                delivery.recipient_encrypted = b""
                delivery.payload_encrypted = b""
            else:
                delivery.status = WhatsAppDelivery.STATUS_QUEUED
                delivery.available_at = now + timedelta(
                    seconds=min(30 * (2 ** (delivery.attempts - 1)), 1800)
                )
            delivery.save(update_fields=[
                "status",
                "recipient_encrypted",
                "payload_encrypted",
                "available_at",
                "locked_at",
                "error_code",
                "updated_at",
            ])
            _audit_delivery(None, None, "whatsapp.delivery_failed", delivery)
        logger.warning(
            "WhatsApp delivery %s failed on attempt %s (%s).",
            delivery.pk,
            delivery.attempts,
            type(exc).__name__,
        )
        return delivery

    with transaction.atomic():
        delivery.status = WhatsAppDelivery.STATUS_SENT
        delivery.sent_at = now
        delivery.locked_at = None
        if isinstance(provider_message_id, str):
            delivery.provider_message_id = provider_message_id[:255]
        delivery.error_code = ""
        delivery.recipient_encrypted = b""
        delivery.payload_encrypted = b""
        delivery.save(update_fields=[
            "status",
            "sent_at",
            "locked_at",
            "provider_message_id",
            "error_code",
            "recipient_encrypted",
            "payload_encrypted",
            "updated_at",
        ])
        _audit_delivery(None, None, "whatsapp.sent", delivery)
    return delivery


def _signature_is_valid(request):
    app_secret = settings.WHATSAPP_APP_SECRET
    if not app_secret:
        return settings.DEBUG
    header = request.headers.get("X-Hub-Signature-256", "")
    if not header.startswith("sha256="):
        return False
    expected = hmac.new(
        app_secret.encode("utf-8"),
        request.body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), expected)


def _apply_status_event(status_event):
    provider_message_id = status_event.get("id")
    channel_status = status_event.get("status", "")
    if not provider_message_id or not channel_status:
        return
    delivery = WhatsAppDelivery.objects.filter(
        provider_message_id=provider_message_id
    ).first()
    error_codes = [
        error.get("code")
        for error in status_event.get("errors", [])
        if isinstance(error, dict)
    ]
    if delivery is not None:
        delivery.channel_status = channel_status[:20]
        if channel_status == "failed" and error_codes:
            delivery.error_code = str(error_codes[0])[:100]
        delivery.save(update_fields=["channel_status", "error_code", "updated_at"])
    account_level = [
        code for code in error_codes if code in ACCOUNT_LEVEL_ERROR_CODES
    ]
    if account_level:
        AuditLog.objects.create(
            user=None,
            action="whatsapp.account_delivery_blocked",
            entity="WhatsAppDelivery",
            new_values={
                "provider_message_id": provider_message_id,
                "error_codes": account_level,
            },
        )
        logger.error(
            "WhatsApp account-level delivery block reported by Meta (codes %s). "
            "Business-initiated messages are being dropped until this is resolved.",
            account_level,
        )


@csrf_exempt
@require_http_methods(["GET", "POST"])
def whatsapp_webhook(request):
    if request.method == "GET":
        params = request.GET
        if (
            params.get("hub.mode") == "subscribe"
            and params.get("hub.verify_token") == settings.WHATSAPP_VERIFY_TOKEN
            and settings.WHATSAPP_VERIFY_TOKEN
        ):
            return HttpResponse(params.get("hub.challenge", ""))
        return HttpResponseForbidden()

    if not _signature_is_valid(request):
        return HttpResponseForbidden()
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid webhook payload."}, status=400)
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for status_event in value.get("statuses", []):
                _apply_status_event(status_event)
            for message in value.get("messages", []):
                # Routing failures must never bubble into a non-2xx response,
                # or Meta retries the whole batch.
                try:
                    from users.whatsapp_bot import handle_inbound_message

                    handle_inbound_message(message)
                except Exception:
                    logger.exception(
                        "Inbound WhatsApp message %s could not be routed.",
                        message.get("id"),
                    )
    return JsonResponse({"status": "ok"})
