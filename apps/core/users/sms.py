import base64
import hashlib
import importlib
import json
import logging
import secrets
from datetime import timedelta
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from users.models import AuditLog, SMSDelivery

logger = logging.getLogger(__name__)


class SMSBackend(Protocol):
    def send(self, phone: str, message: str) -> str | dict[str, str] | None: ...


SMS_TEMPLATES = {
    "login_verification_code": (
        "Your Legacy Real Estate sign-in code is {code}. It expires shortly."
    ),
    "payment_receipt": (
        "Payment {reference} of KES {amount} has been received. "
        "Thank you, {agency_name}."
    ),
    "tenant_activation": (
        "Hi {tenant_name}, welcome to Legacy Real Estate! Your tenant account "
        "has been successfully onboarded. Create your password at {activation_url} "
        "using OTP {code}. This link expires in 48 hours."
    ),
    "invoice_reminder": (
        "Your rent account has an outstanding balance of KES {amount}. "
        "Please contact {agency_name} if you have already paid."
    ),
    "caretaker_water_readings": (
        "Please record and publish all water meter readings at {property_name} "
        "for {billing_month} billing by month-end."
    ),
    "agent_billing_drafts": (
        "{invoice_count} draft invoice(s) for {property_name}, {billing_month}, "
        "are ready in the Billing Wizard."
    ),
    "director_invoice_waiver": (
        "A KES {amount} waiver request for {property_name}, unit {unit_code}, "
        "for {billing_month} is awaiting your approval. Reason: {reason}"
    ),
    "move_out_notice_submitted": (
        "Move-out notice submitted: {tenant_name}, {property_name} / {unit_code}, "
        "proposed exit {move_out_date}. Please review the request."
    ),
    "move_out_notice_withdrawn": (
        "Move-out notice withdrawn: {tenant_name}, {property_name} / {unit_code}, "
        "proposed exit {move_out_date}."
    ),
    "move_out_notice_updated": (
        "Your move-out notice for {property_name} / {unit_code} is {status}. "
        "Proposed exit: {move_out_date}.{note}"
    ),
    "move_in_handover_assigned": (
        "Move-in inspection assigned: {property_name} / {unit_code} for "
        "{tenant_name}. Complete the pending checklist on {move_in_date}."
    ),
    "move_out_inspection_assigned": (
        "Exit inspection assigned: {property_name} / {unit_code} for {tenant_name} "
        "on {move_out_date}. "
        "Please complete the condition checklist, photos, and final meter readings."
    ),
    "tenant_move_out_inspection_scheduled": (
        "An exit inspection is scheduled for {property_name} / {unit_code} "
        "on {move_out_date}. "
        "The caretaker will record the unit condition and final meter readings."
    ),
    "move_out_inspection_completed": (
        "Exit inspection completed for {property_name} / {unit_code} ({tenant_name}). "
        "The findings and evidence are ready for settlement preparation."
    ),
    "move_out_settlement_submitted": (
        "Move-out settlement for {tenant_name}, {property_name} / {unit_code}, "
        "is awaiting approval. Refund KES {refund}; deductions KES {deductions}; "
        "shortfall KES {shortfall}."
    ),
    "move_out_settlement_approved": (
        "Move-out settlement approved for {tenant_name}, {property_name} / {unit_code}. "
        "Refund due: KES {refund}. Finance can now record the payout."
    ),
    "move_out_settlement_rejected": (
        "The move-out settlement for {property_name} / {unit_code} needs correction. "
        "Reason: {reason}"
    ),
    "move_out_settlement_paid": (
        "Move-out settlement completed for {property_name} / {unit_code}. "
        "Refund KES {refund}; deductions KES {deductions}; shortfall KES {shortfall}. "
        "Payout reference: {reference}. Statement: {statement_url}"
    ),
    "move_in_handover_submitted": (
        "Move-in handover submitted for {tenant_name}, {property_name} / {unit_code}. "
        "Review the condition, keys, opening readings, and photos in the portal."
    ),
    "move_in_handover_confirmed": (
        "Move-in handover confirmed for {property_name} / {unit_code}, {tenant_name}. "
        "Keys and opening readings have been recorded."
    ),
    "maintenance_ticket_completed_staff": (
        "On-site repair completed: {property_name}, {unit_label} — {title}. "
        "Technician: {fundi_name}; reported cost: KES {repair_cost}. Review the sign-off in the portal."
    ),
    "maintenance_ticket_completed_tenant": (
        "Your maintenance request for {property_name}, {unit_label} has been marked complete. "
        "Contact management if the issue remains."
    ),
}


class ConsoleSMSBackend:
    def send(self, phone, message):
        if not settings.DEBUG:
            raise ImproperlyConfigured(
                "The console SMS backend is available only when DEBUG is enabled."
            )
        logger.warning("Development-only SMS to %s: %s", phone, message)


class SoftlinkSMSBackend:
    def send(self, phone, message, *, idempotency_key):
        api_key = settings.SOFTLINK_SMS_API_KEY
        api_url = settings.SOFTLINK_SMS_API_URL
        if not api_key:
            raise ImproperlyConfigured("SOFTLINK_SMS_API_KEY must be configured.")
        if not api_url.startswith("https://"):
            raise ImproperlyConfigured("SOFTLINK_SMS_API_URL must use HTTPS.")
        if not isinstance(phone, str) or not phone.strip():
            raise ValueError("A recipient phone number is required.")
        recipient = phone.strip()
        if not recipient.startswith("+"):
            recipient = f"+{recipient}"
        if not recipient[1:].isdigit() or not 8 <= len(recipient[1:]) <= 15:
            raise ValueError("The SMS recipient must be a valid international phone number.")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("An idempotency key is required for Softlink SMS delivery.")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("SMS content cannot be empty.")

        payload = json.dumps({
            "recipients": [recipient],
            "body": message,
            "traffic_class": settings.SOFTLINK_SMS_TRAFFIC_CLASS,
        }).encode("utf-8")
        request = Request(
            api_url,
            data=payload,
            headers={
                "X-Api-Key": api_key,
                "Idempotency-Key": idempotency_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=settings.SOFTLINK_SMS_TIMEOUT_SECONDS) as response:
                response_body = response.read()
        except HTTPError as exc:
            raise RuntimeError(f"Softlink rejected the SMS request (HTTP {exc.code}).") from None
        except URLError:
            raise RuntimeError("Softlink SMS service could not be reached.") from None
        except TimeoutError:
            raise RuntimeError("Softlink SMS request timed out.") from None

        if not response_body:
            return None
        try:
            result = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(result, dict):
            return None
        if result.get("success") is False or str(result.get("status", "")).lower() in {
            "error",
            "failed",
            "failure",
            "rejected",
        }:
            raise RuntimeError("Softlink rejected the SMS request.")
        return result.get("message_id") or result.get("id")


def sms_backend_is_configured():
    backend_path = getattr(settings, "AUTH_SMS_BACKEND", "")
    if not backend_path:
        return False
    if backend_path == "users.sms.SoftlinkSMSBackend":
        return bool(
            settings.SOFTLINK_SMS_API_KEY
            and settings.SOFTLINK_SMS_API_URL.startswith("https://")
        )
    return True


def send_sms(phone, message, *, idempotency_key=None):
    backend_path = getattr(settings, "AUTH_SMS_BACKEND", "")
    if not backend_path:
        raise ImproperlyConfigured(
            "AUTH_SMS_BACKEND must be configured before SMS authentication can be used."
        )

    module_name, class_name = backend_path.rsplit(".", 1)
    backend_class = getattr(importlib.import_module(module_name), class_name)
    backend = backend_class()
    if isinstance(backend, SoftlinkSMSBackend):
        return backend.send(
            phone,
            message,
            idempotency_key=idempotency_key or secrets.token_hex(16),
        )
    return backend.send(phone, message)


def _fernet():
    secret_key = settings.SECRET_KEY
    if not secret_key:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be configured to encrypt queued SMS content."
        )
    derived_key = hashlib.sha256(
        b"legacy-real-estate:sms-outbox:v1:" + secret_key.encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(derived_key))


def render_sms_template(template_name: str, context: dict[str, Any]) -> str:
    template = SMS_TEMPLATES.get(template_name)
    if template is None:
        raise ValueError(f"Unknown SMS template: {template_name}")
    try:
        message = template.format_map(context)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid context for SMS template: {template_name}") from exc
    if not message.strip():
        raise ValueError("Rendered SMS content cannot be empty.")
    return message


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
            "SMSDelivery",
            new_values=values,
            actor=actor,
        )
        return
    AuditLog.objects.create(
        user=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        entity="SMSDelivery",
        new_values=values,
    )


def queue_template_sms(
    phone: str,
    template_name: str,
    context: dict[str, Any],
    *,
    actor=None,
    request=None,
    expires_at=None,
) -> SMSDelivery:
    if not isinstance(phone, str) or not phone.strip():
        raise ValueError("A recipient phone number is required.")
    if not sms_backend_is_configured():
        raise ImproperlyConfigured(
            "AUTH_SMS_BACKEND must be configured before SMS messages can be queued."
        )
    message = render_sms_template(template_name, context)
    fernet = _fernet()
    with transaction.atomic():
        delivery = SMSDelivery.objects.create(
            template_name=template_name,
            recipient_encrypted=fernet.encrypt(phone.strip().encode("utf-8")),
            message_encrypted=fernet.encrypt(message.encode("utf-8")),
            expires_at=expires_at,
            created_by=actor if getattr(actor, "is_authenticated", False) else None,
        )
        _audit_delivery(request, actor, "sms.queued", delivery)
    return delivery


def _provider_message_id(result):
    if isinstance(result, dict):
        result = result.get("message_id") or result.get("id")
    if isinstance(result, str):
        return result[:255]
    return ""


def dispatch_next_sms_delivery(*, now=None, max_attempts=5):
    if max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer.")
    now = now or timezone.now()
    stale_before = now - timedelta(minutes=5)
    with transaction.atomic():
        delivery = (
            SMSDelivery.objects.select_for_update(skip_locked=True)
            .filter(
                Q(
                    status=SMSDelivery.STATUS_QUEUED,
                    available_at__lte=now,
                )
                | Q(
                    status=SMSDelivery.STATUS_SENDING,
                    locked_at__lte=stale_before,
                )
            )
            .order_by("created_at", "id")
            .first()
        )
        if delivery is None:
            return None
        if delivery.expires_at and delivery.expires_at <= now:
            delivery.status = SMSDelivery.STATUS_EXPIRED
            delivery.recipient_encrypted = b""
            delivery.message_encrypted = b""
            delivery.locked_at = None
            delivery.error_code = ""
            delivery.save(update_fields=[
                "status",
                "recipient_encrypted",
                "message_encrypted",
                "locked_at",
                "error_code",
                "updated_at",
            ])
            _audit_delivery(None, None, "sms.expired", delivery)
            return delivery
        delivery.status = SMSDelivery.STATUS_SENDING
        delivery.locked_at = now
        delivery.attempts += 1
        delivery.save(update_fields=["status", "locked_at", "attempts", "updated_at"])

    fernet = _fernet()
    try:
        phone = fernet.decrypt(bytes(delivery.recipient_encrypted)).decode("utf-8")
        message = fernet.decrypt(bytes(delivery.message_encrypted)).decode("utf-8")
        provider_result = send_sms(
            phone,
            message,
            idempotency_key=f"sms-delivery-{delivery.pk}",
        )
    except Exception as exc:
        with transaction.atomic():
            delivery.refresh_from_db()
            delivery.error_code = type(exc).__name__[:100]
            delivery.locked_at = None
            if delivery.attempts >= max_attempts:
                delivery.status = SMSDelivery.STATUS_FAILED
                delivery.recipient_encrypted = b""
                delivery.message_encrypted = b""
            else:
                delivery.status = SMSDelivery.STATUS_QUEUED
                delivery.available_at = now + timedelta(
                    seconds=min(30 * (2 ** (delivery.attempts - 1)), 1800)
                )
            delivery.save(update_fields=[
                "status",
                "recipient_encrypted",
                "message_encrypted",
                "available_at",
                "locked_at",
                "error_code",
                "updated_at",
            ])
            _audit_delivery(None, None, "sms.delivery_failed", delivery)
        logger.warning(
            "SMS delivery %s failed on attempt %s (%s).",
            delivery.pk,
            delivery.attempts,
            type(exc).__name__,
        )
        return delivery

    with transaction.atomic():
        delivery.status = SMSDelivery.STATUS_SENT
        delivery.sent_at = now
        delivery.locked_at = None
        delivery.provider_message_id = _provider_message_id(provider_result)
        delivery.error_code = ""
        delivery.recipient_encrypted = b""
        delivery.message_encrypted = b""
        delivery.save(update_fields=[
            "status",
            "sent_at",
            "locked_at",
            "provider_message_id",
            "error_code",
            "recipient_encrypted",
            "message_encrypted",
            "updated_at",
        ])
        _audit_delivery(None, None, "sms.sent", delivery)
    return delivery
