import calendar
import csv
import hashlib
import json
import re
import secrets
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from io import BytesIO, StringIO

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout, password_validation
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from openpyxl import load_workbook

from users.access import accessible_branches
from users.arrears import arrears_for_accessible_invoices, invoice_arrears
from users.audit import record_audit_event
from users.models import (
    ArrearsPaymentPlan,
    ArrearsPaymentPlanInstalment,
    ArrearsWaiver,
    Announcement,
    AuditLog,
    BankStatementImport,
    BankStatementRow,
    Block,
    Branch,
    CaretakerInvitation,
    DemandLetter,
    DepositAccount,
    DepositEntry,
    Floor,
    Invoice,
    InvoiceLine,
    InvoiceNumberSequence,
    Landlord,
    ManagementAgreement,
    MaintenanceCostAllocation,
    MaintenanceQuotation,
    MeterReading,
    MaintenanceTicket,
    MaintenanceTicketComment,
    MoveInCharge,
    MoveInChecklistItem,
    MoveInPackage,
    MpesaCallback,
    Payment,
    PaymentAllocation,
    Property,
    PropertyCaretakerAssignment,
    PropertyDocument,
    PropertyExpense,
    PropertyInspection,
    Receipt,
    SMSLoginChallenge,
    Tenancy,
    Tenant,
    TenantActivationChallenge,
    TenantCredit,
    TenantDocument,
    Unit,
    UnitCharge,
    UnitReservation,
    UserProfile,
    UtilityConfiguration,
    Vendor,
)
from users.roles import (
    CARETAKER_ROLE,
    LANDLORD_PORTAL_ROLE,
    LANDLORD_MANAGEMENT_ROLE_NAMES,
    TENANT_PORTAL_ROLE,
    requires_finance_mfa,
)
from users.sms import send_sms


def _record_auth_event(request, action, user=None):
    record_audit_event(
        request,
        action,
        "User",
        new_values={"user_id": user.pk} if user else None,
        actor=user,
    )


def _problem(status, title, detail, problem_type):
    return JsonResponse(
        {
            "type": f"https://legacy.example/problems/{problem_type}",
            "title": title,
            "status": status,
            "detail": detail,
        },
        status=status,
        content_type="application/problem+json",
    )


def _json_body(request):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _has_landlord_management_access(user):
    return user.groups.filter(name__in=LANDLORD_MANAGEMENT_ROLE_NAMES).exists()


def _is_caretaker(user):
    return user.groups.filter(name=CARETAKER_ROLE).exists()


def _accessible_branches(user):
    return accessible_branches(user)


def _accessible_landlords(user):
    profile = getattr(user, "profile", None)
    if profile is None or not _has_landlord_management_access(user):
        return Landlord.objects.none()
    return Landlord.objects.filter(branch__in=profile.accessible_branches())


def _accessible_properties(user):
    profile = getattr(user, "profile", None)
    if profile is None:
        return Property.objects.none()
    if _is_caretaker(user):
        return Property.objects.filter(
            caretaker_assignments__caretaker=user,
            caretaker_assignments__ended_at__isnull=True,
        ).distinct()
    if not _has_landlord_management_access(user):
        return Property.objects.none()
    return Property.objects.filter(branch__in=profile.accessible_branches())


def _accessible_tenants(user):
    profile = getattr(user, "profile", None)
    if profile is None or not _has_landlord_management_access(user):
        return Tenant.objects.none()
    return Tenant.objects.filter(branch__in=profile.accessible_branches())


def _decimal_value(body, field):
    value = body.get(field)
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} must be a valid decimal value.")


def _landlord_payload(landlord):
    return {
        "id": landlord.pk,
        "branch_id": landlord.branch_id,
        "name": landlord.name,
        "id_or_registration_number": landlord.id_or_registration_number,
        "kra_pin": landlord.kra_pin or None,
        "phone": landlord.phone,
        "email": landlord.email or None,
        "payout_details": landlord.payout_details,
        "management_agreements": [
            _agreement_payload(agreement)
            for agreement in landlord.management_agreements.all()
        ],
    }


def _agreement_payload(agreement):
    return {
        "id": agreement.pk,
        "effective_from": agreement.effective_from.isoformat(),
        "effective_to": (
            agreement.effective_to.isoformat() if agreement.effective_to else None
        ),
        "commission_model": agreement.commission_model,
        "commission_rate": (
            str(agreement.commission_rate)
            if agreement.commission_rate is not None
            else None
        ),
        "fixed_fee": str(agreement.fixed_fee) if agreement.fixed_fee is not None else None,
        "payout_day": agreement.payout_day,
        "mri_enabled": agreement.mri_enabled,
        "terms": agreement.terms,
    }


def _property_payload(property_obj):
    return {
        "id": property_obj.pk,
        "branch_id": property_obj.branch_id,
        "landlord_id": property_obj.landlord_id,
        "name": property_obj.name,
        "property_type": property_obj.property_type,
        "location": property_obj.location,
        "gps_coordinates": property_obj.gps_coordinates,
        "amenities": property_obj.amenities,
        "house_rules": property_obj.house_rules,
        "photos": property_obj.photos,
        "caretaker_ids": list(
            property_obj.caretaker_assignments.filter(ended_at__isnull=True)
            .order_by("caretaker_id")
            .values_list("caretaker_id", flat=True)
        ),
        "caretakers": [
            {
                "id": assignment.caretaker_id,
                "name": assignment.caretaker.profile.name,
                "email": assignment.caretaker.email,
                "assigned_at": assignment.assigned_at.isoformat(),
            }
            for assignment in property_obj.caretaker_assignments.filter(
                ended_at__isnull=True
            ).select_related("caretaker__profile")
        ],
        "blocks": [
            {
                "id": block.pk,
                "name": block.name,
                "floors": [
                    {"id": floor.pk, "name": floor.name, "level": floor.level}
                    for floor in block.floors.all()
                ],
            }
            for block in property_obj.blocks.all()
        ],
        "documents": [
            {
                "id": document.pk,
                "document_type": document.document_type,
                "name": document.name,
                "storage_key": document.storage_key,
                "metadata": document.metadata,
                "uploaded_by": document.uploaded_by_id,
                "created_at": document.created_at.isoformat(),
            }
            for document in property_obj.documents.all()
        ],
    }


def _unit_payload(unit):
    return {
        "id": unit.pk,
        "property_id": unit.property_id,
        "floor_id": unit.floor_id,
        "code": unit.code,
        "unit_type": unit.unit_type,
        "bedrooms": unit.bedrooms,
        "characteristics": unit.characteristics,
        "meters": unit.meters,
        "listing_enabled": unit.listing_enabled,
        "status": unit.status,
    }


def _current_rent(unit, as_of=None):
    as_of = as_of or timezone.localdate()
    return (
        unit.charges.filter(
            charge_type=UnitCharge.CHARGE_RENT,
            effective_from__lte=as_of,
        )
        .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=as_of))
        .order_by("-effective_from", "-id")
        .first()
    )


def _vacancy_payload(unit, rent):
    payload = _unit_payload(unit)
    payload.update(
        {
            "branch_id": unit.property.branch_id,
            "property_name": unit.property.name,
            "location": unit.property.location,
            "rent": str(rent.amount) if rent else None,
            "rent_charge_id": rent.pk if rent else None,
        }
    )
    return payload


def _reservation_payload(reservation):
    return {
        "id": reservation.pk,
        "unit_id": reservation.unit_id,
        "applicant_id": reservation.applicant_id,
        "expires_at": reservation.expires_at.isoformat(),
        "status": reservation.status,
        "reserved_at": reservation.reserved_at.isoformat(),
        "released_at": (
            reservation.released_at.isoformat() if reservation.released_at else None
        ),
    }


def _unit_audit_values(unit):
    return _unit_payload(unit)


def _expire_reservations(request, reservations):
    now = timezone.now()
    expired = []
    with transaction.atomic():
        for reservation in reservations.select_for_update().select_related("unit"):
            if reservation.status == UnitReservation.STATUS_ACTIVE and reservation.expires_at <= now:
                reservation.release(UnitReservation.STATUS_EXPIRED, now)
                reservation.save(update_fields=["status", "released_at"])
                reservation.unit.listing_enabled = (
                    reservation.listing_enabled_before_reservation
                )
                reservation.unit.save(update_fields=["listing_enabled", "updated_at"])
                record_audit_event(
                    request,
                    "unit_reservation.expired",
                    "UnitReservation",
                    old_values={"id": reservation.pk, "status": UnitReservation.STATUS_ACTIVE},
                    new_values=_reservation_payload(reservation),
                )
                expired.append(reservation)
    return expired


def _charge_payload(charge):
    return {
        "id": charge.pk,
        "unit_id": charge.unit_id,
        "charge_type": charge.charge_type,
        "charge_code": charge.charge_code,
        "name": charge.name,
        "amount": str(charge.amount),
        "effective_from": charge.effective_from.isoformat(),
        "effective_to": (
            charge.effective_to.isoformat() if charge.effective_to else None
        ),
        "metadata": charge.metadata,
    }


def _utility_payload(utility):
    return {
        "id": utility.pk,
        "unit_id": utility.unit_id,
        "utility_type": utility.utility_type,
        "name": utility.name,
        "billing_method": utility.billing_method,
        "fixed_amount": str(utility.fixed_amount) if utility.fixed_amount is not None else None,
        "rate_per_unit": str(utility.rate_per_unit) if utility.rate_per_unit is not None else None,
        "minimum_charge": str(utility.minimum_charge),
        "effective_from": utility.effective_from.isoformat(),
        "effective_to": utility.effective_to.isoformat() if utility.effective_to else None,
        "metadata": utility.metadata,
    }


def _meter_reading_payload(reading):
    return {
        "id": reading.pk,
        "unit_id": reading.unit_id,
        "meter": reading.meter,
        "period": reading.period.isoformat(),
        "previous_value": f"{reading.previous_value:.4f}",
        "current_value": f"{reading.current_value:.4f}",
        "submitted_by": reading.submitted_by_id,
        "submitted_at": reading.submitted_at.isoformat(),
        "photo_key": reading.photo_key or None,
        "photo_metadata": reading.photo_metadata,
    }


def _water_bill_payload(reading, utility, usage, charge):
    return {
        "unit_id": reading.unit_id,
        "meter": reading.meter,
        "period": reading.period.isoformat(),
        "reading_id": reading.pk,
        "utility_id": utility.pk,
        "previous_value": f"{reading.previous_value:.4f}",
        "current_value": f"{reading.current_value:.4f}",
        "usage": f"{usage:.4f}",
        "rate_per_unit": str(utility.rate_per_unit),
        "minimum_charge": str(utility.minimum_charge),
        "calculated_charge": str(charge),
    }


def _tenant_payload(tenant):
    return {
        "id": tenant.pk,
        "branch_id": tenant.branch_id,
        "tenant_type": tenant.tenant_type,
        "name": tenant.name,
        "company_registration_number": tenant.company_registration_number or None,
        "contact_person": tenant.contact_person or None,
        "national_id_or_passport": tenant.national_id_or_passport or None,
        "kra_pin": tenant.kra_pin or None,
        "phone": tenant.phone,
        "email": tenant.email or None,
        "is_blacklisted": tenant.is_blacklisted,
        "blacklist_reason": tenant.blacklist_reason or None,
        "portal_user_id": tenant.portal_user_id,
        "documents": [
            {
                "id": document.pk,
                "document_type": document.document_type,
                "name": document.name,
                "storage_key": document.storage_key,
                "metadata": document.metadata,
                "uploaded_by": document.uploaded_by_id,
                "created_at": document.created_at.isoformat(),
            }
            for document in tenant.documents.all()
        ],
    }


def _tenancy_payload(tenancy):
    return {
        "id": tenancy.pk,
        "unit_id": tenancy.unit_id,
        "tenant_id": tenancy.tenant_id,
        "tenancy_type": tenancy.tenancy_type,
        "start_date": tenancy.start_date.isoformat(),
        "end_date": tenancy.end_date.isoformat() if tenancy.end_date else None,
        "due_day": tenancy.due_day,
        "grace_period_days": tenancy.grace_period_days,
        "notice_period_days": tenancy.notice_period_days,
        "rent_escalation_terms": tenancy.rent_escalation_terms,
        "status": tenancy.status,
    }


def _invoice_line_payload(line):
    return {
        "id": line.pk,
        "invoice_id": line.invoice_id,
        "charge_type": line.charge_type,
        "description": line.description,
        "amount": str(line.amount),
        "metadata": line.metadata,
    }


def _invoice_payload(invoice):
    return {
        "id": invoice.pk,
        "branch_id": invoice.branch_id,
        "tenancy_id": invoice.tenancy_id,
        "number": invoice.number,
        "period_start": invoice.period_start.isoformat(),
        "period_end": invoice.period_end.isoformat(),
        "due_date": invoice.due_date.isoformat(),
        "status": invoice.status,
        "total": str(invoice.total),
        "issued_at": invoice.issued_at.isoformat(),
        "lines": [_invoice_line_payload(line) for line in invoice.lines.all()],
    }


def _arrears_payload(item):
    invoice = item["invoice"]
    return {
        "invoice_id": invoice.pk,
        "invoice_number": invoice.number,
        "tenancy_id": invoice.tenancy_id,
        "tenant": invoice.tenancy.tenant.name,
        "due_date": invoice.due_date.isoformat(),
        "days_overdue": item["days_overdue"],
        "bucket": item["bucket"],
        "outstanding": str(item["outstanding"]),
        "penalty": str(item["penalty"]),
        "total_due": str(item["total_due"]),
        "reminder_due": item["reminder_due"],
    }


def _payment_plan_payload(plan):
    instalments = list(plan.instalments.all())
    return {
        "id": plan.pk,
        "tenancy_id": plan.tenancy_id,
        "total_amount": str(plan.total_amount),
        "instalment_amount": str(plan.instalment_amount),
        "start_date": plan.start_date.isoformat(),
        "status": plan.status,
        "reason": plan.reason,
        "balance": str(
            sum((item.amount_due - item.amount_paid for item in instalments), Decimal("0.00"))
        ),
        "instalments": [
            {
                "id": item.pk,
                "sequence": item.sequence,
                "due_date": item.due_date.isoformat(),
                "amount_due": str(item.amount_due),
                "amount_paid": str(item.amount_paid),
                "status": item.status,
                "paid_at": item.paid_at.isoformat() if item.paid_at else None,
                "missed_at": item.missed_at.isoformat() if item.missed_at else None,
            }
            for item in instalments
        ],
    }


def _callback_value(body, *keys):
    for key in keys:
        value = body.get(key)
        if value not in (None, ""):
            return value
    return None


@csrf_exempt
@require_POST
def receive_mpesa_callback(request):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        return _problem(400, "Invalid callback", "The callback body must be a JSON object.", "invalid-mpesa-callback")

    transaction_code = _callback_value(body, "TransID", "TransactionID", "transaction_code")
    transaction_code = str(transaction_code).strip() if transaction_code is not None else None
    if transaction_code:
        existing = MpesaCallback.objects.filter(transaction_code=transaction_code).first()
        if existing:
            return JsonResponse({"callback": {"id": existing.pk, "transaction_code": existing.transaction_code, "status": existing.status}})

    amount_value = _callback_value(body, "TransAmount", "Amount", "amount")
    try:
        amount = Decimal(str(amount_value)) if amount_value not in (None, "") else None
    except (InvalidOperation, TypeError, ValueError):
        amount = None
    failure_reason = ""
    if not transaction_code:
        failure_reason = "A transaction code is required."
    elif amount is None or amount < 0:
        failure_reason = "A non-negative transaction amount is required."
    callback = MpesaCallback(
        transaction_code=transaction_code,
        amount=amount,
        payer_phone=str(_callback_value(body, "MSISDN", "PhoneNumber", "payer_phone") or ""),
        account_reference=str(_callback_value(body, "BillRefNumber", "AccountReference", "account_reference") or ""),
        transaction_time=str(_callback_value(body, "TransTime", "transaction_time") or ""),
        raw_payload=body,
        status=MpesaCallback.STATUS_FAILED if failure_reason else MpesaCallback.STATUS_PROCESSED,
        failure_reason=failure_reason,
        processed_at=timezone.now() if not failure_reason else None,
    )
    callback.save()
    record_audit_event(
        request,
        "mpesa_callback.processed" if not failure_reason else "mpesa_callback.failed",
        "MpesaCallback",
        new_values={"id": callback.pk, "transaction_code": callback.transaction_code, "status": callback.status},
    )
    return JsonResponse(
        {"callback": {"id": callback.pk, "transaction_code": callback.transaction_code, "status": callback.status}},
        status=202 if not failure_reason else 400,
    )


def _payment_payload(payment):
    receipt = getattr(payment, "receipt", None)
    return {
        "id": payment.pk,
        "tenant_id": payment.tenant_id,
        "amount": str(payment.amount),
        "reference": payment.reference,
        "source": payment.source,
        "account_reference": payment.account_reference,
        "payer_phone": payment.payer_phone,
        "received_at": payment.received_at.isoformat(),
        "status": payment.status,
        "instrument_status": payment.instrument_status,
        "cheque_number": payment.cheque_number,
        "cheque_bank": payment.cheque_bank,
        "clearing_date": payment.clearing_date.isoformat() if payment.clearing_date else None,
        "collected_by": payment.collected_by_id,
        "notes": payment.notes,
        "allocated_amount": str(payment.allocated_amount),
        "credit_amount": str(payment.credit_amount),
        "allocations": [
            {
                "invoice_id": allocation.invoice_id,
                "amount": str(allocation.amount),
            }
            for allocation in payment.allocations.all()
        ],
        "receipt": _receipt_payload(receipt) if receipt else None,
    }


def _payment_suggestions(payment, accessible_tenants):
    terms = [payment.account_reference.strip(), payment.payer_phone.strip()]
    terms = [term for term in terms if term]
    if not terms:
        return []
    query = models.Q()
    for term in terms:
        query |= (
            models.Q(phone=term)
            | models.Q(national_id_or_passport=term)
            | models.Q(company_registration_number=term)
            | models.Q(name__iexact=term)
            | models.Q(email__iexact=term)
        )
    return [
        {"tenant_id": tenant.pk, "tenant": tenant.name, "reason": "Matched payment reference"}
        for tenant in accessible_tenants.filter(query)[:5]
    ]


def _payment_balance_after(payment):
    return max(
        sum(
            (
                invoice.total - (
                    invoice.payment_allocations.aggregate(total=models.Sum("amount"))["total"]
                    or Decimal("0.00")
                )
                for invoice in Invoice.objects.filter(
                    tenancy__tenant_id=payment.tenant_id,
                    status=Invoice.STATUS_ISSUED,
                )
            ),
            Decimal("0.00"),
        ),
        Decimal("0.00"),
    )


def _receipt_payload(receipt):
    return {
        "id": receipt.pk,
        "number": receipt.number,
        "payment_id": receipt.payment_id,
        "tenant_id": receipt.tenant_id,
        "amount": str(receipt.amount),
        "balance_after": str(receipt.balance_after),
        "issued_at": receipt.issued_at.isoformat(),
    }


def _ensure_payment_receipt(payment):
    if not payment.tenant_id:
        return None
    receipt, created = Receipt.objects.get_or_create(
        payment=payment,
        defaults={
            "tenant_id": payment.tenant_id,
            "number": f"RCT-{payment.received_at.year}-{payment.pk:06d}",
            "amount": payment.amount,
            "balance_after": _payment_balance_after(payment),
        },
    )
    return receipt


def _normalise_statement_header(value):
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _statement_rows(upload):
    suffix = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
    if suffix == "csv":
        rows = csv.DictReader(StringIO(upload.read().decode("utf-8-sig")))
        headers = [_normalise_statement_header(value) for value in rows.fieldnames or []]
        return "csv", [
            {
                _normalise_statement_header(key): value
                for key, value in row.items()
            }
            for row in rows
        ], headers
    if suffix in {"xlsx", "xlsm"}:
        workbook = load_workbook(BytesIO(upload.read()), read_only=True, data_only=True)
        worksheet = workbook.active
        values = list(worksheet.values)
        if not values:
            return suffix, [], []
        headers = [_normalise_statement_header(value) for value in values[0]]
        return suffix, [
            {
                headers[index]: value
                for index, value in enumerate(row)
                if index < len(headers) and headers[index]
            }
            for row in values[1:]
        ], headers
    raise ValueError("Only CSV and XLSX bank statements are supported.")


def _statement_value(row, *names):
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return ""


def _statement_date(value):
    if hasattr(value, "date"):
        return value.date() if hasattr(value, "date") else value
    text = str(value or "").strip()
    if not text:
        return None
    for formatter in (date.fromisoformat, lambda item: datetime.strptime(item, "%d/%m/%Y").date()):
        try:
            return formatter(text)
        except ValueError:
            continue
    raise ValueError("transaction_date must be YYYY-MM-DD or DD/MM/YYYY.")


def _statement_row_payload(row):
    return {
        "id": row.pk,
        "row_number": row.row_number,
        "transaction_date": row.transaction_date.isoformat() if row.transaction_date else None,
        "reference": row.reference,
        "account_reference": row.account_reference,
        "payer_phone": row.payer_phone,
        "amount": str(row.amount) if row.amount is not None else None,
        "status": row.status,
        "validation_error": row.validation_error,
        "suggestions": row.suggestions,
        "payment_id": row.payment_id,
    }


@require_POST
def import_bank_statement(request):
    error = _property_access_error(request)
    if error:
        return error
    upload = request.FILES.get("file")
    if upload is None:
        return _problem(400, "Invalid statement", "Upload a CSV or XLSX file in the file field.", "invalid-statement")
    try:
        file_format, rows, headers = _statement_rows(upload)
    except (UnicodeDecodeError, ValueError):
        return _problem(400, "Invalid statement", "The statement format or encoding is not supported.", "invalid-statement")
    required_headers = {"reference", "amount"}
    if not required_headers.issubset(headers):
        return _problem(400, "Invalid statement", "The statement must contain reference and amount columns.", "invalid-statement")
    tenants = _accessible_tenants(request.user)
    with transaction.atomic():
        statement = BankStatementImport.objects.create(
            filename=upload.name,
            file_format=file_format,
            imported_by=request.user,
            total_rows=len(rows),
        )
        valid_rows = invalid_rows = 0
        for row_number, raw_row in enumerate(rows, start=2):
            reference = str(_statement_value(raw_row, "reference", "transaction_reference")).strip()
            account_reference = str(_statement_value(raw_row, "account_reference", "account", "bill_ref_number")).strip()
            payer_phone = str(_statement_value(raw_row, "payer_phone", "phone", "msisdn")).strip()
            error_message = ""
            amount = None
            transaction_date = None
            try:
                amount = Decimal(str(_statement_value(raw_row, "amount", "transaction_amount")))
                if amount <= 0:
                    raise ValueError("amount must be greater than zero.")
                transaction_date = _statement_date(_statement_value(raw_row, "transaction_date", "date"))
                if not reference:
                    raise ValueError("reference is required.")
                if Payment.objects.filter(reference=reference).exists():
                    raise ValueError("reference already exists.")
            except (InvalidOperation, TypeError, ValueError) as exc:
                error_message = str(exc)
            suggestions = []
            if not error_message:
                suggestion_query = models.Q()
                for term in (account_reference, payer_phone):
                    if term:
                        suggestion_query |= models.Q(phone=term) | models.Q(national_id_or_passport=term) | models.Q(name__iexact=term)
                suggestions = [
                    {"tenant_id": tenant.pk, "tenant": tenant.name, "reason": "Statement reference match"}
                    for tenant in tenants.filter(suggestion_query)[:5]
                ] if suggestion_query else []
            bank_row = BankStatementRow.objects.create(
                statement=statement,
                row_number=row_number,
                transaction_date=transaction_date,
                reference=reference,
                account_reference=account_reference,
                payer_phone=payer_phone,
                amount=amount,
                raw_data={str(key): str(value) for key, value in raw_row.items()},
                validation_error=error_message,
                suggestions=suggestions,
                status=BankStatementRow.STATUS_INVALID if error_message else BankStatementRow.STATUS_REVIEW,
            )
            if error_message:
                invalid_rows += 1
                continue
            payment = Payment.objects.create(
                amount=amount,
                reference=reference,
                source=Payment.SOURCE_BANK,
                account_reference=account_reference,
                payer_phone=payer_phone,
                received_at=timezone.make_aware(
                    datetime.combine(transaction_date or timezone.localdate(), datetime.min.time())
                ),
                status=Payment.STATUS_UNMATCHED,
            )
            bank_row.payment = payment
            bank_row.save(update_fields=["payment"])
            valid_rows += 1
        statement.valid_rows = valid_rows
        statement.invalid_rows = invalid_rows
        statement.save(update_fields=["valid_rows", "invalid_rows"])
    record_audit_event(
        request,
        "bank_statement.imported",
        "BankStatementImport",
        new_values={"id": statement.pk, "filename": statement.filename, "valid_rows": valid_rows, "invalid_rows": invalid_rows},
    )
    return JsonResponse(
        {
            "statement": {
                "id": statement.pk,
                "filename": statement.filename,
                "total_rows": statement.total_rows,
                "valid_rows": valid_rows,
                "invalid_rows": invalid_rows,
            }
        },
        status=201,
    )


@require_GET
def get_bank_statement_import(request, statement_id):
    error = _property_access_error(request)
    if error:
        return error
    statement = BankStatementImport.objects.filter(
        pk=statement_id,
        imported_by__profile__branches__in=_accessible_branches(request.user),
    ).prefetch_related("rows").first()
    if statement is None:
        return _problem(404, "Statement not found", "The statement is unavailable.", "statement-not-found")
    return JsonResponse(
        {
            "statement": {
                "id": statement.pk,
                "filename": statement.filename,
                "status": statement.status,
                "total_rows": statement.total_rows,
                "valid_rows": statement.valid_rows,
                "invalid_rows": statement.invalid_rows,
                "rows": [_statement_row_payload(row) for row in statement.rows.all()],
            }
        }
    )


@require_POST
def match_bank_statement_row(request, row_id):
    error = _property_access_error(request)
    if error:
        return error
    row = BankStatementRow.objects.select_related("payment").filter(
        pk=row_id,
        statement__imported_by__profile__branches__in=_accessible_branches(request.user),
    ).first()
    if row is None or row.payment_id is None:
        return _problem(404, "Statement row not found", "The row is unavailable or invalid.", "statement-row-not-found")
    response = allocate_payment(request, row.payment_id)
    if response.status_code == 200:
        row.status = BankStatementRow.STATUS_MATCHED
        row.save(update_fields=["status"])
        if not row.statement.rows.filter(status=BankStatementRow.STATUS_REVIEW).exists():
            row.statement.status = BankStatementImport.STATUS_REVIEWED
            row.statement.save(update_fields=["status"])
    return response


@require_GET
def bank_reconciliation_report(request, statement_id):
    error = _property_access_error(request)
    if error:
        return error
    statement = BankStatementImport.objects.filter(
        pk=statement_id,
        imported_by__profile__branches__in=_accessible_branches(request.user),
    ).first()
    if statement is None:
        return _problem(404, "Statement not found", "The statement is unavailable.", "statement-not-found")
    rows = statement.rows.all()
    totals = {
        "statement_amount": sum((row.amount or Decimal("0.00") for row in rows), Decimal("0.00")),
        "valid_amount": sum((row.amount or Decimal("0.00") for row in rows if row.status != BankStatementRow.STATUS_INVALID), Decimal("0.00")),
        "matched_amount": sum((row.amount or Decimal("0.00") for row in rows if row.status == BankStatementRow.STATUS_MATCHED), Decimal("0.00")),
    }
    return JsonResponse(
        {
            "statement_id": statement.pk,
            "row_counts": {
                "total": statement.total_rows,
                "valid": statement.valid_rows,
                "invalid": statement.invalid_rows,
                "matched": rows.filter(status=BankStatementRow.STATUS_MATCHED).count(),
                "review": rows.filter(status=BankStatementRow.STATUS_REVIEW).count(),
            },
            "totals": {key: str(value) for key, value in totals.items()},
        }
    )


@require_GET
def list_unmatched_payments(request):
    error = _property_access_error(request)
    if error:
        return error
    tenants = _accessible_tenants(request.user)
    payments = Payment.objects.filter(
        status__in={Payment.STATUS_UNMATCHED, Payment.STATUS_PARTIALLY_ALLOCATED},
    ).order_by("received_at", "id")
    return JsonResponse(
        {
            "payments": [
                {
                    **_payment_payload(payment),
                    "suggestions": _payment_suggestions(payment, tenants),
                }
                for payment in payments
            ]
        }
    )


@require_POST
def create_payment(request):
    error = _property_access_error(request)
    if error:
        return error
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "A JSON object is required.", "invalid-request")
    try:
        amount = Decimal(str(body.get("amount")))
    except (TypeError, ValueError, InvalidOperation):
        return _problem(400, "Invalid payment", "amount must be valid.", "invalid-payment")
    reference = body.get("reference")
    source = body.get("source", Payment.SOURCE_MANUAL)
    if not isinstance(reference, str) or not reference.strip():
        return _problem(400, "Invalid payment", "A payment reference is required.", "invalid-payment")
    if source not in dict(Payment.SOURCE_CHOICES) or amount <= 0:
        return _problem(400, "Invalid payment", "The source and amount must be valid.", "invalid-payment")
    tenant_id = body.get("tenant_id")
    tenant = None
    if tenant_id not in (None, ""):
        try:
            tenant = _accessible_tenants(request.user).filter(pk=int(tenant_id)).first()
        except (TypeError, ValueError):
            tenant = None
        if tenant is None:
            return _problem(404, "Tenant not found", "The tenant is unavailable or outside your access scope.", "tenant-not-found")
    received_at = timezone.now()
    try:
        with transaction.atomic():
            payment = Payment.objects.create(
                tenant=tenant,
                amount=amount,
                reference=reference.strip(),
                source=source,
                account_reference=str(body.get("account_reference") or "").strip(),
                payer_phone=str(body.get("payer_phone") or "").strip(),
                received_at=received_at,
                status=Payment.STATUS_UNMATCHED if tenant is None else Payment.STATUS_ALLOCATED,
            )
            if tenant is None:
                payment.save(update_fields=["status"])
                allocated = Decimal("0.00")
                credit = Decimal("0.00")
                remaining = amount
            else:
                invoice_ids = list(
                    Invoice.objects.select_for_update()
                    .filter(
                        tenancy__tenant=tenant,
                        status=Invoice.STATUS_ISSUED,
                    )
                    .filter(tenancy__unit__property__in=_accessible_properties(request.user))
                    .order_by("due_date", "period_start", "id")
                    .values_list("pk", flat=True)
                )
                invoices = (
                    Invoice.objects.filter(pk__in=invoice_ids)
                    .annotate(
                        paid=Coalesce(
                            models.Sum("payment_allocations__amount"),
                            Decimal("0.00"),
                        )
                    )
                    .order_by("due_date", "period_start", "id")
                )
                remaining = amount
                allocated = Decimal("0.00")
                for invoice in invoices:
                    outstanding = invoice.total - invoice.paid
                    if outstanding <= 0:
                        continue
                    allocation_amount = min(remaining, outstanding)
                    PaymentAllocation.objects.create(
                        payment=payment,
                        invoice=invoice,
                        amount=allocation_amount,
                    )
                    allocated += allocation_amount
                    remaining -= allocation_amount
                    if remaining == 0:
                        break
                credit = remaining
                payment.allocated_amount = allocated
                payment.credit_amount = credit
                payment.status = (
                    Payment.STATUS_CREDIT
                    if allocated == 0
                    else Payment.STATUS_PARTIALLY_ALLOCATED
                    if credit
                    else Payment.STATUS_ALLOCATED
                )
                payment.save(update_fields=["allocated_amount", "credit_amount", "status"])
                if credit:
                    TenantCredit.objects.create(
                        tenant=tenant,
                        payment=payment,
                        amount=credit,
                        remaining_amount=credit,
                    )
                _ensure_payment_receipt(payment)
            if tenant is None:
                payment.allocated_amount = allocated
                payment.credit_amount = credit
                payment.save(update_fields=["allocated_amount", "credit_amount"])
    except IntegrityError:
        return _problem(409, "Duplicate payment", "The payment reference already exists.", "duplicate-payment")
    record_audit_event(
        request,
        "payment.recorded",
        "Payment",
        new_values={
            "id": payment.pk,
            "tenant_id": tenant.pk if tenant else None,
            "amount": str(payment.amount),
            "reference": payment.reference,
            "allocated_amount": str(payment.allocated_amount),
            "credit_amount": str(payment.credit_amount),
        },
    )
    return JsonResponse({"payment": _payment_payload(payment)}, status=201)


@require_POST
def create_offline_payment(request):
    error = _waiver_manager_error(request)
    if error:
        return error
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "A JSON object is required.", "invalid-request")
    try:
        tenant = _accessible_tenants(request.user).get(pk=int(body.get("tenant_id")))
        amount = Decimal(str(body.get("amount")))
    except (Tenant.DoesNotExist, TypeError, ValueError, InvalidOperation):
        return _problem(400, "Invalid payment", "tenant_id and amount must be valid.", "invalid-payment")
    source = body.get("source")
    reference = body.get("reference")
    if source not in {Payment.SOURCE_CASH, Payment.SOURCE_CHEQUE} or amount <= 0:
        return _problem(400, "Invalid payment", "Use cash or cheque with a positive amount.", "invalid-payment")
    if not isinstance(reference, str) or not reference.strip():
        return _problem(400, "Invalid payment", "A payment reference is required.", "invalid-payment")
    if source == Payment.SOURCE_CHEQUE and not str(body.get("cheque_number") or "").strip():
        return _problem(400, "Invalid cheque", "cheque_number is required.", "invalid-cheque")
    if source == Payment.SOURCE_CASH and not body.get("collected_by"):
        return _problem(400, "Invalid cash payment", "collected_by is required.", "invalid-cash-payment")
    instrument_status = (
        Payment.INSTRUMENT_PENDING
        if source == Payment.SOURCE_CHEQUE
        else Payment.INSTRUMENT_COLLECTED
    )
    try:
        collector = (
            get_user_model().objects.filter(pk=body.get("collected_by")).first()
            if source == Payment.SOURCE_CASH
            else None
        )
        if source == Payment.SOURCE_CASH and collector is None:
            return _problem(400, "Invalid cash payment", "The collector does not exist.", "invalid-cash-payment")
        with transaction.atomic():
            payment = Payment.objects.create(
                tenant=tenant,
                amount=amount,
                reference=reference.strip(),
                source=source,
                account_reference=str(body.get("account_reference") or "").strip(),
                payer_phone=str(body.get("payer_phone") or "").strip(),
                received_at=timezone.now(),
                status=Payment.STATUS_UNMATCHED,
                instrument_status=instrument_status,
                cheque_number=str(body.get("cheque_number") or "").strip(),
                cheque_bank=str(body.get("cheque_bank") or "").strip(),
                collected_by=collector,
                notes=str(body.get("notes") or "").strip(),
            )
            invoice_ids = list(
                Invoice.objects.select_for_update()
                .filter(tenancy__tenant=tenant, status=Invoice.STATUS_ISSUED)
                .filter(tenancy__unit__property__in=_accessible_properties(request.user))
                .order_by("due_date", "period_start", "id")
                .values_list("pk", flat=True)
            )
            invoices = (
                Invoice.objects.filter(pk__in=invoice_ids)
                .annotate(paid=Coalesce(models.Sum("payment_allocations__amount"), Decimal("0.00")))
                .order_by("due_date", "period_start", "id")
            )
            remaining = amount
            allocated = Decimal("0.00")
            for invoice in invoices:
                outstanding = invoice.total - invoice.paid
                if outstanding <= 0:
                    continue
                allocation_amount = min(remaining, outstanding)
                PaymentAllocation.objects.create(payment=payment, invoice=invoice, amount=allocation_amount)
                allocated += allocation_amount
                remaining -= allocation_amount
                if not remaining:
                    break
            payment.allocated_amount = allocated
            payment.credit_amount = remaining
            payment.status = (
                Payment.STATUS_CREDIT
                if not allocated
                else Payment.STATUS_PARTIALLY_ALLOCATED
                if remaining
                else Payment.STATUS_ALLOCATED
            )
            payment.save(update_fields=["allocated_amount", "credit_amount", "status"])
            if remaining:
                TenantCredit.objects.create(
                    tenant=tenant,
                    payment=payment,
                    amount=remaining,
                    remaining_amount=remaining,
                )
            _ensure_payment_receipt(payment)
    except IntegrityError:
        return _problem(409, "Duplicate payment", "The payment reference already exists.", "duplicate-payment")
    record_audit_event(
        request,
        "offline_payment.recorded",
        "Payment",
        new_values={
            "id": payment.pk,
            "source": payment.source,
            "amount": str(payment.amount),
            "instrument_status": payment.instrument_status,
        },
    )
    return JsonResponse({"payment": _payment_payload(payment)}, status=201)


@require_POST
def update_cheque_status(request, payment_id):
    error = _waiver_manager_error(request)
    if error:
        return error
    payment = Payment.objects.filter(
        pk=payment_id,
        source=Payment.SOURCE_CHEQUE,
        tenant__branch__in=_accessible_branches(request.user),
    ).first()
    if payment is None:
        return _problem(404, "Cheque not found", "The cheque payment is unavailable.", "cheque-not-found")
    body = _json_body(request)
    status = body.get("status") if body else None
    if status not in {Payment.INSTRUMENT_CLEARED, Payment.INSTRUMENT_BOUNCED}:
        return _problem(400, "Invalid cheque status", "Status must be cleared or bounced.", "invalid-cheque-status")
    payment.instrument_status = status
    payment.clearing_date = timezone.localdate()
    payment.save(update_fields=["instrument_status", "clearing_date"])
    record_audit_event(
        request,
        "cheque.status_updated",
        "Payment",
        new_values={"id": payment.pk, "status": status, "clearing_date": payment.clearing_date.isoformat()},
    )
    return JsonResponse({"payment": _payment_payload(payment)})


@require_GET
def cash_collection_report(request):
    error = _waiver_manager_error(request)
    if error:
        return error
    try:
        start = date.fromisoformat(request.GET.get("from", timezone.localdate().isoformat()))
        end = date.fromisoformat(request.GET.get("to", start.isoformat()))
    except ValueError:
        return _problem(400, "Invalid report range", "from and to must be valid dates.", "invalid-report-range")
    if end < start:
        return _problem(400, "Invalid report range", "to cannot be before from.", "invalid-report-range")
    payments = Payment.objects.filter(
        source=Payment.SOURCE_CASH,
        tenant__branch__in=_accessible_branches(request.user),
        received_at__date__range=(start, end),
    )
    total = payments.aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")
    return JsonResponse(
        {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "count": payments.count(),
            "total": str(total),
            "payments": [_payment_payload(payment) for payment in payments],
        }
    )


@require_GET
def daily_reconciliation_report(request):
    error = _property_access_error(request)
    if error:
        return error
    today = timezone.localdate()
    try:
        from_date = date.fromisoformat(request.GET.get("from", today.isoformat()))
        to_date = date.fromisoformat(request.GET.get("to", from_date.isoformat()))
    except ValueError:
        return _problem(400, "Invalid request", "from and to must be valid dates.", "invalid-request")
    if from_date > to_date:
        return _problem(400, "Invalid request", "from cannot be after to.", "invalid-request")

    branches = _accessible_branches(request.user)
    payments = (
        Payment.objects.filter(
            Q(tenant__branch__in=branches)
            | Q(bank_statement_row__statement__imported_by__profile__branches__in=branches),
            received_at__date__range=(from_date, to_date),
        )
        .distinct()
        .order_by("source", "received_at", "id")
    )
    channel_totals = {}
    totals = {
        "received": Decimal("0.00"),
        "matched": Decimal("0.00"),
        "unmatched": Decimal("0.00"),
        "reversed": Decimal("0.00"),
        "allocated": Decimal("0.00"),
    }
    payment_rows = []
    for payment in payments:
        reversed_amount = (
            payment.amount
            if payment.instrument_status == Payment.INSTRUMENT_BOUNCED
            else Decimal("0.00")
        )
        matched_amount = (
            Decimal("0.00") if reversed_amount else payment.allocated_amount
        )
        unmatched_amount = (
            Decimal("0.00")
            if reversed_amount
            else max(
                payment.amount - payment.allocated_amount - payment.credit_amount,
                Decimal("0.00"),
            )
        )
        allocated_amount = (
            payment.amount
            if payment.status == Payment.STATUS_ALLOCATED and not reversed_amount
            else Decimal("0.00")
        )
        values = {
            "received": payment.amount,
            "matched": matched_amount,
            "unmatched": unmatched_amount,
            "reversed": reversed_amount,
            "allocated": allocated_amount,
        }
        channel = channel_totals.setdefault(
            payment.source,
            {
                "received": Decimal("0.00"),
                "matched": Decimal("0.00"),
                "unmatched": Decimal("0.00"),
                "reversed": Decimal("0.00"),
                "allocated": Decimal("0.00"),
                "payment_count": 0,
            },
        )
        channel["payment_count"] += 1
        for key, value in values.items():
            totals[key] += value
            channel[key] += value
        payment_rows.append(
            {
                "id": payment.pk,
                "reference": payment.reference,
                "source": payment.source,
                "status": payment.status,
                "instrument_status": payment.instrument_status,
                "received_at": payment.received_at.isoformat(),
                **{key: str(value) for key, value in values.items()},
            }
        )

    def serialize(values):
        return {
            **{
                key: str(value)
                for key, value in values.items()
                if key != "payment_count"
            },
            **({"payment_count": values["payment_count"]} if "payment_count" in values else {}),
        }

    return JsonResponse(
        {
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "totals": serialize(totals),
            "channels": {
                source: serialize(values)
                for source, values in sorted(channel_totals.items())
            },
            "payments": payment_rows,
        }
    )


def _deposit_payload(account):
    return {
        "id": account.pk,
        "tenancy_id": account.tenancy_id,
        "tenant_id": account.tenancy.tenant_id,
        "property_id": account.tenancy.unit.property_id,
        "landlord_id": account.tenancy.unit.property.landlord_id,
        "target_amount": str(account.target_amount),
        "balance": str(account.balance),
        "top_up_amount": str(max(account.target_amount - account.balance, Decimal("0.00"))),
        "entries": [
            {
                "id": entry.pk,
                "entry_type": entry.entry_type,
                "amount": str(entry.amount),
                "reference": entry.reference,
                "description": entry.description,
                "created_at": entry.created_at.isoformat(),
            }
            for entry in account.entries.all()
        ],
    }


def _deposit_finance_error(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not requires_finance_mfa(request.user):
        return _problem(
            403,
            "Permission denied",
            "Only finance-authorized staff can record deposit movements.",
            "deposit-access-denied",
        )
    return None


@require_GET
def get_deposit_account(request, tenancy_id):
    error = _property_access_error(request)
    if error:
        return error
    account = (
        DepositAccount.objects.filter(
            tenancy_id=tenancy_id,
            tenancy__unit__property__in=_accessible_properties(request.user),
        )
        .select_related("tenancy__tenant", "tenancy__unit__property")
        .prefetch_related("entries")
        .first()
    )
    if account is None:
        return _problem(404, "Deposit account not found", "The tenancy has no deposit ledger.", "deposit-not-found")
    return JsonResponse({"deposit": _deposit_payload(account)})


@require_POST
def create_deposit_entry(request, tenancy_id):
    error = _deposit_finance_error(request)
    if error:
        return error
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "A JSON object is required.", "invalid-request")
    try:
        amount = Decimal(str(body.get("amount")))
    except (TypeError, ValueError, InvalidOperation):
        return _problem(400, "Invalid deposit entry", "amount must be a valid positive number.", "invalid-deposit-entry")
    entry_type = body.get("entry_type", DepositEntry.TYPE_RECEIPT)
    reference = body.get("reference")
    if amount <= 0 or entry_type not in dict(DepositEntry.TYPE_CHOICES):
        return _problem(400, "Invalid deposit entry", "The entry type and amount must be valid.", "invalid-deposit-entry")
    if not isinstance(reference, str) or not reference.strip():
        return _problem(400, "Invalid deposit entry", "A unique reference is required.", "invalid-deposit-entry")
    tenancy = Tenancy.objects.filter(
        pk=tenancy_id,
        unit__property__in=_accessible_properties(request.user),
    ).first()
    if tenancy is None:
        return _problem(404, "Tenancy not found", "The tenancy is unavailable or outside your access scope.", "tenancy-not-found")
    try:
        with transaction.atomic():
            account, _ = DepositAccount.objects.select_for_update().get_or_create(
                tenancy=tenancy,
                defaults={"target_amount": amount},
            )
            if "target_amount" in body:
                target_amount = Decimal(str(body["target_amount"]))
                if target_amount < 0:
                    return _problem(400, "Invalid deposit target", "target_amount cannot be negative.", "invalid-deposit-target")
                account.target_amount = target_amount
            if entry_type in {DepositEntry.TYPE_REFUND}:
                if amount > account.balance:
                    return _problem(400, "Invalid refund", "A refund cannot exceed the deposit balance.", "invalid-refund")
                account.balance -= amount
            else:
                account.balance += amount
            entry = DepositEntry.objects.create(
                account=account,
                entry_type=entry_type,
                amount=amount,
                reference=reference.strip(),
                description=str(body.get("description") or "").strip(),
                created_by=request.user,
            )
            account.save(update_fields=["target_amount", "balance", "updated_at"])
    except IntegrityError:
        return _problem(409, "Duplicate deposit entry", "The deposit reference already exists.", "duplicate-deposit-entry")
    record_audit_event(
        request,
        "deposit.entry_created",
        "DepositEntry",
        new_values={
            "id": entry.pk,
            "tenancy_id": tenancy_id,
            "entry_type": entry.entry_type,
            "amount": str(entry.amount),
            "balance": str(account.balance),
        },
    )
    account = DepositAccount.objects.prefetch_related("entries").get(pk=account.pk)
    return JsonResponse({"deposit": _deposit_payload(account)}, status=201)


@require_POST
def allocate_payment(request, payment_id):
    error = _waiver_manager_error(request)
    if error:
        return error
    payment = Payment.objects.filter(pk=payment_id).first()
    if payment is None:
        return _problem(404, "Payment not found", "The payment is unavailable.", "payment-not-found")
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "A JSON object is required.", "invalid-request")
    try:
        invoice_id = int(body.get("invoice_id"))
        amount = Decimal(str(body.get("amount", payment.amount - payment.allocated_amount)))
    except (TypeError, ValueError, InvalidOperation):
        return _problem(400, "Invalid allocation", "invoice_id and amount must be valid.", "invalid-allocation")
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        invoice = (
            Invoice.objects.select_for_update()
            .filter(
                pk=invoice_id,
                status=Invoice.STATUS_ISSUED,
                tenancy__unit__property__in=_accessible_properties(request.user),
            )
            .select_related("tenancy__tenant")
            .first()
        )
        if invoice is None:
            return _problem(404, "Invoice not found", "The invoice is unavailable or outside your access scope.", "invoice-not-found")
        invoice.paid = PaymentAllocation.objects.filter(invoice_id=invoice.pk).aggregate(
            total=Coalesce(models.Sum("amount"), Decimal("0.00"))
        )["total"]
        if payment.tenant_id and payment.tenant_id != invoice.tenancy.tenant_id:
            return _problem(400, "Invalid allocation", "The invoice does not belong to this payment's tenant.", "tenant-mismatch")
        outstanding = invoice.total - invoice.paid
        unallocated = payment.amount - payment.allocated_amount
        if amount <= 0 or amount > outstanding or amount > unallocated:
            return _problem(400, "Invalid allocation", "The allocation exceeds the available payment or invoice balance.", "invalid-allocation")
        if payment.tenant_id is None:
            payment.tenant = invoice.tenancy.tenant
        PaymentAllocation.objects.create(payment=payment, invoice=invoice, amount=amount)
        payment.allocated_amount += amount
        remaining = payment.amount - payment.allocated_amount
        payment.credit_amount = remaining if payment.tenant_id else Decimal("0.00")
        payment.status = (
            Payment.STATUS_ALLOCATED
            if remaining == 0
            else Payment.STATUS_PARTIALLY_ALLOCATED
        )
        payment.save(update_fields=["tenant", "allocated_amount", "credit_amount", "status"])
        if payment.tenant_id and remaining:
            credit, _ = TenantCredit.objects.get_or_create(
                tenant=payment.tenant,
                payment=payment,
                defaults={"amount": remaining, "remaining_amount": remaining},
            )
            if credit.amount != remaining or credit.remaining_amount != remaining:
                credit.amount = remaining
                credit.remaining_amount = remaining
                credit.save(update_fields=["amount", "remaining_amount"])
        elif payment.tenant_id:
            TenantCredit.objects.filter(payment=payment).update(remaining_amount=Decimal("0.00"))
        _ensure_payment_receipt(payment)
    record_audit_event(
        request,
        "payment.manually_allocated",
        "Payment",
        new_values={"id": payment.pk, "invoice_id": invoice.pk, "amount": str(amount)},
    )
    return JsonResponse({"payment": _payment_payload(payment)})


@require_GET
def get_payment_receipt(request, payment_id):
    error = _property_access_error(request)
    if error:
        return error
    receipt = (
        Receipt.objects.filter(
            payment_id=payment_id,
            tenant__branch__in=_accessible_branches(request.user),
        )
        .select_related("payment", "tenant")
        .first()
    )
    if receipt is None:
        return _problem(404, "Receipt not found", "The receipt is unavailable or the payment is unmatched.", "receipt-not-found")
    response = JsonResponse({"receipt": _receipt_payload(receipt)})
    response["Content-Disposition"] = f'attachment; filename="{receipt.number}.json"'
    return response


def _history_audit_payload(event):
    return {
        "id": event.pk,
        "action": event.action,
        "entity": event.entity,
        "old_values": event.old_values,
        "new_values": event.new_values,
        "user_id": event.user_id,
        "timestamp": event.timestamp.isoformat(),
    }


def _move_in_package_payload(package):
    charges = list(package.charges.all())
    return {
        "id": package.pk,
        "tenancy_id": package.tenancy_id,
        "agreement_reference": package.agreement_reference,
        "generated_at": package.generated_at.isoformat(),
        "charges": [
            {
                "id": charge.pk,
                "charge_type": charge.charge_type,
                "description": charge.description,
                "amount": str(charge.amount),
                "calculation": charge.calculation,
            }
            for charge in charges
        ],
        "total": str(sum((charge.amount for charge in charges), Decimal("0.00"))),
        "checklist": [
            {
                "id": item.pk,
                "title": item.title,
                "status": item.status,
                "notes": item.notes,
                "completed_at": (
                    item.completed_at.isoformat() if item.completed_at else None
                ),
            }
            for item in package.checklist_items.all()
        ],
    }


def _pdf_escape(value):
    return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _activation_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_ke_phone(phone):
    value = re.sub(r"[\s()+-]", "", str(phone or ""))
    if value.startswith("0") and len(value) == 10:
        value = f"254{value[1:]}"
    if not re.fullmatch(r"254[17]\d{8}", value):
        raise ValueError("Use a Kenyan mobile number such as 0712345678 or 254712345678.")
    return value


def _agreement_pdf(tenancy, package):
    lines = [
        "MOVE-IN TENANCY AGREEMENT",
        f"Reference: {package.agreement_reference}",
        f"Tenant: {tenancy.tenant.name}",
        f"Unit: {tenancy.unit.code}",
        f"Property: {tenancy.unit.property.name}",
        f"Start date: {tenancy.start_date.isoformat()}",
        f"End date: {tenancy.end_date.isoformat() if tenancy.end_date else 'Open-ended'}",
        f"Due day: {tenancy.due_day}",
        f"Notice period: {tenancy.notice_period_days} days",
        "",
        "MOVE-IN CHARGES",
    ]
    for charge in package.charges.all():
        lines.append(f"{charge.description}: KES {charge.amount}")
    lines.append(f"Total: KES {_move_in_package_payload(package)['total']}")
    lines.extend(["", "This document records the configured move-in terms and charges."])
    stream_lines = ["BT", "/F1 11 Tf", "50 790 Td"]
    for index, line in enumerate(lines):
        if index:
            stream_lines.append("0 -16 Td")
        stream_lines.append(f"({_pdf_escape(line)}) Tj")
    stream_lines.append("ET")
    stream = "\n".join(stream_lines).encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode())
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(pdf)


def _tenant_audit_values(tenant):
    values = _tenant_payload(tenant)
    values["national_id_or_passport"] = (
        "[redacted]" if tenant.national_id_or_passport else None
    )
    values["kra_pin"] = "[redacted]" if tenant.kra_pin else None
    return values


def _redacted_payout_details(details):
    return {key: "[redacted]" for key in details}


def _landlord_audit_values(landlord):
    return {
        "id": landlord.pk,
        "branch_id": landlord.branch_id,
        "name": landlord.name,
        "id_or_registration_number": landlord.id_or_registration_number,
        "kra_pin": "[redacted]" if landlord.kra_pin else None,
        "phone": landlord.phone,
        "email": landlord.email,
        "payout_details": _redacted_payout_details(landlord.payout_details),
    }


def _agreement_audit_values(agreement):
    values = _agreement_payload(agreement)
    values["landlord_id"] = agreement.landlord_id
    return values


def _user_payload(user):
    profile = getattr(user, "profile", None)
    if profile is None:
        name = user.get_full_name() or user.get_username()
        phone = None
        branches = Branch.objects.none()
    else:
        name = profile.name
        phone = profile.phone or None
        branches = profile.accessible_branches()
    return {
        "id": user.pk,
        "name": name,
        "email": user.email or None,
        "phone": phone,
        "roles": list(user.groups.order_by("name").values_list("name", flat=True)),
        "branches": list(branches.order_by("name").values("id", "name", "town")),
    }


@require_POST
def register_landlord_portal(request):
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    name = str(body.get("name") or "").strip()
    email = str(body.get("email") or "").strip().lower()
    phone = str(body.get("phone") or "").strip()
    password = body.get("password")
    property_name = str(body.get("property_name") or "").strip()
    if not name or not email or not phone or not property_name or not isinstance(password, str):
        return _problem(400, "Invalid request", "name, email, phone, property_name, and password are required.", "invalid-request")
    if len(password) < 12:
        return _problem(400, "Invalid password", "Password must contain at least 12 characters.", "invalid-password")
    try:
        validate_email(email)
        phone = _normalize_ke_phone(phone)
        password_validation.validate_password(password)
    except (ValidationError, ValueError) as error:
        detail = "; ".join(error.messages) if isinstance(error, ValidationError) else str(error)
        return _problem(400, "Invalid registration", detail, "invalid-registration")
    user_model = get_user_model()
    if user_model.objects.filter(Q(username__iexact=email) | Q(email__iexact=email)).exists():
        return _problem(409, "Account exists", "An account already exists for this email.", "account-exists")
    branch = Branch.objects.order_by("pk").first()
    if branch is None:
        return _problem(503, "Registration unavailable", "No registration branch is configured.", "registration-unavailable")
    try:
        with transaction.atomic():
            user = user_model.objects.create_user(username=email, email=email, password=password, is_active=True)
            profile = UserProfile.objects.create(user=user, name=name, phone=phone)
            profile.full_clean()
            profile.branches.add(branch)
            user.groups.add(Group.objects.get(name=LANDLORD_PORTAL_ROLE))
            landlord = Landlord.objects.create(
                branch=branch,
                portal_user=user,
                name=name,
                id_or_registration_number=f"PORTAL-{user.pk}",
                phone=phone,
                email=email,
            )
            property_obj = Property.objects.create(
                branch=branch,
                landlord=landlord,
                name=property_name,
                property_type="residential",
                location=branch.town,
            )
        login(request, user, backend="users.authentication.EmailOrPhoneBackend")
    except (ValidationError, IntegrityError, Group.DoesNotExist) as error:
        return _problem(400, "Invalid registration", str(error), "invalid-registration")
    _record_auth_event(request, "portal_registration.succeeded", user)
    return JsonResponse({"user": _user_payload(user), "property": _property_payload(property_obj)}, status=201)


@require_GET
@ensure_csrf_cookie
def csrf_token(request):
    return JsonResponse({"csrfToken": get_token(request)})


@require_POST
def login_view(request):
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")

    identifier = body.get("identifier")
    password = body.get("password")
    if not isinstance(identifier, str) or not isinstance(password, str):
        return _problem(
            400,
            "Invalid request",
            "Both identifier and password are required.",
            "invalid-request",
        )

    user = authenticate(request, username=identifier.strip(), password=password)
    if user is None:
        _record_auth_event(request, "login.failed")
        return _problem(
            401,
            "Authentication failed",
            "The supplied credentials are invalid.",
            "authentication-failed",
        )

    if requires_finance_mfa(user):
        profile = getattr(user, "profile", None)
        if profile is None or not profile.phone:
            _record_auth_event(request, "login.mfa_unavailable", user)
            return _problem(
                403,
                "Two-factor authentication unavailable",
                "A phone number is required for this account's finance-access roles.",
                "mfa-contact-required",
            )
        code = f"{secrets.randbelow(1_000_000):06d}"
        with transaction.atomic():
            challenge = SMSLoginChallenge.objects.create(
                user=user,
                code_hash=make_password(code),
                expires_at=timezone.now()
                + timedelta(seconds=settings.AUTH_SMS_CODE_LIFETIME_SECONDS),
            )
            send_sms(
                profile.phone,
                f"Your Legacy Real Estate sign-in code is {code}.",
            )
        _record_auth_event(request, "login.mfa_challenge_issued", user)

        return JsonResponse(
            {
                "mfa_required": True,
                "challenge_id": challenge.pk,
            },
            status=202,
        )

    login(request, user, backend="users.authentication.EmailOrPhoneBackend")
    _record_auth_event(request, "login.succeeded", user)
    return JsonResponse({"user": _user_payload(user)})


@require_POST
def verify_sms_challenge(request):
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")

    challenge_id = body.get("challenge_id")
    code = body.get("code")
    if (
        not isinstance(challenge_id, int)
        or isinstance(challenge_id, bool)
        or not isinstance(code, str)
        or len(code) != 6
        or not code.isascii()
        or not code.isdigit()
    ):
        return _problem(
            400,
            "Invalid request",
            "A challenge_id and six-digit code are required.",
            "invalid-request",
        )

    response = None
    user = None
    with transaction.atomic():
        try:
            challenge = SMSLoginChallenge.objects.select_for_update().select_related(
                "user"
            ).get(
                pk=challenge_id,
                user__is_active=True,
            )
        except SMSLoginChallenge.DoesNotExist:
            _record_auth_event(request, "login.mfa_failed")
            response = _problem(
                401,
                "Invalid challenge",
                "The challenge is invalid.",
                "invalid-mfa",
            )
        else:
            user = challenge.user
            if challenge.is_expired():
                _record_auth_event(request, "login.mfa_failed", user)
                challenge.delete()
                response = _problem(
                    401,
                    "Expired challenge",
                    "Request a new sign-in code.",
                    "expired-mfa",
                )
            elif challenge.attempts >= settings.AUTH_SMS_MAX_ATTEMPTS:
                _record_auth_event(request, "login.mfa_failed", user)
                challenge.delete()
                response = _problem(
                    401,
                    "Invalid challenge",
                    "Request a new sign-in code.",
                    "invalid-mfa",
                )
            elif not check_password(code, challenge.code_hash):
                challenge.attempts += 1
                challenge.save(update_fields=["attempts"])
                _record_auth_event(request, "login.mfa_failed", user)
                if challenge.attempts >= settings.AUTH_SMS_MAX_ATTEMPTS:
                    challenge.delete()
                response = _problem(
                    401,
                    "Invalid code",
                    "The sign-in code is invalid.",
                    "invalid-mfa",
                )
            else:
                challenge.delete()

    if response is not None:
        return response

    login(request, user, backend="users.authentication.EmailOrPhoneBackend")
    _record_auth_event(request, "login.succeeded", user)
    return JsonResponse({"user": _user_payload(user)})


@require_POST
def logout_view(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    user = request.user
    logout(request)
    _record_auth_event(request, "logout.succeeded", user)
    return JsonResponse({"status": "ok"})


@require_GET
def current_user(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    return JsonResponse({"user": _user_payload(request.user)})


@require_GET
def list_branches(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    branches = _accessible_branches(request.user)
    return JsonResponse(
        {"branches": list(branches.order_by("name").values("id", "name", "town"))}
    )


@require_GET
def get_branch(request, branch_id):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    branch = get_object_or_404(_accessible_branches(request.user), pk=branch_id)
    return JsonResponse(
        {"branch": {"id": branch.pk, "name": branch.name, "town": branch.town}}
    )


@require_GET
def list_landlords(request):
    if not request.user.is_authenticated:
        return _problem(
            401,
            "Authentication required",
            "Sign in first.",
            "authentication-required",
        )
    if not _has_landlord_management_access(request.user):
        return _problem(
            403,
            "Permission denied",
            "You do not have access to landlord records.",
            "landlord-access-denied",
        )

    landlords = _accessible_landlords(request.user).prefetch_related(
        "management_agreements"
    )
    return JsonResponse({"landlords": [_landlord_payload(item) for item in landlords]})


@require_GET
def get_landlord(request, landlord_id):
    if not request.user.is_authenticated:
        return _problem(
            401,
            "Authentication required",
            "Sign in first.",
            "authentication-required",
        )
    if not _has_landlord_management_access(request.user):
        return _problem(
            403,
            "Permission denied",
            "You do not have access to landlord records.",
            "landlord-access-denied",
        )

    landlord = _accessible_landlords(request.user).prefetch_related(
        "management_agreements"
    ).filter(pk=landlord_id).first()
    if landlord is None:
        return _problem(
            404,
            "Landlord not found",
            "The landlord is unavailable or outside your access scope.",
            "landlord-not-found",
        )
    return JsonResponse({"landlord": _landlord_payload(landlord)})


@require_POST
def create_landlord(request):
    if not request.user.is_authenticated:
        return _problem(
            401,
            "Authentication required",
            "Sign in first.",
            "authentication-required",
        )
    if not _has_landlord_management_access(request.user):
        return _problem(
            403,
            "Permission denied",
            "You do not have access to landlord records.",
            "landlord-access-denied",
        )

    body = _json_body(request)
    required = ("name", "id_or_registration_number", "phone")
    if body is None or any(
        not isinstance(body.get(field), str) or not body[field].strip()
        for field in required
    ):
        return _problem(
            400,
            "Invalid request",
            "branch_id, name, id_or_registration_number, and phone are required.",
            "invalid-request",
        )

    try:
        branch_id = int(body["branch_id"])
    except (TypeError, ValueError):
        return _problem(400, "Invalid request", "branch_id must be an integer.", "invalid-request")

    profile = getattr(request.user, "profile", None)
    if profile is None:
        return _problem(
            403,
            "Permission denied",
            "A user profile is required to manage landlord records.",
            "landlord-access-denied",
        )
    branch = profile.accessible_branches().filter(pk=branch_id).first()
    if branch is None:
        return _problem(
            404,
            "Branch not found",
            "The branch is unavailable or outside your access scope.",
            "branch-not-found",
        )

    payout_details = body.get("payout_details") or {}
    if not isinstance(payout_details, dict):
        return _problem(
            400,
            "Invalid request",
            "payout_details must be an object.",
            "invalid-request",
        )

    landlord = Landlord(
        branch=branch,
        name=body["name"].strip(),
        id_or_registration_number=body["id_or_registration_number"].strip(),
        kra_pin=(body.get("kra_pin") or "").strip(),
        phone=body["phone"].strip(),
        email=(body.get("email") or "").strip(),
        payout_details=payout_details,
    )
    try:
        landlord.full_clean()
        landlord.save()
    except (ValidationError, IntegrityError) as error:
        detail = (
            error.message_dict
            if isinstance(error, ValidationError)
            else "A landlord with this identity already exists in the branch."
        )
        return JsonResponse(
            {
                "type": "https://legacy.example/problems/invalid-landlord",
                "title": "Invalid landlord",
                "status": 400,
                "detail": detail,
            },
            status=400,
            content_type="application/problem+json",
        )

    record_audit_event(
        request,
        "landlord.created",
        "Landlord",
        new_values=_landlord_audit_values(landlord),
    )
    return JsonResponse({"landlord": _landlord_payload(landlord)}, status=201)


@require_POST
def create_management_agreement(request, landlord_id):
    if not request.user.is_authenticated:
        return _problem(
            401,
            "Authentication required",
            "Sign in first.",
            "authentication-required",
        )
    if not _has_landlord_management_access(request.user):
        return _problem(
            403,
            "Permission denied",
            "You do not have access to landlord records.",
            "landlord-access-denied",
        )

    landlord = _accessible_landlords(request.user).filter(pk=landlord_id).first()
    if landlord is None:
        return _problem(
            404,
            "Landlord not found",
            "The landlord is unavailable or outside your access scope.",
            "landlord-not-found",
        )

    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    try:
        effective_from = date.fromisoformat(body["effective_from"])
        effective_to = (
            date.fromisoformat(body["effective_to"])
            if body.get("effective_to")
            else None
        )
        commission_rate = _decimal_value(body, "commission_rate")
        fixed_fee = _decimal_value(body, "fixed_fee")
    except (KeyError, TypeError, ValueError) as error:
        return _problem(400, "Invalid request", str(error), "invalid-request")

    commission_model = body.get("commission_model")
    if not isinstance(commission_model, str) or not commission_model.strip():
        return _problem(
            400,
            "Invalid request",
            "commission_model is required.",
            "invalid-request",
        )

    payout_day = body.get("payout_day")
    if payout_day not in (None, ""):
        try:
            payout_day = int(payout_day)
        except (TypeError, ValueError):
            return _problem(
                400,
                "Invalid request",
                "payout_day must be an integer.",
                "invalid-request",
            )
    mri_enabled = body.get("mri_enabled", False)
    if not isinstance(mri_enabled, bool):
        return _problem(
            400,
            "Invalid request",
            "mri_enabled must be a boolean.",
            "invalid-request",
        )
    terms = body.get("terms") or {}
    if not isinstance(terms, dict):
        return _problem(400, "Invalid request", "terms must be an object.", "invalid-request")

    agreement = ManagementAgreement(
        landlord=landlord,
        effective_from=effective_from,
        effective_to=effective_to,
        commission_model=commission_model.strip(),
        commission_rate=commission_rate,
        fixed_fee=fixed_fee,
        payout_day=payout_day,
        mri_enabled=mri_enabled,
        terms=terms,
    )
    try:
        agreement.full_clean()
        agreement.save()
    except ValidationError as error:
        return JsonResponse(
            {
                "type": "https://legacy.example/problems/invalid-agreement",
                "title": "Invalid management agreement",
                "status": 400,
                "detail": error.message_dict,
            },
            status=400,
            content_type="application/problem+json",
        )

    record_audit_event(
        request,
        "management_agreement.created",
        "ManagementAgreement",
        new_values=_agreement_audit_values(agreement),
    )
    return JsonResponse({"agreement": _agreement_payload(agreement)}, status=201)


def _property_access_error(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not (_has_landlord_management_access(request.user) or _is_caretaker(request.user)):
        return _problem(
            403,
            "Permission denied",
            "You do not have access to property records.",
            "property-access-denied",
        )
    return None


def _caretaker_management_error(request):
    error = _property_access_error(request)
    if error:
        return error
    if not _has_landlord_management_access(request.user):
        return _problem(
            403,
            "Permission denied",
            "Only authorized property staff can manage caretaker accounts and assignments.",
            "caretaker-management-denied",
        )
    return None


def _caretaker_dashboard_error(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not _is_caretaker(request.user):
        return _problem(
            403,
            "Permission denied",
            "Only caretaker accounts can access the caretaker dashboard.",
            "caretaker-dashboard-denied",
        )
    return None


def _caretaker_payload(user):
    profile = getattr(user, "profile", None)
    return {
        "id": user.pk,
        "email": user.email,
        "is_active": user.is_active,
        "name": profile.name if profile else user.get_full_name(),
        "phone": profile.phone if profile else "",
        "branch_ids": list(profile.branches.order_by("pk").values_list("pk", flat=True)) if profile else [],
    }


def _caretaker_assignment_payload(assignment):
    return {
        "id": assignment.pk,
        "property_id": assignment.property_id,
        "caretaker": _caretaker_payload(assignment.caretaker),
        "assigned_by": assignment.assigned_by_id,
        "assigned_at": assignment.assigned_at.isoformat(),
        "ended_at": assignment.ended_at.isoformat() if assignment.ended_at else None,
        "ended_by": assignment.ended_by_id,
        "active": assignment.is_active,
    }


@require_POST
def create_caretaker(request):
    error = _caretaker_management_error(request)
    if error:
        return error
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "A JSON object is required.", "invalid-request")
    email = body.get("email")
    name = body.get("name")
    phone = body.get("phone", "")
    password = body.get("password")
    branch_ids = body.get("branch_ids")
    if (
        not isinstance(email, str)
        or not email.strip()
        or not isinstance(name, str)
        or not name.strip()
        or not isinstance(password, str)
        or len(password) < 12
        or not isinstance(branch_ids, list)
        or not branch_ids
    ):
        return _problem(
            400,
            "Invalid caretaker",
            "email, name, a password of at least 12 characters, and branch_ids are required.",
            "invalid-caretaker",
        )
    try:
        branch_ids = [int(value) for value in branch_ids]
    except (TypeError, ValueError):
        return _problem(400, "Invalid caretaker", "branch_ids must contain integers.", "invalid-caretaker")
    branches = _accessible_branches(request.user).filter(pk__in=branch_ids)
    if branches.count() != len(set(branch_ids)):
        return _problem(
            400,
            "Invalid branches",
            "Every branch must be within your access scope.",
            "invalid-caretaker-branches",
        )
    user_model = get_user_model()
    if user_model.objects.filter(username=email.strip()).exists():
        return _problem(409, "Caretaker exists", "A user with this email already exists.", "caretaker-exists")
    try:
        with transaction.atomic():
            user = user_model.objects.create_user(
                username=email.strip(),
                email=email.strip(),
                password=password,
                is_active=True,
            )
            group = Group.objects.get(name=CARETAKER_ROLE)
            user.groups.add(group)
            profile = UserProfile.objects.create(
                user=user,
                name=name.strip(),
                phone=phone.strip() if isinstance(phone, str) else "",
            )
            profile.branches.set(branches)
    except (ValidationError, IntegrityError) as exc:
        return _problem(400, "Invalid caretaker", str(exc), "invalid-caretaker")
    record_audit_event(
        request,
        "caretaker.created",
        "User",
        new_values={"id": user.pk, "email": user.email, "branch_ids": sorted(set(branch_ids))},
    )
    return JsonResponse({"caretaker": _caretaker_payload(user)}, status=201)


@require_GET
def list_property_caretakers(request, property_id):
    error = _property_access_error(request)
    if error:
        return error
    property_obj = _accessible_properties(request.user).filter(pk=property_id).first()
    if property_obj is None:
        return _problem(404, "Property not found", "The property is unavailable.", "property-not-found")
    assignments = property_obj.caretaker_assignments.select_related(
        "caretaker__profile", "assigned_by", "ended_by"
    )
    return JsonResponse({"assignments": [_caretaker_assignment_payload(item) for item in assignments]})


@require_POST
def assign_property_caretaker(request, property_id):
    error = _caretaker_management_error(request)
    if error:
        return error
    property_obj = _accessible_properties(request.user).filter(pk=property_id).first()
    if property_obj is None:
        return _problem(404, "Property not found", "The property is unavailable.", "property-not-found")
    body = _json_body(request)
    try:
        caretaker_id = int(body["caretaker_id"]) if body else None
    except (KeyError, TypeError, ValueError):
        caretaker_id = None
    caretaker = get_user_model().objects.filter(
        pk=caretaker_id,
        is_active=True,
        groups__name=CARETAKER_ROLE,
        profile__branches=property_obj.branch,
    ).select_related("profile").first()
    if caretaker is None:
        return _problem(
            400,
            "Invalid caretaker",
            "The caretaker must be active, have the Caretaker role, and belong to the property branch.",
            "invalid-caretaker",
        )
    if PropertyCaretakerAssignment.objects.filter(
        property=property_obj, caretaker=caretaker, ended_at__isnull=True
    ).exists():
        return _problem(409, "Already assigned", "The caretaker is already assigned.", "caretaker-already-assigned")
    with transaction.atomic():
        assignment = PropertyCaretakerAssignment.objects.create(
            property=property_obj,
            caretaker=caretaker,
            assigned_by=request.user,
        )
        property_obj.caretakers.add(caretaker)
    record_audit_event(
        request,
        "caretaker.assigned",
        "PropertyCaretakerAssignment",
        new_values=_caretaker_assignment_payload(assignment),
    )
    return JsonResponse({"assignment": _caretaker_assignment_payload(assignment)}, status=201)


@require_POST
def end_property_caretaker_assignment(request, property_id, caretaker_id):
    error = _caretaker_management_error(request)
    if error:
        return error
    property_obj = _accessible_properties(request.user).filter(pk=property_id).first()
    if property_obj is None:
        return _problem(404, "Property not found", "The property is unavailable.", "property-not-found")
    assignment = PropertyCaretakerAssignment.objects.filter(
        property=property_obj, caretaker_id=caretaker_id, ended_at__isnull=True
    ).select_related("caretaker__profile").first()
    if assignment is None:
        return _problem(404, "Assignment not found", "The active assignment is unavailable.", "assignment-not-found")
    assignment.ended_at = timezone.now()
    assignment.ended_by = request.user
    assignment.save(update_fields=["ended_at", "ended_by"])
    property_obj.caretakers.remove(assignment.caretaker)
    record_audit_event(
        request,
        "caretaker.unassigned",
        "PropertyCaretakerAssignment",
        old_values={"id": assignment.pk, "property_id": property_id, "caretaker_id": caretaker_id},
        new_values=_caretaker_assignment_payload(assignment),
    )
    return JsonResponse({"assignment": _caretaker_assignment_payload(assignment)})


def _landlord_portal_properties(user):
    return Property.objects.filter(landlord__portal_user=user).select_related("landlord")


def _landlord_portal_error(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not request.user.groups.filter(name=LANDLORD_PORTAL_ROLE).exists():
        return _problem(403, "Permission denied", "A landlord portal account is required.", "landlord-portal-required")
    return None


def _caretaker_invitation_payload(invitation):
    return {
        "id": invitation.pk,
        "email": invitation.email,
        "property_id": invitation.property_id,
        "property_name": invitation.property.name,
        "expires_at": invitation.expires_at.isoformat(),
        "status": invitation.status,
        "used_at": invitation.used_at.isoformat() if invitation.used_at else None,
    }


@require_GET
def list_caretaker_invitations(request):
    error = _landlord_portal_error(request)
    if error:
        return error
    properties = _landlord_portal_properties(request.user)
    raw_property_id = request.GET.get("property_id")
    if raw_property_id:
        try:
            property_id = int(raw_property_id)
        except ValueError:
            return _problem(400, "Invalid property", "property_id must be an integer.", "invalid-property")
        properties = properties.filter(pk=property_id)
        if not properties.exists():
            return _problem(404, "Property not found", "The property is not owned by this account.", "property-not-found")
    property_ids = list(properties.values_list("id", flat=True))
    invitations = CaretakerInvitation.objects.filter(
        property_id__in=property_ids,
    ).select_related("property")[:100]
    assignments = PropertyCaretakerAssignment.objects.filter(
        property_id__in=property_ids,
        ended_at__isnull=True,
    ).select_related("property", "caretaker__profile")
    caretakers = [
        {
            "id": assignment.caretaker_id,
            "full_name": assignment.caretaker.profile.name,
            "email": assignment.caretaker.email,
            "phone": assignment.caretaker.profile.phone,
            "created_at": assignment.caretaker.date_joined.isoformat(),
            "property_id": assignment.property_id,
            "property_name": assignment.property.name,
        }
        for assignment in assignments
    ]
    return JsonResponse(
        {
            "properties": list(properties.values("id", "name")),
            "invites": [_caretaker_invitation_payload(item) for item in invitations],
            "caretakers": caretakers,
        }
    )


@require_POST
def create_caretaker_invitation(request):
    error = _landlord_portal_error(request)
    if error:
        return error
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    email = body.get("email")
    if not isinstance(email, str):
        return _problem(400, "Invalid email", "A valid caretaker email address is required.", "invalid-email")
    email = email.strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        return _problem(400, "Invalid email", "A valid caretaker email address is required.", "invalid-email")
    try:
        property_id = int(body.get("property_id"))
    except (TypeError, ValueError):
        return _problem(400, "Invalid property", "property_id is required.", "invalid-property")
    user_model = get_user_model()
    if user_model.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exists():
        return _problem(409, "Account exists", "This email already belongs to an account.", "caretaker-exists")
    token = secrets.token_urlsafe(32)
    with transaction.atomic():
        property_obj = _landlord_portal_properties(request.user).select_for_update().filter(
            pk=property_id,
            landlord__portal_user=request.user,
        ).first()
        if property_obj is None:
            return _problem(404, "Property not found", "The property is not owned by this account.", "property-not-found")
        if CaretakerInvitation.objects.filter(
            property=property_obj,
            email__iexact=email,
            used_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).exists():
            return _problem(409, "Invitation exists", "An active invitation already exists for this email.", "invitation-exists")
        invitation = CaretakerInvitation.objects.create(
            property=property_obj,
            email=email,
            token_hash=_activation_hash(token),
            created_by=request.user,
            expires_at=timezone.now() + timedelta(hours=48),
        )
    record_audit_event(
        request,
        "caretaker_invitation.created",
        "CaretakerInvitation",
        new_values={"id": invitation.pk, "property_id": property_obj.pk, "email": email},
    )
    return JsonResponse(
        {
            "invitation": _caretaker_invitation_payload(invitation),
            "activation_token": token,
        },
        status=201,
    )


@require_POST
def resend_caretaker_invitation(request, invitation_id):
    error = _landlord_portal_error(request)
    if error:
        return error
    token = secrets.token_urlsafe(32)
    with transaction.atomic():
        invitation = CaretakerInvitation.objects.select_for_update().filter(
            pk=invitation_id,
            property__in=_landlord_portal_properties(request.user),
        ).select_related("property").first()
        if invitation is None or invitation.used_at or invitation.revoked_at:
            return _problem(404, "Invitation not found", "The invitation is unavailable or no longer active.", "invitation-not-found")
        invitation.token_hash = _activation_hash(token)
        invitation.expires_at = timezone.now() + timedelta(hours=48)
        invitation.save(update_fields=["token_hash", "expires_at"])
    record_audit_event(
        request,
        "caretaker_invitation.resent",
        "CaretakerInvitation",
        new_values={"id": invitation.pk, "property_id": invitation.property_id},
    )
    return JsonResponse(
        {
            "invitation": _caretaker_invitation_payload(invitation),
            "activation_token": token,
        }
    )


@require_POST
def revoke_caretaker_access(request):
    error = _landlord_portal_error(request)
    if error:
        return error
    body = _json_body(request)
    try:
        property_id = int(body["property_id"]) if body else None
        caretaker_id = int(body["caretaker_id"]) if body else None
    except (KeyError, TypeError, ValueError):
        property_id = caretaker_id = None
    property_obj = _landlord_portal_properties(request.user).filter(pk=property_id).first()
    if property_obj is None or caretaker_id is None:
        return _problem(404, "Assignment not found", "The property caretaker assignment is unavailable.", "assignment-not-found")
    assignment = PropertyCaretakerAssignment.objects.filter(
        property=property_obj,
        caretaker_id=caretaker_id,
        ended_at__isnull=True,
    ).first()
    if assignment is None:
        return _problem(404, "Assignment not found", "The active caretaker assignment is unavailable.", "assignment-not-found")
    assignment.ended_at = timezone.now()
    assignment.ended_by = request.user
    assignment.save(update_fields=["ended_at", "ended_by"])
    property_obj.caretakers.remove(assignment.caretaker)
    record_audit_event(
        request,
        "caretaker.unassigned",
        "PropertyCaretakerAssignment",
        old_values={"id": assignment.pk, "property_id": property_id, "caretaker_id": caretaker_id},
        new_values=_caretaker_assignment_payload(assignment),
    )
    return JsonResponse({"success": True, "message": "Caretaker property access removed."})


@require_GET
def caretaker_invitation_context(request):
    token = request.GET.get("token", "")
    invitation = CaretakerInvitation.objects.filter(
        token_hash=_activation_hash(token),
    ).select_related("property").first()
    if not token or invitation is None or not invitation.is_usable:
        return _problem(400, "Invalid invitation", "The caretaker invitation is invalid, expired, or already used.", "invalid-invitation")
    if get_user_model().objects.filter(email__iexact=invitation.email).exists():
        return _problem(409, "Account exists", "This email already belongs to an account.", "caretaker-exists")
    return JsonResponse(
        {
            "email": invitation.email,
            "property_name": invitation.property.name,
            "expires_at": invitation.expires_at.isoformat(),
        }
    )


@require_POST
def activate_caretaker(request):
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    token = body.get("token")
    name = body.get("name")
    phone = body.get("phone")
    password = body.get("password")
    if not all(isinstance(value, str) for value in (token, name, phone, password)):
        return _problem(400, "Invalid request", "token, name, phone, and password are required.", "invalid-request")
    name = name.strip()
    try:
        phone = _normalize_ke_phone(phone)
    except ValueError as exc:
        return _problem(400, "Invalid phone", str(exc), "invalid-phone")
    if not name or len(password) < 12:
        return _problem(400, "Invalid registration", "A name and a password of at least 12 characters are required.", "invalid-registration")
    try:
        password_validation.validate_password(password)
    except ValidationError as exc:
        return _problem(400, "Invalid registration", "; ".join(exc.messages), "invalid-registration")
    invitation = CaretakerInvitation.objects.filter(
        token_hash=_activation_hash(token),
    ).select_related("property").first()
    if invitation is None or not invitation.is_usable:
        return _problem(400, "Invalid invitation", "The caretaker invitation is invalid, expired, or already used.", "invalid-invitation")
    user_model = get_user_model()
    if user_model.objects.filter(Q(email__iexact=invitation.email) | Q(username__iexact=invitation.email)).exists():
        return _problem(409, "Account exists", "This email already belongs to an account.", "caretaker-exists")
    try:
        with transaction.atomic():
            invitation = CaretakerInvitation.objects.select_for_update().select_related(
                "property"
            ).get(pk=invitation.pk)
            if not invitation.is_usable:
                return _problem(400, "Invalid invitation", "The caretaker invitation is invalid, expired, or already used.", "invalid-invitation")
            user = user_model.objects.create_user(
                username=invitation.email,
                email=invitation.email,
                password=password,
            )
            group = Group.objects.get(name=CARETAKER_ROLE)
            user.groups.add(group)
            profile = UserProfile(user=user, name=name, phone=phone)
            profile.full_clean()
            profile.save()
            profile.branches.add(invitation.property.branch)
            PropertyCaretakerAssignment.objects.create(
                property=invitation.property,
                caretaker=user,
                assigned_by=invitation.created_by,
            )
            invitation.property.caretakers.add(user)
            invitation.used_by = user
            invitation.used_at = timezone.now()
            invitation.save(update_fields=["used_by", "used_at"])
    except Group.DoesNotExist:
        return _problem(503, "Registration unavailable", "The caretaker role is not configured.", "registration-unavailable")
    except ValidationError as exc:
        return _problem(400, "Invalid registration", "; ".join(exc.messages), "invalid-registration")
    except IntegrityError:
        return _problem(409, "Account exists", "The account or caretaker assignment already exists.", "account-exists")
    login(request, user, backend="users.authentication.EmailOrPhoneBackend")
    record_audit_event(
        request,
        "caretaker_invitation.accepted",
        "CaretakerInvitation",
        new_values={"id": invitation.pk, "user_id": user.pk, "property_id": invitation.property_id},
        actor=user,
    )
    return JsonResponse({"user": _user_payload(user)}, status=201)


@require_GET
def caretaker_dashboard(request):
    error = _caretaker_dashboard_error(request)
    if error:
        return error
    properties = _accessible_properties(request.user).prefetch_related("caretaker_assignments")
    units = Unit.objects.filter(property__in=properties).select_related("property", "floor")
    readings = MeterReading.objects.filter(unit__in=units).select_related("unit", "submitted_by")[:20]
    today = timezone.localdate()
    period = today.replace(day=1)
    active_tenancies = Tenancy.objects.filter(
        unit__in=units,
        status=Tenancy.STATUS_ACTIVE,
    ).select_related("tenant")
    tenancy_by_unit = {tenancy.unit_id: tenancy for tenancy in active_tenancies}
    period_reading_units = set(
        MeterReading.objects.filter(unit__in=units, period=period).values_list("unit_id", flat=True)
    )
    checklist_items = MoveInChecklistItem.objects.filter(
        package__tenancy__unit__in=units,
        status=MoveInChecklistItem.STATUS_PENDING,
    ).select_related("package__tenancy", "package__tenancy__tenant")
    maintenance_tickets = _accessible_maintenance_tickets(request.user)
    unit_payloads = []
    for unit in units:
        tenancy = tenancy_by_unit.get(unit.pk)
        unit_payloads.append(
            {
                **_unit_payload(unit),
                "property_name": unit.property.name,
                "floor_name": unit.floor.name if unit.floor else None,
                "tenant": (
                    {"id": tenancy.tenant_id, "name": tenancy.tenant.name}
                    if tenancy
                    else None
                ),
                "reading_submitted_for_period": unit.pk in period_reading_units,
            }
        )
    return JsonResponse(
        {
            "period": period.isoformat(),
            "summary": {
                "properties": properties.count(),
                "units": len(unit_payloads),
                "occupied": sum(unit.status == Unit.STATUS_OCCUPIED for unit in units),
                "notice_given": sum(unit.status == Unit.STATUS_NOTICE_GIVEN for unit in units),
                "vacant": sum(unit.status == Unit.STATUS_VACANT for unit in units),
                "maintenance": sum(unit.status == Unit.STATUS_MAINTENANCE for unit in units),
                "readings_pending": len(unit_payloads) - len(period_reading_units),
                "checklists_pending": checklist_items.count(),
                "maintenance_tickets": maintenance_tickets.exclude(
                    status=MaintenanceTicket.STATUS_CLOSED
                ).count(),
            },
            "properties": [
                {
                    "id": property_obj.pk,
                    "name": property_obj.name,
                    "location": property_obj.location,
                    "branch_id": property_obj.branch_id,
                    "unit_count": sum(unit.property_id == property_obj.pk for unit in units),
                }
                for property_obj in properties
            ],
            "units": unit_payloads,
            "readings": [_meter_reading_payload(reading) for reading in readings],
            "checklists": [
                {
                    "id": item.pk,
                    "tenancy_id": item.package.tenancy_id,
                    "unit_id": item.package.tenancy.unit_id,
                    "title": item.title,
                    "status": item.status,
                    "notes": item.notes,
                }
                for item in checklist_items
            ],
            "maintenance_tickets": [
                _maintenance_ticket_payload(ticket, include_comments=False)
                for ticket in maintenance_tickets[:20]
            ],
        }
    )


@require_POST
def create_caretaker_meter_readings(request):
    error = _caretaker_dashboard_error(request)
    if error:
        return error
    body = _json_body(request)
    readings_data = body.get("readings") if body else None
    if not isinstance(readings_data, list) or not readings_data:
        return _problem(400, "Invalid request", "readings must be a non-empty array.", "invalid-readings")
    if len(readings_data) > 250:
        return _problem(400, "Invalid request", "A maximum of 250 readings can be submitted at once.", "invalid-readings")
    accessible_units = {
        unit.pk: unit
        for unit in Unit.objects.filter(property__in=_accessible_properties(request.user))
    }
    prepared = []
    seen = set()
    for item in readings_data:
        if not isinstance(item, dict):
            return _problem(400, "Invalid reading", "Each reading must be an object.", "invalid-reading")
        try:
            unit_id = int(item["unit_id"])
            period = date.fromisoformat(str(item["period"]))
            previous_value = Decimal(str(item["previous_value"]))
            current_value = Decimal(str(item["current_value"]))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            return _problem(400, "Invalid reading", "Each reading must include valid unit_id, period, and values.", "invalid-reading")
        meter = str(item.get("meter") or "").strip()
        key = (unit_id, meter, period)
        if unit_id not in accessible_units:
            return _problem(404, "Unit not found", "The unit is unavailable or outside your assignment scope.", "unit-not-found")
        if not meter or key in seen:
            return _problem(400, "Invalid reading", "Meter identifiers and readings must be unique in the submission.", "invalid-reading")
        seen.add(key)
        photo_metadata = item.get("photo_metadata") or {}
        if not isinstance(photo_metadata, dict):
            return _problem(400, "Invalid reading", "photo_metadata must be an object.", "invalid-reading")
        prepared.append(
            MeterReading(
                unit=accessible_units[unit_id],
                meter=meter,
                period=period,
                previous_value=previous_value,
                current_value=current_value,
                submitted_by=request.user,
                photo_key=str(item.get("photo_key") or "").strip(),
                photo_metadata=photo_metadata,
            )
        )
    try:
        with transaction.atomic():
            for reading in prepared:
                reading.full_clean()
            MeterReading.objects.bulk_create(prepared)
    except (ValidationError, IntegrityError) as error:
        detail = error.message_dict if isinstance(error, ValidationError) else "A reading already exists for one of the submitted periods."
        return _problem(400, "Invalid meter reading", str(detail), "invalid-meter-reading")
    payload = [_meter_reading_payload(reading) for reading in prepared]
    for reading in prepared:
        record_audit_event(request, "meter_reading.created", "MeterReading", new_values=_meter_reading_payload(reading))
    return JsonResponse({"readings": payload}, status=201)


@require_GET
def list_properties(request):
    error = _property_access_error(request)
    if error:
        return error
    properties = _accessible_properties(request.user).prefetch_related(
        "caretakers", "blocks__floors", "documents"
    )
    return JsonResponse({"properties": [_property_payload(item) for item in properties]})


@require_GET
def get_property(request, property_id):
    error = _property_access_error(request)
    if error:
        return error
    property_obj = (
        _accessible_properties(request.user)
        .prefetch_related("caretakers", "blocks__floors", "documents")
        .filter(pk=property_id)
        .first()
    )
    if property_obj is None:
        return _problem(
            404,
            "Property not found",
            "The property is unavailable or outside your access scope.",
            "property-not-found",
        )
    return JsonResponse({"property": _property_payload(property_obj)})


@require_POST
def create_property(request):
    error = _caretaker_management_error(request)
    if error:
        return error
    body = _json_body(request)
    required = ("branch_id", "name", "property_type", "location")
    if body is None or any(
        not isinstance(body.get(field), str) or not body[field].strip()
        for field in required[1:]
    ):
        return _problem(
            400,
            "Invalid request",
            "branch_id, name, property_type, and location are required.",
            "invalid-request",
        )
    try:
        branch_id = int(body["branch_id"])
    except (TypeError, ValueError):
        return _problem(400, "Invalid request", "branch_id must be an integer.", "invalid-request")
    profile = getattr(request.user, "profile", None)
    if profile is None:
        return _problem(403, "Permission denied", "A user profile is required.", "property-access-denied")
    branch = profile.accessible_branches().filter(pk=branch_id).first()
    if branch is None:
        return _problem(404, "Branch not found", "The branch is unavailable.", "branch-not-found")
    landlord = None
    if body.get("landlord_id") is not None:
        landlord = _accessible_landlords(request.user).filter(
            pk=body["landlord_id"], branch=branch
        ).first()
        if landlord is None:
            return _problem(
                400,
                "Invalid landlord",
                "The landlord must belong to the selected branch.",
                "invalid-landlord",
            )
    property_obj = Property(
        branch=branch,
        landlord=landlord,
        name=body["name"].strip(),
        property_type=body["property_type"].strip(),
        location=body["location"].strip(),
        gps_coordinates=body.get("gps_coordinates") or {},
        amenities=body.get("amenities") or [],
        house_rules=body.get("house_rules") or {},
        photos=body.get("photos") or [],
    )
    caretaker_ids = body.get("caretaker_ids") or []
    if not isinstance(caretaker_ids, list):
        return _problem(400, "Invalid caretakers", "caretaker_ids must be an array.", "invalid-caretakers")
    try:
        caretaker_ids = [int(value) for value in caretaker_ids]
    except (TypeError, ValueError):
        return _problem(400, "Invalid caretakers", "caretaker_ids must contain integers.", "invalid-caretakers")
    if len(caretaker_ids) != len(set(caretaker_ids)):
        return _problem(400, "Invalid caretakers", "caretaker_ids must be unique.", "invalid-caretakers")
    caretakers = get_user_model().objects.filter(
        pk__in=caretaker_ids,
        groups__name=CARETAKER_ROLE,
        profile__branches=branch,
    ).distinct()
    if len(caretaker_ids) != caretakers.count():
        return _problem(
            400,
            "Invalid caretakers",
            "Every caretaker must have the Caretaker role and access to the property branch.",
            "invalid-caretakers",
        )
    try:
        with transaction.atomic():
            property_obj.full_clean()
            property_obj.save()
            property_obj.caretakers.set(caretakers)
            PropertyCaretakerAssignment.objects.bulk_create(
                [
                    PropertyCaretakerAssignment(
                        property=property_obj,
                        caretaker=caretaker,
                        assigned_by=request.user,
                    )
                    for caretaker in caretakers
                ]
            )
    except ValidationError as error:
        return _problem(400, "Invalid property", str(error.message_dict), "invalid-property")
    record_audit_event(
        request,
        "property.created",
        "Property",
        new_values={"id": property_obj.pk, "branch_id": property_obj.branch_id},
    )
    return JsonResponse({"property": _property_payload(property_obj)}, status=201)


@require_POST
def create_block(request, property_id):
    error = _property_access_error(request)
    if error:
        return error
    property_obj = _accessible_properties(request.user).filter(pk=property_id).first()
    if property_obj is None:
        return _problem(404, "Property not found", "The property is unavailable.", "property-not-found")
    body = _json_body(request)
    if body is None or not isinstance(body.get("name"), str) or not body["name"].strip():
        return _problem(400, "Invalid request", "name is required.", "invalid-request")
    block = Block(property=property_obj, name=body["name"].strip())
    try:
        block.full_clean()
        block.save()
    except (ValidationError, IntegrityError):
        return _problem(400, "Invalid block", "A block with this name already exists.", "invalid-block")
    return JsonResponse({"block": {"id": block.pk, "name": block.name}}, status=201)


@require_POST
def create_floor(request, block_id):
    error = _property_access_error(request)
    if error:
        return error
    block = Block.objects.filter(
        pk=block_id, property__in=_accessible_properties(request.user)
    ).first()
    if block is None:
        return _problem(404, "Block not found", "The block is unavailable.", "block-not-found")
    body = _json_body(request)
    if body is None or not isinstance(body.get("name"), str) or not body["name"].strip():
        return _problem(400, "Invalid request", "name is required.", "invalid-request")
    try:
        level = int(body["level"])
    except (KeyError, TypeError, ValueError):
        return _problem(400, "Invalid request", "level must be an integer.", "invalid-request")
    floor = Floor(block=block, name=body["name"].strip(), level=level)
    try:
        floor.full_clean()
        floor.save()
    except (ValidationError, IntegrityError):
        return _problem(400, "Invalid floor", "That floor already exists.", "invalid-floor")
    return JsonResponse(
        {"floor": {"id": floor.pk, "name": floor.name, "level": floor.level}},
        status=201,
    )


@require_POST
def create_property_document(request, property_id):
    error = _property_access_error(request)
    if error:
        return error
    property_obj = _accessible_properties(request.user).filter(pk=property_id).first()
    if property_obj is None:
        return _problem(404, "Property not found", "The property is unavailable.", "property-not-found")
    body = _json_body(request)
    required = ("document_type", "name", "storage_key")
    if body is None or any(
        not isinstance(body.get(field), str) or not body[field].strip() for field in required
    ):
        return _problem(400, "Invalid request", "Document metadata is incomplete.", "invalid-request")
    document = PropertyDocument(
        property=property_obj,
        document_type=body["document_type"].strip(),
        name=body["name"].strip(),
        storage_key=body["storage_key"].strip(),
        metadata=body.get("metadata") or {},
        uploaded_by=request.user,
    )
    document.save()
    return JsonResponse(
        {
            "document": {
                "id": document.pk,
                "document_type": document.document_type,
                "name": document.name,
                "storage_key": document.storage_key,
                "metadata": document.metadata,
            }
        },
        status=201,
    )


@require_GET
def list_units(request):
    error = _property_access_error(request)
    if error:
        return error
    units = Unit.objects.filter(property__in=_accessible_properties(request.user))
    return JsonResponse({"units": [_unit_payload(unit) for unit in units]})


@require_GET
def vacancy_board(request):
    error = _property_access_error(request)
    if error:
        return error
    properties = _accessible_properties(request.user)
    units = Unit.objects.filter(property__in=properties).select_related(
        "property"
    ).prefetch_related("charges")
    branch_id = request.GET.get("branch_id")
    if branch_id:
        try:
            branch_id = int(branch_id)
        except ValueError:
            return _problem(400, "Invalid filter", "branch_id must be an integer.", "invalid-filter")
        if not properties.filter(branch_id=branch_id).exists():
            return _problem(404, "Branch not found", "The branch is unavailable or outside your access scope.", "branch-not-found")
        units = units.filter(property__branch_id=branch_id)
    area = request.GET.get("area")
    if area:
        units = units.filter(property__location__icontains=area.strip())
    unit_type = request.GET.get("type") or request.GET.get("unit_type")
    if unit_type:
        units = units.filter(unit_type__iexact=unit_type.strip())
    status = request.GET.get("status")
    if status:
        valid_statuses = {value for value, _ in Unit.STATUS_CHOICES}
        if status not in valid_statuses:
            return _problem(400, "Invalid filter", "status is not a supported unit status.", "invalid-filter")
        units = units.filter(status=status)
    listing = request.GET.get("listing_enabled")
    if listing is not None:
        if listing.lower() not in {"true", "false"}:
            return _problem(400, "Invalid filter", "listing_enabled must be true or false.", "invalid-filter")
        units = units.filter(listing_enabled=listing.lower() == "true")
    else:
        units = units.filter(listing_enabled=True)
    try:
        rent_min = Decimal(request.GET["rent_min"]) if request.GET.get("rent_min") else None
        rent_max = Decimal(request.GET["rent_max"]) if request.GET.get("rent_max") else None
    except InvalidOperation:
        return _problem(400, "Invalid filter", "rent_min and rent_max must be valid decimals.", "invalid-filter")
    if rent_min is not None and rent_min < 0 or rent_max is not None and rent_max < 0:
        return _problem(400, "Invalid filter", "Rent filters cannot be negative.", "invalid-filter")
    if rent_min is not None and rent_max is not None and rent_min > rent_max:
        return _problem(400, "Invalid filter", "rent_min cannot exceed rent_max.", "invalid-filter")
    vacancies = []
    for unit in units.order_by("property__name", "code", "id"):
        rent = _current_rent(unit)
        if rent is not None and rent_min is not None and rent.amount < rent_min:
            continue
        if rent is not None and rent_max is not None and rent.amount > rent_max:
            continue
        if rent is None and (rent_min is not None or rent_max is not None):
            continue
        vacancies.append(_vacancy_payload(unit, rent))
    return JsonResponse({"vacancies": vacancies})


@require_GET
def get_unit(request, unit_id):
    error = _property_access_error(request)
    if error:
        return error
    unit = Unit.objects.filter(
        pk=unit_id,
        property__in=_accessible_properties(request.user),
    ).first()
    if unit is None:
        return _problem(
            404,
            "Unit not found",
            "The unit is unavailable or outside your access scope.",
            "unit-not-found",
        )
    return JsonResponse({"unit": _unit_payload(unit)})


@require_GET
def get_unit_history(request, unit_id):
    error = _property_access_error(request)
    if error:
        return error
    unit = (
        Unit.objects.filter(
            pk=unit_id,
            property__in=_accessible_properties(request.user),
        )
        .select_related("property")
        .first()
    )
    if unit is None:
        return _problem(
            404,
            "Unit not found",
            "The unit is unavailable or outside your access scope.",
            "unit-not-found",
        )
    tenancies = unit.tenancies.select_related("tenant").all()
    charges = unit.charges.all()
    audit_events = AuditLog.objects.filter(
        models.Q(entity="Unit", old_values__id=unit.pk)
        | models.Q(entity="Unit", new_values__id=unit.pk)
        | models.Q(entity="UnitCharge", old_values__unit_id=unit.pk)
        | models.Q(entity="UnitCharge", new_values__unit_id=unit.pk)
    ).order_by("timestamp", "id")
    status_changes = audit_events.filter(entity="Unit", action="unit.status_changed")
    charge_events = audit_events.filter(entity="UnitCharge")
    return JsonResponse(
        {
            "unit": _unit_payload(unit),
            "tenancies": [_tenancy_payload(tenancy) for tenancy in tenancies],
            "charges": [_charge_payload(charge) for charge in charges],
            "status_changes": [
                _history_audit_payload(event) for event in status_changes
            ],
            "charge_history": [_history_audit_payload(event) for event in charge_events],
            "maintenance": [],
        }
    )


@require_GET
def list_unit_reservations(request):
    error = _property_access_error(request)
    if error:
        return error
    reservations = UnitReservation.objects.filter(
        unit__property__in=_accessible_properties(request.user)
    ).select_related("unit", "applicant")
    _expire_reservations(request, reservations.filter(status=UnitReservation.STATUS_ACTIVE))
    reservations = reservations.order_by("-reserved_at")
    return JsonResponse(
        {"reservations": [_reservation_payload(item) for item in reservations]}
    )


@require_POST
def create_unit_reservation(request, unit_id):
    error = _property_access_error(request)
    if error:
        return error
    unit = Unit.objects.filter(
        pk=unit_id,
        property__in=_accessible_properties(request.user),
    ).first()
    if unit is None:
        return _problem(
            404,
            "Unit not found",
            "The unit is unavailable or outside your access scope.",
            "unit-not-found",
        )
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    try:
        applicant_id = int(body["applicant_id"])
        expires_at_value = str(body["expires_at"]).replace("Z", "+00:00")
        expires_at = datetime.fromisoformat(expires_at_value)
        if timezone.is_naive(expires_at):
            expires_at = timezone.make_aware(expires_at)
    except (KeyError, TypeError, ValueError):
        return _problem(
            400,
            "Invalid request",
            "applicant_id and an ISO-8601 expires_at are required.",
            "invalid-request",
        )
    applicant = _accessible_tenants(request.user).filter(pk=applicant_id).first()
    if applicant is None:
        return _problem(
            404,
            "Applicant not found",
            "The applicant is unavailable or outside your access scope.",
            "applicant-not-found",
        )
    if unit.status != Unit.STATUS_VACANT:
        return _problem(
            400,
            "Unit unavailable",
            "Only vacant units can be reserved.",
            "unit-unavailable",
        )
    with transaction.atomic():
        _expire_reservations(
            request,
            UnitReservation.objects.filter(unit=unit, status=UnitReservation.STATUS_ACTIVE),
        )
        if UnitReservation.objects.filter(
            unit=unit,
            status=UnitReservation.STATUS_ACTIVE,
        ).exists():
            return _problem(
                409,
                "Unit already reserved",
                "The unit already has an active reservation.",
                "unit-already-reserved",
            )
        reservation = UnitReservation(
            unit=unit,
            applicant=applicant,
            expires_at=expires_at,
            created_by=request.user,
            listing_enabled_before_reservation=unit.listing_enabled,
        )
        try:
            reservation.full_clean()
            reservation.save()
        except (ValidationError, IntegrityError) as error:
            detail = (
                error.message_dict
                if isinstance(error, ValidationError)
                else "The unit already has an active reservation."
            )
            return _problem(400, "Invalid reservation", str(detail), "invalid-reservation")
        unit.listing_enabled = False
        unit.save(update_fields=["listing_enabled", "updated_at"])
    record_audit_event(
        request,
        "unit_reservation.created",
        "UnitReservation",
        new_values=_reservation_payload(reservation),
    )
    return JsonResponse({"reservation": _reservation_payload(reservation)}, status=201)


@require_POST
def release_unit_reservation(request, reservation_id):
    error = _property_access_error(request)
    if error:
        return error
    reservation = UnitReservation.objects.filter(
        pk=reservation_id,
        unit__property__in=_accessible_properties(request.user),
    ).select_related("unit").first()
    if reservation is None:
        return _problem(
            404,
            "Reservation not found",
            "The reservation is unavailable or outside your access scope.",
            "reservation-not-found",
        )
    _expire_reservations(
        request,
        UnitReservation.objects.filter(
            pk=reservation.pk,
            status=UnitReservation.STATUS_ACTIVE,
        ),
    )
    reservation.refresh_from_db()
    if reservation.status == UnitReservation.STATUS_ACTIVE:
        with transaction.atomic():
            reservation.release()
            reservation.save(update_fields=["status", "released_at"])
            reservation.unit.listing_enabled = reservation.listing_enabled_before_reservation
            reservation.unit.save(update_fields=["listing_enabled", "updated_at"])
        record_audit_event(
            request,
            "unit_reservation.released",
            "UnitReservation",
            new_values=_reservation_payload(reservation),
        )
    return JsonResponse({"reservation": _reservation_payload(reservation)})


def _tenancy_access_error(request):
    error = _property_access_error(request)
    if error:
        return error
    return None


def _maintenance_access_error(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not (_has_landlord_management_access(request.user) or _is_caretaker(request.user)):
        return _problem(
            403,
            "Permission denied",
            "You do not have access to maintenance tickets.",
            "maintenance-access-denied",
        )
    return None


def _accessible_maintenance_tickets(user):
    return MaintenanceTicket.objects.filter(
        property__in=_accessible_properties(user)
    ).select_related("property", "unit", "reported_by", "assigned_to", "vendor").prefetch_related(
        "comments__author"
    )


def _maintenance_ticket_payload(ticket, include_comments=True):
    payload = {
        "id": ticket.pk,
        "property_id": ticket.property_id,
        "property_name": ticket.property.name,
        "unit_id": ticket.unit_id,
        "unit_code": ticket.unit.code if ticket.unit else None,
        "reported_by": ticket.reported_by_id,
        "assigned_to": ticket.assigned_to_id,
        "vendor": (
            {
                "id": ticket.vendor_id,
                "name": ticket.vendor.name,
                "trade": ticket.vendor.trade,
                "phone": ticket.vendor.phone,
            }
            if ticket.vendor_id
            else None
        ),
        "quotation_ids": list(ticket.quotations.order_by("pk").values_list("pk", flat=True)),
        "title": ticket.title,
        "description": ticket.description,
        "category": ticket.category,
        "priority": ticket.priority,
        "status": ticket.status,
        "photos": ticket.photos,
        "resolution_notes": ticket.resolution_notes,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
        "completed_at": ticket.completed_at.isoformat() if ticket.completed_at else None,
        "closed_at": ticket.closed_at.isoformat() if ticket.closed_at else None,
    }
    if include_comments:
        payload["comments"] = [
            {
                "id": comment.pk,
                "author": comment.author_id,
                "body": comment.body,
                "photos": comment.photos,
                "created_at": comment.created_at.isoformat(),
            }
            for comment in ticket.comments.all()
        ]
    return payload


def _maintenance_quotation_payload(quotation):
    threshold = _maintenance_approval_threshold(quotation.ticket)
    return {
        "id": quotation.pk,
        "ticket_id": quotation.ticket_id,
        "vendor": {
            "id": quotation.vendor_id,
            "name": quotation.vendor.name,
            "trade": quotation.vendor.trade,
        },
        "description": quotation.description,
        "amount": str(quotation.amount),
        "valid_until": quotation.valid_until.isoformat() if quotation.valid_until else None,
        "status": quotation.status,
        "approval_threshold": str(threshold),
        "requires_landlord_approval": quotation.amount > threshold,
        "submitted_by": quotation.submitted_by_id,
        "approved_by": quotation.approved_by_id,
        "approved_at": quotation.approved_at.isoformat() if quotation.approved_at else None,
        "rejection_reason": quotation.rejection_reason,
        "allocations": [
            {
                "id": allocation.pk,
                "target": allocation.target,
                "landlord_id": allocation.landlord_id,
                "tenant_id": allocation.tenant_id,
                "amount": str(allocation.amount),
                "reason": allocation.reason,
                "allocated_by": allocation.allocated_by_id,
            }
            for allocation in quotation.allocations.all()
        ],
    }


def _maintenance_approval_threshold(ticket):
    landlord = ticket.property.landlord
    if landlord is None:
        return Decimal("0.00")
    agreement = (
        landlord.management_agreements.filter(effective_from__lte=timezone.localdate())
        .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=timezone.localdate()))
        .order_by("-effective_from", "-id")
        .first()
    )
    if agreement is None or not isinstance(agreement.terms, dict):
        return Decimal("0.00")
    value = agreement.terms.get(
        "maintenance_approval_threshold",
        agreement.terms.get("approval_threshold", 0),
    )
    try:
        threshold = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")
    return max(threshold, Decimal("0.00"))


def _quotation_access_error(request, ticket):
    if not (_has_landlord_management_access(request.user) or _is_caretaker(request.user)):
        return _problem(
            403,
            "Permission denied",
            "You do not have access to maintenance quotations.",
            "quotation-access-denied",
        )
    if _is_caretaker(request.user) and not _has_landlord_management_access(request.user):
        if ticket.assigned_to_id != request.user.pk and ticket.reported_by_id != request.user.pk:
            return _problem(
                403,
                "Permission denied",
                "Only the assigned caretaker can manage this quotation.",
                "quotation-access-denied",
            )
    return None


@require_GET
def list_maintenance_quotations(request, ticket_id):
    error = _maintenance_access_error(request)
    if error:
        return error
    ticket = _accessible_maintenance_tickets(request.user).filter(pk=ticket_id).first()
    if ticket is None:
        return _problem(404, "Ticket not found", "The maintenance ticket is unavailable.", "ticket-not-found")
    access_error = _quotation_access_error(request, ticket)
    if access_error:
        return access_error
    quotations = ticket.quotations.select_related("vendor", "submitted_by", "approved_by").prefetch_related("allocations")
    return JsonResponse(
        {
            "approval_threshold": str(_maintenance_approval_threshold(ticket)),
            "quotations": [_maintenance_quotation_payload(quotation) for quotation in quotations],
        }
    )


@require_POST
def create_maintenance_quotation(request, ticket_id):
    error = _maintenance_access_error(request)
    if error:
        return error
    ticket = _accessible_maintenance_tickets(request.user).filter(pk=ticket_id).first()
    if ticket is None:
        return _problem(404, "Ticket not found", "The maintenance ticket is unavailable.", "ticket-not-found")
    access_error = _quotation_access_error(request, ticket)
    if access_error:
        return access_error
    body = _json_body(request)
    if body is None or not isinstance(body.get("description"), str) or not body["description"].strip():
        return _problem(400, "Invalid quotation", "description is required.", "invalid-quotation")
    try:
        vendor = Vendor.objects.get(
            pk=int(body["vendor_id"]),
            branch=ticket.property.branch,
            is_active=True,
        )
        amount = Decimal(str(body["amount"]))
        valid_until = date.fromisoformat(str(body["valid_until"])) if body.get("valid_until") else None
    except (KeyError, Vendor.DoesNotExist, InvalidOperation, TypeError, ValueError):
        return _problem(400, "Invalid quotation", "vendor_id, positive amount, and valid_until are required.", "invalid-quotation")
    quotation = MaintenanceQuotation(
        ticket=ticket,
        vendor=vendor,
        description=body["description"].strip(),
        amount=amount,
        valid_until=valid_until,
        status=MaintenanceQuotation.STATUS_SUBMITTED,
        submitted_by=request.user,
    )
    try:
        quotation.full_clean()
        with transaction.atomic():
            quotation.save()
            if ticket.status == MaintenanceTicket.STATUS_ASSIGNED:
                ticket.transition_to(MaintenanceTicket.STATUS_QUOTATION)
                ticket.save(update_fields=["status", "updated_at"])
    except (ValidationError, IntegrityError) as exc:
        detail = exc.message_dict if isinstance(exc, ValidationError) else "The quotation could not be saved."
        return _problem(400, "Invalid quotation", str(detail), "invalid-quotation")
    record_audit_event(request, "maintenance_quotation.created", "MaintenanceQuotation", new_values=_maintenance_quotation_payload(quotation))
    return JsonResponse({"quotation": _maintenance_quotation_payload(quotation)}, status=201)


@require_POST
def approve_maintenance_quotation(request, quotation_id):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    quotation = MaintenanceQuotation.objects.filter(
        pk=quotation_id,
        ticket__property__in=_accessible_properties(request.user),
    ).select_related("ticket__property__landlord", "vendor").prefetch_related("allocations").first()
    if quotation is None:
        return _problem(404, "Quotation not found", "The quotation is unavailable.", "quotation-not-found")
    threshold = _maintenance_approval_threshold(quotation.ticket)
    if not _has_landlord_management_access(request.user):
        if (
            not _is_caretaker(request.user)
            or quotation.ticket.assigned_to_id != request.user.pk
            or quotation.amount > threshold
        ):
            return _problem(
                403,
                "Permission denied",
                "Only authorized property staff can approve quotations above the agreement threshold.",
                "quotation-approval-denied",
            )
    if quotation.status != MaintenanceQuotation.STATUS_SUBMITTED:
        return _problem(400, "Invalid quotation", "Only submitted quotations can be approved.", "invalid-quotation")
    body = _json_body(request) or {}
    approved = body.get("approved", True)
    if not isinstance(approved, bool):
        return _problem(400, "Invalid quotation", "approved must be a boolean.", "invalid-quotation")
    if not approved and not isinstance(body.get("rejection_reason"), str):
        return _problem(400, "Invalid quotation", "A rejection_reason is required when rejecting.", "invalid-quotation")
    old_status = quotation.status
    quotation.approved_by = request.user if approved else None
    quotation.approved_at = timezone.now() if approved else None
    quotation.status = MaintenanceQuotation.STATUS_APPROVED if approved else MaintenanceQuotation.STATUS_REJECTED
    quotation.rejection_reason = "" if approved else body["rejection_reason"].strip()
    try:
        quotation.full_clean()
        with transaction.atomic():
            quotation.save()
            if approved and quotation.ticket.status == MaintenanceTicket.STATUS_QUOTATION:
                quotation.ticket.transition_to(MaintenanceTicket.STATUS_IN_PROGRESS)
                quotation.ticket.save(update_fields=["status", "updated_at"])
    except ValidationError as exc:
        return _problem(400, "Invalid quotation", str(exc.message_dict), "invalid-quotation")
    record_audit_event(
        request,
        "maintenance_quotation.approved" if approved else "maintenance_quotation.rejected",
        "MaintenanceQuotation",
        old_values={"id": quotation.pk, "status": old_status},
        new_values=_maintenance_quotation_payload(quotation),
    )
    return JsonResponse({"quotation": _maintenance_quotation_payload(quotation)})


@require_POST
def create_maintenance_cost_allocation(request, quotation_id):
    error = _vendor_management_error(request)
    if error:
        return error
    quotation = MaintenanceQuotation.objects.filter(
        pk=quotation_id,
        status=MaintenanceQuotation.STATUS_APPROVED,
        ticket__property__in=_accessible_properties(request.user),
    ).select_related("ticket__property__landlord", "ticket__unit").prefetch_related("allocations").first()
    if quotation is None:
        return _problem(404, "Quotation not found", "An approved quotation in your scope is required.", "quotation-not-found")
    body = _json_body(request)
    if body is None or body.get("target") not in dict(MaintenanceCostAllocation.TARGET_CHOICES):
        return _problem(400, "Invalid allocation", "target must be landlord or tenant.", "invalid-allocation")
    try:
        amount = Decimal(str(body["amount"]))
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return _problem(400, "Invalid allocation", "amount must be a positive number.", "invalid-allocation")
    allocated = quotation.allocations.aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")
    if amount <= 0 or allocated + amount > quotation.amount:
        return _problem(400, "Invalid allocation", "Allocations cannot exceed the approved quotation amount.", "invalid-allocation")
    landlord = tenant = None
    if body["target"] == MaintenanceCostAllocation.TARGET_LANDLORD:
        landlord = quotation.ticket.property.landlord
        if landlord is None:
            return _problem(400, "Invalid allocation", "The ticket property has no landlord.", "invalid-allocation")
    else:
        if quotation.ticket.unit is None:
            return _problem(400, "Invalid allocation", "A tenant allocation requires a ticket unit.", "invalid-allocation")
        tenancy = Tenancy.objects.filter(unit=quotation.ticket.unit, status=Tenancy.STATUS_ACTIVE).select_related("tenant").first()
        if tenancy is None:
            return _problem(400, "Invalid allocation", "The ticket unit has no active tenancy.", "invalid-allocation")
        tenant = tenancy.tenant
    allocation = MaintenanceCostAllocation(
        quotation=quotation,
        target=body["target"],
        landlord=landlord,
        tenant=tenant,
        amount=amount,
        reason=str(body.get("reason") or "").strip(),
        allocated_by=request.user,
    )
    try:
        allocation.full_clean()
        allocation.save()
    except (ValidationError, IntegrityError) as exc:
        detail = exc.message_dict if isinstance(exc, ValidationError) else "The allocation could not be saved."
        return _problem(400, "Invalid allocation", str(detail), "invalid-allocation")
    record_audit_event(
        request,
        "maintenance_cost_allocation.created",
        "MaintenanceCostAllocation",
        new_values={
            "id": allocation.pk,
            "quotation_id": quotation.pk,
            "target": allocation.target,
            "amount": str(allocation.amount),
        },
    )
    return JsonResponse(
        {
            "allocation": {
                "id": allocation.pk,
                "quotation_id": quotation.pk,
                "target": allocation.target,
                "landlord_id": allocation.landlord_id,
                "tenant_id": allocation.tenant_id,
                "amount": str(allocation.amount),
                "reason": allocation.reason,
            }
        },
        status=201,
    )


def _vendor_payload(vendor):
    return {
        "id": vendor.pk,
        "branch_id": vendor.branch_id,
        "name": vendor.name,
        "trade": vendor.trade,
        "phone": vendor.phone,
        "email": vendor.email,
        "mpesa_number": vendor.mpesa_number,
        "rating": str(vendor.rating),
        "notes": vendor.notes,
        "is_active": vendor.is_active,
        "created_at": vendor.created_at.isoformat(),
        "updated_at": vendor.updated_at.isoformat(),
    }


def _vendor_management_error(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not _has_landlord_management_access(request.user):
        return _problem(
            403,
            "Permission denied",
            "Only authorized property staff can manage vendors.",
            "vendor-management-denied",
        )
    return None


def _accessible_vendors(user):
    profile = getattr(user, "profile", None)
    if profile is None or not _has_landlord_management_access(user):
        return Vendor.objects.none()
    return Vendor.objects.filter(branch__in=profile.accessible_branches())


@require_GET
def list_vendors(request):
    error = _vendor_management_error(request)
    if error:
        return error
    vendors = _accessible_vendors(request.user)
    if request.GET.get("active") == "true":
        vendors = vendors.filter(is_active=True)
    return JsonResponse({"vendors": [_vendor_payload(vendor) for vendor in vendors]})


@require_POST
def create_vendor(request):
    error = _vendor_management_error(request)
    if error:
        return error
    body = _json_body(request)
    required = ("branch_id", "name", "trade", "phone")
    if body is None or any(
        body.get(field) in (None, "") for field in required
    ):
        return _problem(400, "Invalid vendor", "branch_id, name, trade, and phone are required.", "invalid-vendor")
    try:
        branch_id = int(body["branch_id"])
    except (TypeError, ValueError):
        return _problem(400, "Invalid vendor", "branch_id must be an integer.", "invalid-vendor")
    branch = _accessible_branches(request.user).filter(pk=branch_id).first()
    if branch is None:
        return _problem(404, "Branch not found", "The branch is unavailable.", "branch-not-found")
    try:
        rating = Decimal(str(body.get("rating", "0")))
    except (InvalidOperation, TypeError, ValueError):
        return _problem(400, "Invalid vendor", "rating must be a number from 0 to 5.", "invalid-vendor")
    vendor = Vendor(
        branch=branch,
        name=str(body["name"]).strip(),
        trade=str(body["trade"]).strip(),
        phone=str(body["phone"]).strip(),
        email=str(body.get("email") or "").strip(),
        mpesa_number=str(body.get("mpesa_number") or "").strip(),
        rating=rating,
        notes=str(body.get("notes") or "").strip(),
    )
    try:
        vendor.full_clean()
        vendor.save()
    except (ValidationError, IntegrityError) as exc:
        detail = exc.message_dict if isinstance(exc, ValidationError) else "A vendor with this phone already exists in the branch."
        return _problem(400, "Invalid vendor", str(detail), "invalid-vendor")
    record_audit_event(request, "vendor.created", "Vendor", new_values=_vendor_payload(vendor))
    return JsonResponse({"vendor": _vendor_payload(vendor)}, status=201)


@require_POST
def update_vendor(request, vendor_id):
    error = _vendor_management_error(request)
    if error:
        return error
    vendor = _accessible_vendors(request.user).filter(pk=vendor_id).first()
    if vendor is None:
        return _problem(404, "Vendor not found", "The vendor is unavailable.", "vendor-not-found")
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid vendor", "A JSON object is required.", "invalid-vendor")
    old_values = _vendor_payload(vendor)
    for field in ("name", "trade", "phone", "email", "mpesa_number", "notes"):
        if field in body:
            setattr(vendor, field, str(body[field] or "").strip())
    if "rating" in body:
        try:
            vendor.rating = Decimal(str(body["rating"]))
        except (InvalidOperation, TypeError, ValueError):
            return _problem(400, "Invalid vendor", "rating must be a number from 0 to 5.", "invalid-vendor")
    if "is_active" in body:
        if not isinstance(body["is_active"], bool):
            return _problem(400, "Invalid vendor", "is_active must be a boolean.", "invalid-vendor")
        vendor.is_active = body["is_active"]
    try:
        vendor.full_clean()
        vendor.save()
    except (ValidationError, IntegrityError) as exc:
        detail = exc.message_dict if isinstance(exc, ValidationError) else "A vendor with this phone already exists in the branch."
        return _problem(400, "Invalid vendor", str(detail), "invalid-vendor")
    record_audit_event(
        request,
        "vendor.updated",
        "Vendor",
        old_values=old_values,
        new_values=_vendor_payload(vendor),
    )
    return JsonResponse({"vendor": _vendor_payload(vendor)})


def _maintenance_photos(body, field="photos"):
    photos = body.get(field) or []
    if not isinstance(photos, list) or len(photos) > 10 or any(not isinstance(item, dict) for item in photos):
        raise ValueError(f"{field} must be an array of at most 10 photo metadata objects.")
    for photo in photos:
        if not str(photo.get("storage_key") or "").strip():
            raise ValueError(f"Every {field} item requires a storage_key.")
        content_type = photo.get("content_type")
        if content_type is not None and content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError(f"{field} content_type must be JPEG, PNG, or WebP.")
        size_bytes = photo.get("size_bytes")
        if size_bytes is not None and (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
            or size_bytes > 10 * 1024 * 1024
        ):
            raise ValueError(f"{field} size_bytes must be an integer from 0 to 10 MB.")
    return photos


@require_GET
def list_maintenance_tickets(request):
    error = _maintenance_access_error(request)
    if error:
        return error
    tickets = _accessible_maintenance_tickets(request.user)
    status = request.GET.get("status")
    if status:
        if status not in dict(MaintenanceTicket.STATUS_CHOICES):
            return _problem(400, "Invalid status", "The maintenance status is not supported.", "invalid-status")
        tickets = tickets.filter(status=status)
    return JsonResponse(
        {"tickets": [_maintenance_ticket_payload(ticket) for ticket in tickets]}
    )


@require_POST
def create_maintenance_ticket(request):
    error = _maintenance_access_error(request)
    if error:
        return error
    body = _json_body(request)
    required = ("property_id", "title", "description", "category")
    if body is None or any(not isinstance(body.get(field), str) or not body[field].strip() for field in required[1:]):
        return _problem(400, "Invalid ticket", "property_id, title, description, and category are required.", "invalid-ticket")
    try:
        property_id = int(body["property_id"])
    except (TypeError, ValueError):
        return _problem(400, "Invalid ticket", "property_id must be an integer.", "invalid-ticket")
    property_obj = _accessible_properties(request.user).filter(pk=property_id).first()
    if property_obj is None:
        return _problem(404, "Property not found", "The property is unavailable or outside your access scope.", "property-not-found")
    unit = None
    vendor = None
    if body.get("unit_id") is not None:
        try:
            unit = Unit.objects.filter(pk=int(body["unit_id"]), property=property_obj).first()
        except (TypeError, ValueError):
            unit = None
        if unit is None:
            return _problem(400, "Invalid ticket", "The unit must belong to the selected property.", "invalid-ticket")
    if body.get("vendor_id") is not None:
        try:
            vendor = Vendor.objects.filter(
                pk=int(body["vendor_id"]),
                branch=property_obj.branch,
                is_active=True,
            ).first()
        except (TypeError, ValueError):
            vendor = None
        if vendor is None:
            return _problem(400, "Invalid vendor", "The vendor must be active and belong to the property branch.", "invalid-vendor")
    try:
        photos = _maintenance_photos(body)
    except ValueError as exc:
        return _problem(400, "Invalid ticket", str(exc), "invalid-ticket")
    priority = body.get("priority", MaintenanceTicket.PRIORITY_NORMAL)
    category = body["category"].strip()
    if category not in dict(MaintenanceTicket.CATEGORY_CHOICES) or priority not in dict(MaintenanceTicket.PRIORITY_CHOICES):
        return _problem(400, "Invalid ticket", "The category or priority is not supported.", "invalid-ticket")
    ticket = MaintenanceTicket(
        property=property_obj,
        unit=unit,
        reported_by=request.user,
        title=body["title"].strip(),
        description=body["description"].strip(),
        category=category,
        priority=priority,
        vendor=vendor,
        photos=photos,
    )
    try:
        ticket.full_clean()
        ticket.save()
    except ValidationError as exc:
        return _problem(400, "Invalid ticket", str(exc.message_dict), "invalid-ticket")
    record_audit_event(request, "maintenance_ticket.created", "MaintenanceTicket", new_values=_maintenance_ticket_payload(ticket, False))
    return JsonResponse({"ticket": _maintenance_ticket_payload(ticket)}, status=201)


@require_POST
def update_maintenance_ticket_status(request, ticket_id):
    error = _maintenance_access_error(request)
    if error:
        return error
    ticket = _accessible_maintenance_tickets(request.user).filter(pk=ticket_id).first()
    if ticket is None:
        return _problem(404, "Ticket not found", "The maintenance ticket is unavailable.", "ticket-not-found")
    body = _json_body(request)
    new_status = body.get("status") if body else None
    if not isinstance(new_status, str):
        return _problem(400, "Invalid status", "status is required.", "invalid-status")
    caretaker_self_assignment = (
        _is_caretaker(request.user)
        and new_status == MaintenanceTicket.STATUS_ASSIGNED
        and str(body.get("assigned_to")) == str(request.user.pk)
    )
    if (
        not _has_landlord_management_access(request.user)
        and ticket.assigned_to_id != request.user.pk
        and not caretaker_self_assignment
    ):
        return _problem(403, "Permission denied", "Only the assigned caretaker or authorized staff can update this ticket.", "ticket-update-denied")
    old_status = ticket.status
    if body.get("assigned_to") is not None:
        try:
            assignee = get_user_model().objects.filter(
                pk=int(body["assigned_to"]),
                is_active=True,
                groups__name=CARETAKER_ROLE,
                profile__branches=ticket.property.branch,
            ).first()
        except (TypeError, ValueError):
            assignee = None
        if assignee is None:
            return _problem(400, "Invalid assignee", "The assignee must be an active caretaker in the property branch.", "invalid-assignee")
        ticket.assigned_to = assignee
    if body.get("vendor_id") is not None:
        try:
            vendor = Vendor.objects.filter(
                pk=int(body["vendor_id"]),
                branch=ticket.property.branch,
                is_active=True,
            ).first()
        except (TypeError, ValueError):
            vendor = None
        if vendor is None:
            return _problem(400, "Invalid vendor", "The vendor must be active and belong to the property branch.", "invalid-vendor")
        ticket.vendor = vendor
    try:
        ticket.transition_to(new_status)
        ticket.full_clean()
        ticket.save()
    except ValidationError as exc:
        return _problem(400, "Invalid status transition", str(exc.message_dict), "invalid-status-transition")
    record_audit_event(
        request,
        "maintenance_ticket.status_changed",
        "MaintenanceTicket",
        old_values={"id": ticket.pk, "status": old_status},
        new_values={"id": ticket.pk, "status": ticket.status, "assigned_to": ticket.assigned_to_id},
    )
    return JsonResponse({"ticket": _maintenance_ticket_payload(ticket)})


@require_POST
def add_maintenance_ticket_comment(request, ticket_id):
    error = _maintenance_access_error(request)
    if error:
        return error
    ticket = _accessible_maintenance_tickets(request.user).filter(pk=ticket_id).first()
    if ticket is None:
        return _problem(404, "Ticket not found", "The maintenance ticket is unavailable.", "ticket-not-found")
    body = _json_body(request)
    if body is None or not isinstance(body.get("body"), str) or not body["body"].strip():
        return _problem(400, "Invalid comment", "A non-empty comment body is required.", "invalid-comment")
    try:
        photos = _maintenance_photos(body)
        comment = MaintenanceTicketComment(
            ticket=ticket,
            author=request.user,
            body=body["body"].strip(),
            photos=photos,
        )
        comment.full_clean()
        comment.save()
    except (ValidationError, ValueError) as exc:
        detail = exc.message_dict if isinstance(exc, ValidationError) else str(exc)
        return _problem(400, "Invalid comment", str(detail), "invalid-comment")
    record_audit_event(
        request,
        "maintenance_ticket.comment_added",
        "MaintenanceTicketComment",
        new_values={"id": comment.pk, "ticket_id": ticket.pk},
    )
    return JsonResponse(
        {
            "comment": {
                "id": comment.pk,
                "author": comment.author_id,
                "body": comment.body,
                "photos": comment.photos,
                "created_at": comment.created_at.isoformat(),
            }
        },
        status=201,
    )


@require_GET
def list_tenancies(request):
    error = _tenancy_access_error(request)
    if error:
        return error
    tenancies = Tenancy.objects.filter(
        unit__property__in=_accessible_properties(request.user)
    )
    return JsonResponse({"tenancies": [_tenancy_payload(item) for item in tenancies]})


@require_GET
def get_tenancy(request, tenancy_id):
    error = _tenancy_access_error(request)
    if error:
        return error
    tenancy = Tenancy.objects.filter(
        pk=tenancy_id,
        unit__property__in=_accessible_properties(request.user),
    ).first()
    if tenancy is None:
        return _problem(
            404,
            "Tenancy not found",
            "The tenancy is unavailable or outside your access scope.",
            "tenancy-not-found",
        )
    return JsonResponse({"tenancy": _tenancy_payload(tenancy)})


@require_POST
def create_tenancy_invoice(request, tenancy_id):
    error = _tenancy_access_error(request)
    if error:
        return error
    tenancy = (
        Tenancy.objects.filter(
            pk=tenancy_id,
            unit__property__in=_accessible_properties(request.user),
        )
        .select_related("unit__property")
        .first()
    )
    if tenancy is None:
        return _problem(
            404,
            "Tenancy not found",
            "The tenancy is unavailable or outside your access scope.",
            "tenancy-not-found",
        )
    body = _json_body(request)
    if body is None or not all(isinstance(body.get(field), str) for field in ("period_start", "period_end")):
        return _problem(
            400,
            "Invalid request",
            "period_start and period_end are required.",
            "invalid-request",
        )
    try:
        period_start = date.fromisoformat(body["period_start"])
        period_end = date.fromisoformat(body["period_end"])
    except ValueError:
        return _problem(400, "Invalid request", "Invoice period dates must be valid.", "invalid-request")
    if period_end < period_start:
        return _problem(400, "Invalid request", "period_end cannot be before period_start.", "invalid-request")
    existing = (
        Invoice.objects.filter(
            tenancy=tenancy,
            period_start=period_start,
            period_end=period_end,
        )
        .prefetch_related("lines")
        .first()
    )
    if existing:
        return JsonResponse({"invoice": _invoice_payload(existing)})
    charges = list(
        tenancy.unit.charges.filter(effective_from__lte=period_start)
        .filter(
            models.Q(effective_to__isnull=True)
            | models.Q(effective_to__gte=period_start)
        )
        .filter(
            charge_type__in={
                UnitCharge.CHARGE_RENT,
                UnitCharge.CHARGE_RECURRING,
            }
        )
        .order_by("charge_type", "name", "id")
    )
    if not charges:
        return _problem(
            400,
            "Invoice cannot be issued",
            "The tenancy has no effective rent or recurring charges.",
            "missing-charges",
        )
    total = sum((charge.amount for charge in charges), Decimal("0.00"))
    due_day = min(tenancy.due_day, calendar.monthrange(period_start.year, period_start.month)[1])
    due_date = date(period_start.year, period_start.month, due_day)
    try:
        with transaction.atomic():
            sequence, _ = InvoiceNumberSequence.objects.select_for_update().get_or_create(
                branch=tenancy.unit.property.branch,
                year=period_start.year,
            )
            sequence_number = sequence.next_number
            sequence.next_number += 1
            sequence.save(update_fields=["next_number"])
            invoice = Invoice.objects.create(
                branch=tenancy.unit.property.branch,
                tenancy=tenancy,
                number=f"INV-{tenancy.unit.property.branch_id}-{period_start.year}-{sequence_number:06d}",
                period_start=period_start,
                period_end=period_end,
                due_date=due_date,
                total=total,
            )
            InvoiceLine.objects.bulk_create(
                [
                    InvoiceLine(
                        invoice=invoice,
                        charge_type=charge.charge_type,
                        description=charge.name,
                        amount=charge.amount,
                        metadata={"unit_charge_id": charge.pk},
                    )
                    for charge in charges
                ]
            )
    except IntegrityError:
        existing = (
            Invoice.objects.filter(
                tenancy=tenancy,
                period_start=period_start,
                period_end=period_end,
            )
            .prefetch_related("lines")
            .first()
        )
        if existing:
            return JsonResponse({"invoice": _invoice_payload(existing)})
        raise
    record_audit_event(
        request,
        "invoice.issued",
        "Invoice",
        new_values=_invoice_payload(invoice),
    )
    return JsonResponse({"invoice": _invoice_payload(invoice)}, status=201)


@require_GET
def list_arrears(request):
    error = _property_access_error(request)
    if error:
        return error
    as_of_value = request.GET.get("as_of")
    try:
        as_of = date.fromisoformat(as_of_value) if as_of_value else timezone.localdate()
    except ValueError:
        return _problem(400, "Invalid request", "as_of must be a valid date.", "invalid-request")
    items = arrears_for_accessible_invoices(
        Invoice.objects.filter(tenancy__unit__property__in=_accessible_properties(request.user)),
        as_of,
    )
    buckets = {bucket: Decimal("0.00") for bucket in ("current", "1-30", "31-60", "61-90", "90+")}
    for item in items:
        buckets[item["bucket"]] += item["total_due"]
    return JsonResponse(
        {
            "as_of": as_of.isoformat(),
            "arrears": [_arrears_payload(item) for item in items],
            "buckets": {key: str(value) for key, value in buckets.items()},
        }
    )


def _waiver_manager_error(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not request.user.is_superuser and not request.user.groups.filter(
        name__in={"Super Admin (Director)", "Branch Manager"}
    ).exists():
        return _problem(403, "Permission denied", "Only a manager can approve arrears waivers.", "waiver-access-denied")
    return None


@require_POST
def create_arrears_waiver(request, invoice_id):
    error = _waiver_manager_error(request)
    if error:
        return error
    invoice = Invoice.objects.filter(
        pk=invoice_id,
        tenancy__unit__property__in=_accessible_properties(request.user),
        status=Invoice.STATUS_ISSUED,
    ).select_related("tenancy__unit__property").first()
    if invoice is None:
        return _problem(404, "Invoice not found", "The invoice is unavailable or outside your access scope.", "invoice-not-found")
    body = _json_body(request)
    if body is None or not isinstance(body.get("reason"), str) or not body["reason"].strip():
        return _problem(400, "Invalid request", "A reason is required.", "invalid-request")
    try:
        amount = Decimal(str(body.get("amount")))
    except (InvalidOperation, TypeError, ValueError):
        return _problem(400, "Invalid request", "amount must be a valid number.", "invalid-request")
    arrears = invoice_arrears(invoice, timezone.localdate())
    if amount <= 0 or amount > arrears["outstanding"]:
        return _problem(400, "Invalid waiver", "Waiver amount must be positive and no greater than outstanding arrears.", "invalid-waiver")
    waiver = ArrearsWaiver.objects.create(
        tenancy=invoice.tenancy,
        invoice=invoice,
        amount=amount,
        reason=body["reason"].strip(),
        approved_by=request.user,
    )
    record_audit_event(request, "arrears_waiver.approved", "ArrearsWaiver", new_values={"id": waiver.pk, "invoice_id": invoice.pk, "amount": str(amount), "reason": waiver.reason})
    return JsonResponse({"waiver": {"id": waiver.pk, "invoice_id": invoice.pk, "amount": str(amount), "reason": waiver.reason}}, status=201)


@require_POST
def create_demand_letter(request, invoice_id):
    error = _waiver_manager_error(request)
    if error:
        return error
    invoice = Invoice.objects.filter(
        pk=invoice_id,
        tenancy__unit__property__in=_accessible_properties(request.user),
        status=Invoice.STATUS_ISSUED,
    ).select_related("tenancy__tenant").first()
    if invoice is None:
        return _problem(404, "Invoice not found", "The invoice is unavailable or outside your access scope.", "invoice-not-found")
    issued_on = timezone.localdate()
    due_on = issued_on + timedelta(days=14)
    existing = invoice.demand_letters.order_by("-id").first()
    if existing:
        return JsonResponse({"demand_letter": {"id": existing.pk, "reference": existing.reference, "issued_on": existing.issued_on.isoformat(), "due_on": existing.due_on.isoformat(), "arrears_amount": str(existing.arrears_amount)}})
    arrears = invoice_arrears(invoice, issued_on)
    letter = DemandLetter.objects.create(
        tenancy=invoice.tenancy,
        invoice=invoice,
        issued_by=request.user,
        issued_on=issued_on,
        due_on=due_on,
        arrears_amount=arrears["total_due"],
        reference=f"DL-{invoice.number}",
    )
    record_audit_event(request, "demand_letter.issued", "DemandLetter", new_values={"id": letter.pk, "invoice_id": invoice.pk, "reference": letter.reference})
    return JsonResponse({"demand_letter": {"id": letter.pk, "reference": letter.reference, "issued_on": issued_on.isoformat(), "due_on": due_on.isoformat(), "arrears_amount": str(letter.arrears_amount)}}, status=201)


@require_GET
def list_payment_plans(request):
    error = _property_access_error(request)
    if error:
        return error
    plans = ArrearsPaymentPlan.objects.filter(
        tenancy__unit__property__in=_accessible_properties(request.user)
    ).prefetch_related("instalments")
    return JsonResponse({"payment_plans": [_payment_plan_payload(plan) for plan in plans]})


@require_POST
def create_payment_plan(request, tenancy_id):
    error = _waiver_manager_error(request)
    if error:
        return error
    tenancy = Tenancy.objects.filter(
        pk=tenancy_id,
        unit__property__in=_accessible_properties(request.user),
    ).first()
    if tenancy is None:
        return _problem(404, "Tenancy not found", "The tenancy is unavailable or outside your access scope.", "tenancy-not-found")
    body = _json_body(request)
    if body is None or not isinstance(body.get("start_date"), str) or not isinstance(body.get("reason"), str):
        return _problem(400, "Invalid request", "total_amount, instalment_amount, start_date, and reason are required.", "invalid-request")
    try:
        total_amount = Decimal(str(body.get("total_amount")))
        instalment_amount = Decimal(str(body.get("instalment_amount")))
        start_date = date.fromisoformat(body["start_date"])
    except (InvalidOperation, TypeError, ValueError):
        return _problem(400, "Invalid request", "Plan amounts and start_date must be valid.", "invalid-request")
    if total_amount <= 0 or instalment_amount <= 0:
        return _problem(400, "Invalid payment plan", "Plan amounts must be positive.", "invalid-payment-plan")
    if not body["reason"].strip():
        return _problem(400, "Invalid payment plan", "A reason is required.", "invalid-payment-plan")
    if ArrearsPaymentPlan.objects.filter(
        tenancy=tenancy, status=ArrearsPaymentPlan.STATUS_ACTIVE
    ).exists():
        return _problem(400, "Invalid payment plan", "The tenancy already has an active payment plan.", "active-payment-plan")
    count = int((total_amount / instalment_amount).to_integral_value(rounding=ROUND_CEILING))
    plan = ArrearsPaymentPlan.objects.create(
        tenancy=tenancy,
        total_amount=total_amount,
        instalment_amount=instalment_amount,
        start_date=start_date,
        reason=body["reason"].strip(),
        created_by=request.user,
    )
    instalments = []
    remaining = total_amount
    for sequence in range(1, count + 1):
        amount = min(instalment_amount, remaining)
        month = start_date.month - 1 + sequence - 1
        due_date = date(
            start_date.year + month // 12,
            month % 12 + 1,
            min(start_date.day, calendar.monthrange(start_date.year + month // 12, month % 12 + 1)[1]),
        )
        instalments.append(
            ArrearsPaymentPlanInstalment(
                plan=plan,
                sequence=sequence,
                due_date=due_date,
                amount_due=amount,
            )
        )
        remaining -= amount
    ArrearsPaymentPlanInstalment.objects.bulk_create(instalments)
    record_audit_event(request, "payment_plan.created", "ArrearsPaymentPlan", new_values={"id": plan.pk, "tenancy_id": tenancy_id, "total_amount": str(total_amount)})
    return JsonResponse({"payment_plan": _payment_plan_payload(plan)}, status=201)


@require_POST
def record_payment_plan_instalment(request, instalment_id):
    error = _waiver_manager_error(request)
    if error:
        return error
    instalment = ArrearsPaymentPlanInstalment.objects.filter(
        pk=instalment_id,
        plan__tenancy__unit__property__in=_accessible_properties(request.user),
        plan__status=ArrearsPaymentPlan.STATUS_ACTIVE,
    ).select_related("plan").first()
    if instalment is None:
        return _problem(404, "Instalment not found", "The instalment is unavailable or outside your access scope.", "instalment-not-found")
    body = _json_body(request)
    try:
        amount = Decimal(str(body.get("amount"))) if body else Decimal("0")
    except (InvalidOperation, TypeError, ValueError):
        return _problem(400, "Invalid request", "amount must be a valid number.", "invalid-request")
    if amount <= 0 or amount > instalment.amount_due - instalment.amount_paid:
        return _problem(400, "Invalid payment", "Payment must be positive and no greater than the instalment balance.", "invalid-payment")
    instalment.amount_paid += amount
    instalment.status = (
        ArrearsPaymentPlanInstalment.STATUS_PAID
        if instalment.amount_paid == instalment.amount_due
        else ArrearsPaymentPlanInstalment.STATUS_DUE
    )
    if instalment.status == ArrearsPaymentPlanInstalment.STATUS_PAID:
        instalment.paid_at = timezone.now()
    instalment.save(update_fields=["amount_paid", "status", "paid_at"])
    plan = instalment.plan
    if not plan.instalments.exclude(status=ArrearsPaymentPlanInstalment.STATUS_PAID).exists():
        plan.status = ArrearsPaymentPlan.STATUS_COMPLETED
        plan.save(update_fields=["status", "updated_at"])
    record_audit_event(request, "payment_plan.instalment_paid", "ArrearsPaymentPlanInstalment", new_values={"id": instalment.pk, "amount": str(amount)})
    return JsonResponse({"payment_plan": _payment_plan_payload(plan)})


@require_POST
def create_move_in_package(request, tenancy_id):
    error = _tenancy_access_error(request)
    if error:
        return error
    tenancy = Tenancy.objects.filter(
        pk=tenancy_id,
        unit__property__in=_accessible_properties(request.user),
    ).select_related("unit__property", "tenant").first()
    if tenancy is None:
        return _problem(
            404,
            "Tenancy not found",
            "The tenancy is unavailable or outside your access scope.",
            "tenancy-not-found",
        )
    existing = MoveInPackage.objects.filter(tenancy=tenancy).prefetch_related(
        "charges", "checklist_items"
    ).first()
    if existing:
        return JsonResponse({"move_in_package": _move_in_package_payload(existing)})

    month_days = calendar.monthrange(tenancy.start_date.year, tenancy.start_date.month)[1]
    remaining_days = month_days - tenancy.start_date.day + 1
    charges = []
    effective_charges = UnitCharge.objects.filter(
        unit=tenancy.unit,
        effective_from__lte=tenancy.start_date,
    ).filter(
        models.Q(effective_to__isnull=True)
        | models.Q(effective_to__gte=tenancy.start_date)
    ).order_by("charge_type", "name", "-effective_from")
    seen_charges = set()
    for charge in effective_charges:
        charge_key = (
            charge.charge_type,
            charge.name if charge.charge_type == UnitCharge.CHARGE_RECURRING else charge.charge_type,
        )
        if charge_key in seen_charges:
            continue
        seen_charges.add(charge_key)
        amount = charge.amount
        calculation = {
            "source_charge_id": charge.pk,
            "effective_from": charge.effective_from.isoformat(),
        }
        if charge.charge_type == UnitCharge.CHARGE_RENT and tenancy.start_date.day != 1:
            amount = (charge.amount / Decimal(month_days) * Decimal(remaining_days)).quantize(
                Decimal("0.01")
            )
            calculation.update(
                {
                    "method": "daily_proration",
                    "monthly_amount": str(charge.amount),
                    "month_days": month_days,
                    "billable_days": remaining_days,
                }
            )
        else:
            calculation["method"] = "full_effective_charge"
        charges.append((charge.charge_type, charge.name, amount, calculation))

    package = MoveInPackage(
        tenancy=tenancy,
        agreement_reference=f"MIA-{timezone.now():%Y%m%d}-{secrets.token_hex(4).upper()}",
    )
    with transaction.atomic():
        package.save()
        MoveInCharge.objects.bulk_create(
            [
                MoveInCharge(
                    package=package,
                    charge_type=charge_type,
                    description=name,
                    amount=amount,
                    calculation=calculation,
                )
                for charge_type, name, amount, calculation in charges
            ]
        )
        MoveInChecklistItem.objects.bulk_create(
            [
                MoveInChecklistItem(package=package, title=title)
                for title in (
                    "Confirm tenant identity and contact details",
                    "Confirm agreement terms and signatures",
                    "Record opening meter readings",
                    "Inspect and document unit condition",
                    "Hand over keys and access devices",
                )
            ]
        )
    package = MoveInPackage.objects.prefetch_related("charges", "checklist_items").get(
        pk=package.pk
    )
    record_audit_event(
        request,
        "move_in_package.created",
        "MoveInPackage",
        new_values=_move_in_package_payload(package),
    )
    return JsonResponse(
        {"move_in_package": _move_in_package_payload(package)},
        status=201,
    )


@require_GET
def get_move_in_agreement_pdf(request, tenancy_id):
    error = _tenancy_access_error(request)
    if error:
        return error
    tenancy = Tenancy.objects.filter(
        pk=tenancy_id,
        unit__property__in=_accessible_properties(request.user),
    ).select_related("unit__property", "tenant").first()
    if tenancy is None:
        return _problem(
            404,
            "Tenancy not found",
            "The tenancy is unavailable or outside your access scope.",
            "tenancy-not-found",
        )
    package = MoveInPackage.objects.filter(tenancy=tenancy).prefetch_related("charges").first()
    if package is None:
        return _problem(
            404,
            "Move-in package not found",
            "Generate the move-in package before downloading the agreement.",
            "move-in-package-not-found",
        )
    response = HttpResponse(_agreement_pdf(tenancy, package), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{package.agreement_reference}.pdf"'
    )
    return response


@require_POST
def issue_tenant_activation(request, tenancy_id):
    error = _tenancy_access_error(request)
    if error:
        return error
    tenancy = Tenancy.objects.filter(
        pk=tenancy_id,
        unit__property__in=_accessible_properties(request.user),
        status=Tenancy.STATUS_ACTIVE,
    ).select_related("tenant").first()
    if tenancy is None:
        return _problem(404, "Tenancy not found", "The active tenancy is unavailable.", "tenancy-not-found")
    return _issue_tenant_activation_challenge(request, tenancy)


def _issue_tenant_activation_challenge(request, tenancy):
    token = secrets.token_urlsafe(32)
    activation_code = secrets.token_urlsafe(9).replace("-", "").replace("_", "").upper()
    now = timezone.now()
    with transaction.atomic():
        TenantActivationChallenge.objects.filter(
            tenant=tenancy.tenant,
            unit=tenancy.unit,
            used_at__isnull=True,
        ).update(used_at=now)
        challenge = TenantActivationChallenge.objects.create(
            tenant=tenancy.tenant,
            unit=tenancy.unit,
            token_hash=_activation_hash(token),
            code_hash=_activation_hash(activation_code),
            expires_at=now + timedelta(hours=48),
        )
    record_audit_event(
        request,
        "tenant_activation.issued",
        "TenantActivationChallenge",
        new_values={
            "id": challenge.pk,
            "tenant_id": tenancy.tenant_id,
            "tenancy_id": tenancy.pk,
            "expires_at": challenge.expires_at.isoformat(),
        },
    )
    return JsonResponse(
        {
            "activation_token": token,
            "activation_code": activation_code,
            "expires_at": challenge.expires_at.isoformat(),
            "tenant_id": tenancy.tenant_id,
            "unit_code": tenancy.unit.code,
        },
        status=201,
    )


@require_POST
def issue_unit_tenant_activation(request, unit_id):
    error = _property_access_error(request)
    if error:
        return error
    tenancy = Tenancy.objects.filter(
        unit_id=unit_id,
        unit__property__in=_accessible_properties(request.user),
        status=Tenancy.STATUS_ACTIVE,
    ).select_related("tenant", "unit").first()
    if tenancy is None:
        return _problem(404, "Active tenancy not found", "No active tenancy is available for this unit.", "active-tenancy-not-found")
    return _issue_tenant_activation_challenge(request, tenancy)


@require_GET
def list_tenant_activation_challenges(request):
    error = _property_access_error(request)
    if error:
        return error
    challenges = TenantActivationChallenge.objects.filter(
        unit__property__in=_accessible_properties(request.user),
        used_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).select_related("tenant", "unit").order_by("expires_at")
    return JsonResponse(
        {
            "codes": [
                {
                    "id": item.pk,
                    "unitCode": item.unit.code,
                    "code": None,
                    "expiresAt": item.expires_at.isoformat(),
                    "tenantName": item.tenant.name,
                    "tenantEmail": item.tenant.email or "",
                    "tenantStatus": "pending",
                }
                for item in challenges
                if Tenancy.objects.filter(
                    tenant_id=item.tenant_id,
                    unit_id=item.unit_id,
                    status=Tenancy.STATUS_ACTIVE,
                ).exists()
            ]
        }
    )


def _usable_tenant_challenge(challenge):
    return (
        challenge is not None
        and challenge.is_usable
        and challenge.unit_id is not None
        and Tenancy.objects.filter(
            tenant_id=challenge.tenant_id,
            unit_id=challenge.unit_id,
            status=Tenancy.STATUS_ACTIVE,
        ).exists()
    )


def _tenant_activation_context_payload(challenge):
    tenant = challenge.tenant
    return {
        "tenant_id": tenant.pk,
        "full_name": tenant.name,
        "email": tenant.email or "",
        "unit_code": challenge.unit.code,
        "property_name": challenge.unit.property.name,
        "expires_at": challenge.expires_at.isoformat(),
    }


@require_GET
def tenant_activation_context(request):
    token = request.GET.get("token", "")
    challenge = TenantActivationChallenge.objects.filter(
        token_hash=_activation_hash(token),
    ).select_related("tenant", "unit__property").first()
    if not token or not _usable_tenant_challenge(challenge):
        return _problem(400, "Invalid activation link", "The activation link is invalid, expired, or already used.", "invalid-activation")
    return JsonResponse(_tenant_activation_context_payload(challenge))


@require_POST
def verify_tenant_activation_code(request):
    body = _json_body(request)
    code = body.get("code") if body else None
    unit_code = body.get("unit_code") if body else None
    if not isinstance(code, str) or not code.strip():
        return _problem(400, "Invalid request", "An activation code is required.", "invalid-request")
    challenge = TenantActivationChallenge.objects.filter(
        code_hash=_activation_hash(code.strip().upper()),
    ).select_related("tenant", "unit__property").first()
    if isinstance(unit_code, str) and unit_code.strip():
        if challenge is None or challenge.unit.code.casefold() != unit_code.strip().casefold():
            challenge = None
    if not _usable_tenant_challenge(challenge):
        return _problem(400, "Invalid activation code", "The activation code is invalid, expired, or already used.", "invalid-activation")
    return JsonResponse(_tenant_activation_context_payload(challenge))


@require_POST
def activate_tenant(request):
    body = _json_body(request)
    if body is None or not isinstance(body.get("password"), str):
        return _problem(400, "Invalid request", "password and an activation token or code are required.", "invalid-request")
    token = body.get("token")
    code = body.get("code")
    if not isinstance(token, str) and not isinstance(code, str):
        return _problem(400, "Invalid request", "password and an activation token or code are required.", "invalid-request")
    password = body["password"]
    if len(password) < 12:
        return _problem(400, "Invalid password", "Password must contain at least 12 characters.", "invalid-password")
    try:
        password_validation.validate_password(password)
    except ValidationError as exc:
        return _problem(400, "Invalid password", "; ".join(exc.messages), "invalid-password")
    full_name = body.get("full_name")
    phone = body.get("phone")
    email = body.get("email")
    if full_name is not None and (not isinstance(full_name, str) or not full_name.strip()):
        return _problem(400, "Invalid request", "full_name must be a non-empty string.", "invalid-request")
    if phone is not None and not isinstance(phone, str):
        return _problem(400, "Invalid request", "phone must be a string.", "invalid-request")
    if email is not None and not isinstance(email, str):
        return _problem(400, "Invalid request", "email must be a string.", "invalid-request")
    if isinstance(email, str) and email.strip():
        email = email.strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            return _problem(400, "Invalid request", "email must be a valid email address.", "invalid-request")
    else:
        email = ""
    try:
        normalized_phone = _normalize_ke_phone(phone) if phone else None
    except ValueError as exc:
        return _problem(400, "Invalid phone", str(exc), "invalid-phone")
    user_model = get_user_model()
    challenge_filter = (
        {"token_hash": _activation_hash(token)}
        if isinstance(token, str)
        else {"code_hash": _activation_hash(code.strip().upper())}
    )
    try:
        with transaction.atomic():
            challenge = TenantActivationChallenge.objects.select_for_update(
                of=("self",)
            ).select_related("tenant", "unit").filter(**challenge_filter).first()
            if not _usable_tenant_challenge(challenge):
                return _problem(400, "Invalid activation", "The activation link or code is invalid, expired, or already used.", "invalid-activation")
            unit_code = body.get("unit_code")
            if isinstance(unit_code, str) and unit_code.strip() and (
                challenge.unit.code.casefold() != unit_code.strip().casefold()
            ):
                return _problem(400, "Invalid activation", "The activation code does not match this unit.", "invalid-activation")
            tenant = challenge.tenant
            if email and tenant.email and email.casefold() != tenant.email.casefold():
                return _problem(403, "Email mismatch", "The activation code is not assigned to this email address.", "email-mismatch")
            tenant_changed = False
            if email and not tenant.email:
                tenant.email = email
                tenant_changed = True
            if normalized_phone and tenant.phone != normalized_phone:
                tenant.phone = normalized_phone
                tenant_changed = True
            if tenant_changed:
                tenant.save(update_fields=["email", "phone", "updated_at"])
            user = tenant.portal_user
            if user is None:
                username = tenant.email.strip().lower() or tenant.phone
                if user_model.objects.filter(
                    Q(username__iexact=username)
                    | (Q(email__iexact=username) if tenant.email else Q(pk__isnull=True))
                ).exists():
                    return _problem(409, "Account exists", "A user account already exists for this tenant.", "account-exists")
                user = user_model.objects.create_user(
                    username=username,
                    email=tenant.email.strip().lower(),
                    password=password,
                    is_active=True,
                )
                profile = UserProfile(user=user, name=(full_name or tenant.name).strip(), phone=normalized_phone or tenant.phone)
                profile.full_clean()
                profile.save()
                profile.branches.add(tenant.branch)
                tenant.portal_user = user
                tenant.save(update_fields=["portal_user", "updated_at"])
            else:
                if not user.groups.filter(name=TENANT_PORTAL_ROLE).exists():
                    return _problem(409, "Account conflict", "This tenant record is linked to a non-tenant portal account.", "account-conflict")
                profile = UserProfile.objects.filter(user=user).first()
                if profile is None:
                    profile = UserProfile(user=user, name=(full_name or tenant.name).strip(), phone=normalized_phone or tenant.phone)
                else:
                    profile.name = (full_name or profile.name).strip()
                    profile.phone = normalized_phone or profile.phone
                profile.full_clean()
                profile.save()
                profile.branches.add(tenant.branch)
                user.set_password(password)
                user.is_active = True
                if not user.email and tenant.email:
                    user.email = tenant.email
                user.save(update_fields=["password", "is_active", "email"])
            user.groups.add(Group.objects.get(name=TENANT_PORTAL_ROLE))
            challenge.used_at = timezone.now()
            challenge.save(update_fields=["used_at"])
    except Group.DoesNotExist:
        return _problem(503, "Activation unavailable", "The tenant portal role is not configured.", "role-unavailable")
    except ValidationError as exc:
        return _problem(400, "Invalid activation", "; ".join(exc.messages), "invalid-activation")
    except IntegrityError:
        return _problem(409, "Account conflict", "The tenant account could not be created because its email or phone is already in use.", "account-conflict")
    login(request, user, backend="users.authentication.EmailOrPhoneBackend")
    record_audit_event(
        request,
        "tenant_activation.completed",
        "TenantActivationChallenge",
        new_values={"id": challenge.pk, "tenant_id": tenant.pk, "user_id": user.pk},
        actor=user,
    )
    return JsonResponse({"activated": True, "tenant_id": tenant.pk, "user": _user_payload(user)})


@require_POST
def create_tenancy(request):
    error = _tenancy_access_error(request)
    if error:
        return error
    body = _json_body(request)
    required = ("unit_id", "tenant_id", "tenancy_type", "start_date", "due_day")
    if body is None or any(body.get(field) in (None, "") for field in required):
        return _problem(
            400,
            "Invalid request",
            "unit_id, tenant_id, tenancy_type, start_date, and due_day are required.",
            "invalid-request",
        )
    try:
        start_date = date.fromisoformat(str(body["start_date"]))
        end_date = (
            date.fromisoformat(str(body["end_date"]))
            if body.get("end_date")
            else None
        )
        due_day = int(body["due_day"])
        grace_period_days = int(body.get("grace_period_days", 0))
        notice_period_days = int(body.get("notice_period_days", 30))
    except (TypeError, ValueError):
        return _problem(
            400,
            "Invalid request",
            "Dates and payment-period values must be valid.",
            "invalid-request",
        )
    escalation_terms = body.get("rent_escalation_terms") or {}
    if not isinstance(escalation_terms, dict):
        return _problem(
            400,
            "Invalid request",
            "rent_escalation_terms must be an object.",
            "invalid-request",
        )
    unit = Unit.objects.filter(
        pk=body["unit_id"],
        property__in=_accessible_properties(request.user),
    ).first()
    if unit is None:
        return _problem(
            404,
            "Unit not found",
            "The unit is unavailable or outside your access scope.",
            "unit-not-found",
        )
    tenant = _accessible_tenants(request.user).filter(pk=body["tenant_id"]).first()
    if tenant is None:
        return _problem(
            404,
            "Tenant not found",
            "The tenant is unavailable or outside your access scope.",
            "tenant-not-found",
        )
    if tenant.branch_id != unit.property.branch_id:
        return _problem(
            400,
            "Invalid tenancy",
            "The tenant and unit must belong to the same branch.",
            "invalid-tenancy",
        )
    if unit.status != Unit.STATUS_VACANT:
        return _problem(
            409,
            "Unit unavailable",
            "Only vacant units can receive a new tenancy.",
            "unit-unavailable",
        )
    tenancy = Tenancy(
        unit=unit,
        tenant=tenant,
        tenancy_type=str(body["tenancy_type"]).strip(),
        start_date=start_date,
        end_date=end_date,
        due_day=due_day,
        grace_period_days=grace_period_days,
        notice_period_days=notice_period_days,
        rent_escalation_terms=escalation_terms,
    )
    try:
        with transaction.atomic():
            locked_unit = Unit.objects.select_for_update().get(pk=unit.pk)
            tenancy.unit = locked_unit
            tenancy.full_clean()
            tenancy.save()
            active_reservation = UnitReservation.objects.select_for_update().filter(
                unit=locked_unit,
                status=UnitReservation.STATUS_ACTIVE,
            ).first()
            if active_reservation:
                active_reservation.release()
                active_reservation.save(update_fields=["status", "released_at"])
                record_audit_event(
                    request,
                    "unit_reservation.released",
                    "UnitReservation",
                    new_values=_reservation_payload(active_reservation),
                )
            locked_unit.listing_enabled = False
            if start_date <= timezone.localdate():
                locked_unit.transition_to(Unit.STATUS_OCCUPIED)
            locked_unit.save(update_fields=["status", "listing_enabled", "updated_at"])
    except (ValidationError, IntegrityError) as error:
        detail = error.message_dict if isinstance(error, ValidationError) else "The unit already has an overlapping tenancy."
        return _problem(400, "Invalid tenancy", str(detail), "invalid-tenancy")
    record_audit_event(
        request,
        "tenancy.created",
        "Tenancy",
        new_values=_tenancy_payload(tenancy),
    )
    return JsonResponse({"tenancy": _tenancy_payload(tenancy)}, status=201)


@require_POST
def create_unit(request):
    error = _property_access_error(request)
    if error:
        return error
    body = _json_body(request)
    required = ("property_id", "code", "unit_type")
    if body is None or any(
        body.get(field) in (None, "") for field in required
    ):
        return _problem(
            400,
            "Invalid request",
            "property_id, code, and unit_type are required.",
            "invalid-request",
        )
    property_obj = _accessible_properties(request.user).filter(
        pk=body["property_id"]
    ).first()
    if property_obj is None:
        return _problem(
            404,
            "Property not found",
            "The property is unavailable or outside your access scope.",
            "property-not-found",
        )
    floor = None
    if body.get("floor_id") is not None:
        floor = Floor.objects.filter(
            pk=body["floor_id"], block__property=property_obj
        ).first()
        if floor is None:
            return _problem(
                400,
                "Invalid floor",
                "The floor must belong to the selected property.",
                "invalid-floor",
            )
    try:
        bedrooms = int(body.get("bedrooms", 0))
    except (TypeError, ValueError):
        return _problem(400, "Invalid request", "bedrooms must be an integer.", "invalid-request")
    unit = Unit(
        property=property_obj,
        floor=floor,
        code=str(body["code"]).strip(),
        unit_type=str(body["unit_type"]).strip(),
        bedrooms=bedrooms,
        characteristics=body.get("characteristics") or {},
        meters=body.get("meters") or {},
        listing_enabled=bool(body.get("listing_enabled", False)),
        status=body.get("status", Unit.STATUS_DRAFT),
    )
    try:
        unit.full_clean()
        unit.save()
    except (ValidationError, IntegrityError) as error:
        detail = (
            error.message_dict
            if isinstance(error, ValidationError)
            else "A unit with this code already exists in the property."
        )
        return _problem(400, "Invalid unit", str(detail), "invalid-unit")
    record_audit_event(
        request,
        "unit.created",
        "Unit",
        new_values=_unit_audit_values(unit),
    )
    return JsonResponse({"unit": _unit_payload(unit)}, status=201)


@require_POST
def transition_unit(request, unit_id):
    error = _property_access_error(request)
    if error:
        return error
    unit = Unit.objects.filter(
        pk=unit_id,
        property__in=_accessible_properties(request.user),
    ).first()
    if unit is None:
        return _problem(
            404,
            "Unit not found",
            "The unit is unavailable or outside your access scope.",
            "unit-not-found",
        )
    body = _json_body(request)
    new_status = body.get("status") if body else None
    if not isinstance(new_status, str):
        return _problem(400, "Invalid request", "status is required.", "invalid-request")
    previous = _unit_audit_values(unit)
    try:
        unit.transition_to(new_status)
        unit.full_clean()
        unit.save(update_fields=["status", "listing_enabled", "updated_at"])
    except ValidationError as error:
        return _problem(400, "Invalid status transition", str(error.message_dict), "invalid-transition")
    record_audit_event(
        request,
        "unit.status_changed",
        "Unit",
        old_values=previous,
        new_values=_unit_audit_values(unit),
    )
    return JsonResponse({"unit": _unit_payload(unit)})


@require_GET
def list_unit_charges(request, unit_id):
    error = _property_access_error(request)
    if error:
        return error
    unit = Unit.objects.filter(
        pk=unit_id,
        property__in=_accessible_properties(request.user),
    ).first()
    if unit is None:
        return _problem(
            404,
            "Unit not found",
            "The unit is unavailable or outside your access scope.",
            "unit-not-found",
        )
    return JsonResponse(
        {"charges": [_charge_payload(charge) for charge in unit.charges.all()]}
    )


@require_POST
def create_unit_charge(request, unit_id):
    error = _property_access_error(request)
    if error:
        return error
    unit = Unit.objects.filter(
        pk=unit_id,
        property__in=_accessible_properties(request.user),
    ).first()
    if unit is None:
        return _problem(
            404,
            "Unit not found",
            "The unit is unavailable or outside your access scope.",
            "unit-not-found",
        )
    body = _json_body(request)
    required = ("charge_type", "name", "amount", "effective_from")
    if body is None or any(
        not isinstance(body.get(field), str) or not body[field].strip()
        for field in required
    ):
        return _problem(
            400,
            "Invalid request",
            "charge_type, name, amount, and effective_from are required.",
            "invalid-request",
        )
    try:
        amount = Decimal(body["amount"])
        effective_from = date.fromisoformat(body["effective_from"])
        effective_to = (
            date.fromisoformat(body["effective_to"])
            if body.get("effective_to")
            else None
        )
    except (InvalidOperation, TypeError, ValueError):
        return _problem(
            400,
            "Invalid request",
            "amount and effective dates must be valid.",
            "invalid-request",
        )
    metadata = body.get("metadata") or {}
    if not isinstance(metadata, dict):
        return _problem(400, "Invalid request", "metadata must be an object.", "invalid-request")
    charge = UnitCharge(
        unit=unit,
        charge_type=body["charge_type"].strip(),
        charge_code=str(body.get("charge_code") or "default").strip(),
        name=body["name"].strip(),
        amount=amount,
        effective_from=effective_from,
        effective_to=effective_to,
        metadata=metadata,
    )
    try:
        with transaction.atomic():
            previous_open_charge = (
                UnitCharge.objects.select_for_update()
                .filter(
                    unit=unit,
                    charge_type=charge.charge_type,
                    effective_to__isnull=True,
                    effective_from__lt=charge.effective_from,
                )
                .order_by("-effective_from")
                .first()
            )
            previous_values = (
                _charge_payload(previous_open_charge)
                if previous_open_charge
                else None
            )
            if previous_open_charge:
                previous_open_charge.effective_to = charge.effective_from - timedelta(days=1)
                previous_open_charge.full_clean()
                previous_open_charge.save(update_fields=["effective_to"])
            charge.full_clean()
            charge.save()
    except (ValidationError, IntegrityError) as error:
        detail = (
            error.message_dict
            if isinstance(error, ValidationError)
            else "The charge could not be saved."
        )
        return _problem(400, "Invalid charge", str(detail), "invalid-charge")
    if previous_open_charge:
        record_audit_event(
            request,
            "unit_charge.updated",
            "UnitCharge",
            old_values=previous_values,
            new_values=_charge_payload(previous_open_charge),
        )
    record_audit_event(
        request,
        "unit_charge.created",
        "UnitCharge",
        new_values=_charge_payload(charge),
    )
    return JsonResponse({"charge": _charge_payload(charge)}, status=201)


@require_GET
def list_unit_utilities(request, unit_id):
    error = _property_access_error(request)
    if error:
        return error
    unit = Unit.objects.filter(
        pk=unit_id,
        property__in=_accessible_properties(request.user),
    ).first()
    if unit is None:
        return _problem(404, "Unit not found", "The unit is unavailable or outside your access scope.", "unit-not-found")
    return JsonResponse({"utilities": [_utility_payload(item) for item in unit.utilities.all()]})


@require_POST
def create_unit_utility(request, unit_id):
    error = _property_access_error(request)
    if error:
        return error
    unit = Unit.objects.filter(
        pk=unit_id,
        property__in=_accessible_properties(request.user),
    ).first()
    if unit is None:
        return _problem(404, "Unit not found", "The unit is unavailable or outside your access scope.", "unit-not-found")
    body = _json_body(request)
    required = ("utility_type", "name", "billing_method", "effective_from")
    if body is None or any(not isinstance(body.get(field), str) or not body[field].strip() for field in required):
        return _problem(400, "Invalid request", "utility_type, name, billing_method, and effective_from are required.", "invalid-request")
    try:
        effective_from = date.fromisoformat(body["effective_from"])
        effective_to = date.fromisoformat(body["effective_to"]) if body.get("effective_to") else None
        fixed_amount = Decimal(body["fixed_amount"]) if body.get("fixed_amount") not in (None, "") else None
        rate_per_unit = Decimal(body["rate_per_unit"]) if body.get("rate_per_unit") not in (None, "") else None
        minimum_charge = Decimal(body.get("minimum_charge", "0"))
    except (InvalidOperation, TypeError, ValueError):
        return _problem(400, "Invalid request", "Utility amounts and effective dates must be valid.", "invalid-request")
    metadata = body.get("metadata") or {}
    if not isinstance(metadata, dict):
        return _problem(400, "Invalid request", "metadata must be an object.", "invalid-request")
    utility = UtilityConfiguration(
        unit=unit,
        utility_type=body["utility_type"].strip(),
        name=body["name"].strip(),
        billing_method=body["billing_method"].strip(),
        fixed_amount=fixed_amount,
        rate_per_unit=rate_per_unit,
        minimum_charge=minimum_charge,
        effective_from=effective_from,
        effective_to=effective_to,
        metadata=metadata,
    )
    try:
        utility.full_clean()
        utility.save()
    except (ValidationError, IntegrityError) as error:
        detail = error.message_dict if isinstance(error, ValidationError) else "The utility configuration could not be saved."
        return _problem(400, "Invalid utility configuration", str(detail), "invalid-utility")
    record_audit_event(request, "utility.created", "UtilityConfiguration", new_values=_utility_payload(utility))
    return JsonResponse({"utility": _utility_payload(utility)}, status=201)


@require_GET
def list_meter_readings(request, unit_id):
    error = _property_access_error(request)
    if error:
        return error
    unit = Unit.objects.filter(
        pk=unit_id,
        property__in=_accessible_properties(request.user),
    ).first()
    if unit is None:
        return _problem(404, "Unit not found", "The unit is unavailable or outside your access scope.", "unit-not-found")
    return JsonResponse(
        {"readings": [_meter_reading_payload(item) for item in unit.meter_readings.select_related("submitted_by").all()]}
    )


@require_POST
def create_meter_reading(request, unit_id):
    error = _property_access_error(request)
    if error:
        return error
    unit = Unit.objects.filter(
        pk=unit_id,
        property__in=_accessible_properties(request.user),
    ).first()
    if unit is None:
        return _problem(404, "Unit not found", "The unit is unavailable or outside your access scope.", "unit-not-found")
    body = _json_body(request)
    required = ("meter", "period", "previous_value", "current_value")
    if body is None or any(body.get(field) in (None, "") for field in required):
        return _problem(400, "Invalid request", "meter, period, previous_value, and current_value are required.", "invalid-request")
    try:
        period = date.fromisoformat(str(body["period"]))
        previous_value = Decimal(str(body["previous_value"]))
        current_value = Decimal(str(body["current_value"]))
    except (InvalidOperation, TypeError, ValueError):
        return _problem(400, "Invalid request", "Meter period and values must be valid.", "invalid-request")
    photo_key = str(body.get("photo_key") or "").strip()
    photo_metadata = body.get("photo_metadata") or {}
    if not isinstance(photo_metadata, dict):
        return _problem(400, "Invalid request", "photo_metadata must be an object.", "invalid-request")
    reading = MeterReading(
        unit=unit,
        meter=str(body["meter"]).strip(),
        period=period,
        previous_value=previous_value,
        current_value=current_value,
        submitted_by=request.user,
        photo_key=photo_key,
        photo_metadata=photo_metadata,
    )
    try:
        reading.full_clean()
        reading.save()
    except (ValidationError, IntegrityError) as error:
        detail = error.message_dict if isinstance(error, ValidationError) else "A reading already exists for this meter and period."
        return _problem(400, "Invalid meter reading", str(detail), "invalid-meter-reading")
    record_audit_event(
        request,
        "meter_reading.created",
        "MeterReading",
        new_values=_meter_reading_payload(reading),
    )
    return JsonResponse({"reading": _meter_reading_payload(reading)}, status=201)


@require_POST
def calculate_water_bill(request, unit_id):
    error = _property_access_error(request)
    if error:
        return error
    unit = Unit.objects.filter(
        pk=unit_id,
        property__in=_accessible_properties(request.user),
    ).first()
    if unit is None:
        return _problem(404, "Unit not found", "The unit is unavailable or outside your access scope.", "unit-not-found")
    body = _json_body(request)
    if body is None or not isinstance(body.get("meter"), str) or not body["meter"].strip():
        return _problem(400, "Invalid request", "meter is required.", "invalid-request")
    if not isinstance(body.get("period"), str):
        return _problem(400, "Invalid request", "period is required.", "invalid-request")
    try:
        period = date.fromisoformat(body["period"])
    except ValueError:
        return _problem(400, "Invalid request", "The billing period must be a valid date.", "invalid-request")
    reading = MeterReading.objects.filter(
        unit=unit,
        meter=body["meter"].strip(),
        period=period,
    ).first()
    if reading is None:
        return _problem(
            400,
            "Water bill cannot be calculated",
            "No meter reading exists for this meter and period.",
            "missing-meter-reading",
        )
    utility = (
        UtilityConfiguration.objects.filter(
            unit=unit,
            utility_type=UtilityConfiguration.UTILITY_WATER,
            billing_method=UtilityConfiguration.METHOD_METERED,
            effective_from__lte=period,
        )
        .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=period))
        .order_by("-effective_from", "-id")
        .first()
    )
    if utility is None:
        return _problem(
            400,
            "Water bill cannot be calculated",
            "No metered water configuration is effective for this period.",
            "missing-water-configuration",
        )
    usage = reading.current_value - reading.previous_value
    calculated_charge = max(
        usage * utility.rate_per_unit,
        utility.minimum_charge,
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    payload = _water_bill_payload(reading, utility, usage, calculated_charge)
    record_audit_event(request, "water_bill.calculated", "MeterReading", new_values=payload)
    return JsonResponse({"water_bill": payload})


def _tenant_access_error(request):
    if not request.user.is_authenticated:
        return _problem(
            401,
            "Authentication required",
            "Sign in first.",
            "authentication-required",
        )
    if not _has_landlord_management_access(request.user):
        return _problem(
            403,
            "Permission denied",
            "You do not have access to tenant records.",
            "tenant-access-denied",
        )
    return None


@require_GET
def list_tenants(request):
    error = _tenant_access_error(request)
    if error:
        return error
    tenants = _accessible_tenants(request.user).prefetch_related("documents")
    return JsonResponse({"tenants": [_tenant_payload(tenant) for tenant in tenants]})


@require_GET
def get_tenant(request, tenant_id):
    error = _tenant_access_error(request)
    if error:
        return error
    tenant = (
        _accessible_tenants(request.user)
        .prefetch_related("documents")
        .filter(pk=tenant_id)
        .first()
    )
    if tenant is None:
        return _problem(
            404,
            "Tenant not found",
            "The tenant is unavailable or outside your access scope.",
            "tenant-not-found",
        )
    return JsonResponse({"tenant": _tenant_payload(tenant)})


@require_POST
def create_tenant(request):
    error = _tenant_access_error(request)
    if error:
        return error
    body = _json_body(request)
    required = ("branch_id", "tenant_type", "name", "phone")
    if body is None or any(
        body.get(field) in (None, "") for field in required
    ):
        return _problem(
            400,
            "Invalid request",
            "branch_id, tenant_type, name, and phone are required.",
            "invalid-request",
        )
    try:
        branch_id = int(body["branch_id"])
    except (TypeError, ValueError):
        return _problem(400, "Invalid request", "branch_id must be an integer.", "invalid-request")
    profile = getattr(request.user, "profile", None)
    branch = (
        profile.accessible_branches().filter(pk=branch_id)
        if profile is not None
        else Branch.objects.none()
    ).first()
    if branch is None:
        return _problem(
            404,
            "Branch not found",
            "The branch is unavailable or outside your access scope.",
            "branch-not-found",
        )
    tenant = Tenant(
        branch=branch,
        tenant_type=str(body["tenant_type"]).strip(),
        name=str(body["name"]).strip(),
        company_registration_number=str(
            body.get("company_registration_number") or ""
        ).strip(),
        contact_person=str(body.get("contact_person") or "").strip(),
        national_id_or_passport=str(
            body.get("national_id_or_passport") or ""
        ).strip(),
        kra_pin=str(body.get("kra_pin") or "").strip(),
        phone=str(body["phone"]).strip(),
        email=str(body.get("email") or "").strip(),
        is_blacklisted=body.get("is_blacklisted", False),
        blacklist_reason=str(body.get("blacklist_reason") or "").strip(),
    )
    if not isinstance(tenant.is_blacklisted, bool):
        return _problem(
            400,
            "Invalid request",
            "is_blacklisted must be a boolean.",
            "invalid-request",
        )
    try:
        tenant.full_clean()
        tenant.save()
    except (ValidationError, IntegrityError) as error:
        detail = (
            error.message_dict
            if isinstance(error, ValidationError)
            else "A tenant with this identity already exists in the branch."
        )
        return _problem(400, "Invalid tenant", str(detail), "invalid-tenant")
    record_audit_event(
        request,
        "tenant.created",
        "Tenant",
        new_values=_tenant_audit_values(tenant),
    )
    return JsonResponse({"tenant": _tenant_payload(tenant)}, status=201)


@require_POST
def create_tenant_document(request, tenant_id):
    error = _tenant_access_error(request)
    if error:
        return error
    tenant = _accessible_tenants(request.user).filter(pk=tenant_id).first()
    if tenant is None:
        return _problem(
            404,
            "Tenant not found",
            "The tenant is unavailable or outside your access scope.",
            "tenant-not-found",
        )
    body = _json_body(request)
    required = ("document_type", "name", "storage_key")
    if body is None or any(
        not isinstance(body.get(field), str) or not body[field].strip()
        for field in required
    ):
        return _problem(
            400,
            "Invalid request",
            "Document metadata is incomplete.",
            "invalid-request",
        )
    metadata = body.get("metadata") or {}
    if not isinstance(metadata, dict):
        return _problem(400, "Invalid request", "metadata must be an object.", "invalid-request")
    document = TenantDocument(
        tenant=tenant,
        document_type=body["document_type"].strip(),
        name=body["name"].strip(),
        storage_key=body["storage_key"].strip(),
        metadata=metadata,
        uploaded_by=request.user,
    )
    document.save()
    record_audit_event(
        request,
        "tenant_document.created",
        "TenantDocument",
        new_values={
            "id": document.pk,
            "tenant_id": tenant.pk,
            "document_type": document.document_type,
            "name": document.name,
        },
    )
    return JsonResponse(
        {
            "document": {
                "id": document.pk,
                "document_type": document.document_type,
                "name": document.name,
                "storage_key": document.storage_key,
                "metadata": document.metadata,
            }
        },
        status=201,
    )


def _inspection_payload(inspection, include_private_token=True):
    payload = {
        "id": inspection.pk,
        "property_id": inspection.property_id,
        "unit_id": inspection.unit_id,
        "title": inspection.title,
        "inspection_type": inspection.inspection_type,
        "scheduled_for": inspection.scheduled_for.isoformat(),
        "responsible_staff_id": inspection.responsible_staff_id,
        "created_by_id": inspection.created_by_id,
        "status": inspection.status,
        "checklist": inspection.checklist,
        "photos": inspection.photos,
        "findings": inspection.findings,
        "recommendations": inspection.recommendations,
        "completed_at": inspection.completed_at.isoformat() if inspection.completed_at else None,
        "shared_at": inspection.shared_at.isoformat() if inspection.shared_at else None,
    }
    if include_private_token:
        payload["share_token"] = str(inspection.share_token) if inspection.shared_at else None
    return payload


def _inspection_access_error(request, property_obj=None):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not (_has_landlord_management_access(request.user) or _is_caretaker(request.user)):
        return _problem(
            403,
            "Permission denied",
            "Only authorized property staff and assigned caretakers can manage inspections.",
            "inspection-access-denied",
        )
    if property_obj is not None and property_obj not in _accessible_properties(request.user):
        return _problem(403, "Permission denied", "The property is outside your scope.", "inspection-access-denied")
    return None


def _accessible_inspections(user):
    return PropertyInspection.objects.filter(property__in=_accessible_properties(user))


def _inspection_checklist(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("checklist must be an array.")
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str) or not item["title"].strip():
            raise ValueError("Each checklist item requires a title.")
    return value


def _inspection_photos(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("photos must be an array.")
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("storage_key"), str) or not item["storage_key"].strip():
            raise ValueError("Each photo requires a storage_key.")
    return value


@require_GET
def list_inspections(request):
    error = _inspection_access_error(request)
    if error:
        return error
    inspections = _accessible_inspections(request.user).select_related(
        "property", "unit", "responsible_staff", "created_by"
    )
    if request.GET.get("property_id"):
        try:
            inspections = inspections.filter(property_id=int(request.GET["property_id"]))
        except ValueError:
            return _problem(400, "Invalid property", "property_id must be an integer.", "invalid-property")
    if request.GET.get("status"):
        inspections = inspections.filter(status=request.GET["status"])
    return JsonResponse({"inspections": [_inspection_payload(item) for item in inspections]})


@require_POST
def create_inspection(request):
    error = _inspection_access_error(request)
    if error:
        return error
    body = _json_body(request) or {}
    try:
        property_obj = Property.objects.get(pk=int(body["property_id"]))
        if property_obj not in _accessible_properties(request.user):
            return _problem(403, "Permission denied", "The property is outside your scope.", "inspection-access-denied")
        unit = Unit.objects.filter(pk=int(body["unit_id"]), property=property_obj).first() if body.get("unit_id") else None
        if body.get("unit_id") and unit is None:
            raise ValueError("unit_id must identify a unit in the property.")
        responsible_staff = get_user_model().objects.get(pk=int(body["responsible_staff_id"]))
        if not isinstance(body.get("title"), str) or not body["title"].strip():
            raise ValueError("title is required.")
        if not isinstance(body.get("inspection_type"), str) or not body["inspection_type"].strip():
            raise ValueError("inspection_type is required.")
        scheduled_for = datetime.fromisoformat(str(body["scheduled_for"]).replace("Z", "+00:00"))
        if timezone.is_naive(scheduled_for):
            scheduled_for = timezone.make_aware(scheduled_for)
        checklist = _inspection_checklist(body.get("checklist"))
        photos = _inspection_photos(body.get("photos"))
    except (KeyError, TypeError, ValueError, Property.DoesNotExist, get_user_model().DoesNotExist):
        return _problem(
            400,
            "Invalid inspection",
            "property_id, title, inspection_type, scheduled_for, and responsible_staff_id are required.",
            "invalid-inspection",
        )
    responsible_in_branch = UserProfile.objects.filter(
        user=responsible_staff,
        branches=property_obj.branch,
    ).exists()
    responsible_caretaker_assignment = PropertyCaretakerAssignment.objects.filter(
        property=property_obj,
        caretaker=responsible_staff,
        ended_at__isnull=True,
    ).exists()
    if not responsible_in_branch or (
        _is_caretaker(responsible_staff) and not responsible_caretaker_assignment
    ):
        return _problem(
            400,
            "Invalid inspection",
            "Responsible staff must belong to the property branch; caretakers must be assigned to the property.",
            "invalid-inspection",
        )
    inspection = PropertyInspection(
        property=property_obj,
        unit=unit,
        title=body["title"].strip(),
        inspection_type=body["inspection_type"].strip(),
        scheduled_for=scheduled_for,
        responsible_staff=responsible_staff,
        created_by=request.user,
        checklist=checklist,
        photos=photos,
    )
    try:
        inspection.full_clean()
        inspection.save()
    except ValidationError as exc:
        return _problem(400, "Invalid inspection", str(exc.message_dict), "invalid-inspection")
    record_audit_event(
        request,
        "property_inspection.created",
        "PropertyInspection",
        new_values=_inspection_payload(inspection),
    )
    return JsonResponse({"inspection": _inspection_payload(inspection)}, status=201)


@require_POST
def complete_inspection(request, inspection_id):
    error = _inspection_access_error(request)
    if error:
        return error
    inspection = _accessible_inspections(request.user).filter(pk=inspection_id).first()
    if inspection is None:
        return _problem(404, "Inspection not found", "The inspection is unavailable.", "inspection-not-found")
    if inspection.status == PropertyInspection.STATUS_CANCELLED:
        return _problem(400, "Invalid inspection", "Cancelled inspections cannot be completed.", "invalid-inspection")
    if not _has_landlord_management_access(request.user) and inspection.responsible_staff_id != request.user.pk:
        return _problem(403, "Permission denied", "Only the responsible caretaker can complete this inspection.", "inspection-access-denied")
    body = _json_body(request) or {}
    try:
        checklist = _inspection_checklist(body.get("checklist", inspection.checklist))
        photos = _inspection_photos(body.get("photos", inspection.photos))
    except ValueError as exc:
        return _problem(400, "Invalid inspection", str(exc), "invalid-inspection")
    inspection.checklist = checklist
    inspection.photos = photos
    inspection.findings = str(body.get("findings", inspection.findings)).strip()
    inspection.recommendations = str(body.get("recommendations", inspection.recommendations)).strip()
    inspection.status = PropertyInspection.STATUS_COMPLETED
    inspection.completed_at = timezone.now()
    try:
        inspection.full_clean()
        inspection.save()
    except ValidationError as exc:
        return _problem(400, "Invalid inspection", str(exc.message_dict), "invalid-inspection")
    record_audit_event(
        request,
        "property_inspection.completed",
        "PropertyInspection",
        new_values=_inspection_payload(inspection),
    )
    return JsonResponse({"inspection": _inspection_payload(inspection)})


@require_POST
def share_inspection_report(request, inspection_id):
    error = _inspection_access_error(request)
    if error:
        return error
    inspection = _accessible_inspections(request.user).filter(pk=inspection_id).first()
    if inspection is None:
        return _problem(404, "Inspection not found", "The inspection is unavailable.", "inspection-not-found")
    if inspection.status != PropertyInspection.STATUS_COMPLETED:
        return _problem(400, "Invalid inspection", "Only completed inspections can be shared.", "invalid-inspection")
    if inspection.shared_at is None:
        inspection.shared_at = timezone.now()
        inspection.save(update_fields=["shared_at", "updated_at"])
        record_audit_event(
            request,
            "property_inspection.report_shared",
            "PropertyInspection",
            new_values={"id": inspection.pk, "shared_at": inspection.shared_at.isoformat()},
        )
    return JsonResponse({"report": _inspection_payload(inspection)})


@require_GET
def get_inspection_report(request, inspection_id):
    error = _inspection_access_error(request)
    if error:
        return error
    inspection = _accessible_inspections(request.user).filter(pk=inspection_id).first()
    if inspection is None:
        return _problem(404, "Inspection not found", "The inspection is unavailable.", "inspection-not-found")
    if inspection.status != PropertyInspection.STATUS_COMPLETED:
        return _problem(404, "Report not found", "A report is available after completion.", "report-not-found")
    return JsonResponse({"report": _inspection_payload(inspection)})


@require_GET
def get_shared_inspection_report(request, share_token):
    inspection = PropertyInspection.objects.filter(
        share_token=share_token,
        status=PropertyInspection.STATUS_COMPLETED,
        shared_at__isnull=False,
    ).first()
    if inspection is None:
        return _problem(404, "Report not found", "The shared inspection report is unavailable.", "report-not-found")
    return JsonResponse({"report": _inspection_payload(inspection, include_private_token=False)})


def _property_expense_threshold(expense):
    landlord = expense.property.landlord
    if landlord is None:
        return Decimal("0.00")
    agreement = (
        landlord.management_agreements.filter(effective_from__lte=timezone.localdate())
        .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=timezone.localdate()))
        .order_by("-effective_from", "-id")
        .first()
    )
    if agreement is None or not isinstance(agreement.terms, dict):
        return Decimal("0.00")
    value = agreement.terms.get(
        "expense_approval_threshold",
        agreement.terms.get("approval_threshold", 0),
    )
    try:
        return max(Decimal(str(value)), Decimal("0.00"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def _property_expense_payload(expense):
    threshold = _property_expense_threshold(expense)
    return {
        "id": expense.pk,
        "property_id": expense.property_id,
        "vendor_id": expense.vendor_id,
        "category": expense.category,
        "description": expense.description,
        "amount": str(expense.amount),
        "recurrence": expense.recurrence,
        "effective_from": expense.effective_from.isoformat(),
        "effective_to": expense.effective_to.isoformat() if expense.effective_to else None,
        "next_due_date": expense.next_due_date.isoformat() if expense.next_due_date else None,
        "submitted_by": expense.submitted_by_id,
        "approved_by": expense.approved_by_id,
        "approved_at": expense.approved_at.isoformat() if expense.approved_at else None,
        "status": expense.status,
        "rejection_reason": expense.rejection_reason or None,
        "approval_threshold": str(threshold),
        "requires_landlord_approval": expense.amount > threshold,
    }


def _property_expense_access_error(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not (_has_landlord_management_access(request.user) or _is_caretaker(request.user)):
        return _problem(
            403,
            "Permission denied",
            "Only authorized property staff and assigned caretakers can manage expenses.",
            "expense-access-denied",
        )
    return None


def _accessible_property_expenses(user):
    return PropertyExpense.objects.filter(
        property__in=_accessible_properties(user)
    ).select_related("property", "property__landlord", "vendor", "submitted_by", "approved_by")


@require_GET
def list_property_expenses(request):
    error = _property_expense_access_error(request)
    if error:
        return error
    expenses = _accessible_property_expenses(request.user)
    if request.GET.get("property_id"):
        try:
            expenses = expenses.filter(property_id=int(request.GET["property_id"]))
        except ValueError:
            return _problem(400, "Invalid property", "property_id must be an integer.", "invalid-property")
    if request.GET.get("status"):
        expenses = expenses.filter(status=request.GET["status"])
    return JsonResponse({"expenses": [_property_expense_payload(expense) for expense in expenses]})


@require_POST
def create_property_expense(request):
    error = _property_expense_access_error(request)
    if error:
        return error
    body = _json_body(request) or {}
    try:
        property_obj = Property.objects.get(pk=int(body["property_id"]))
        if not _accessible_properties(request.user).filter(pk=property_obj.pk).exists():
            return _problem(403, "Permission denied", "The property is outside your scope.", "expense-access-denied")
        if not isinstance(body.get("category"), str) or not body["category"].strip():
            raise ValueError("category is required.")
        if not isinstance(body.get("description"), str) or not body["description"].strip():
            raise ValueError("description is required.")
        amount = Decimal(str(body["amount"]))
        recurrence = str(body.get("recurrence", PropertyExpense.RECURRENCE_ONE_OFF))
        effective_from = date.fromisoformat(str(body["effective_from"]))
        effective_to = date.fromisoformat(str(body["effective_to"])) if body.get("effective_to") else None
        next_due_date = date.fromisoformat(str(body["next_due_date"])) if body.get("next_due_date") else None
        vendor = (
            Vendor.objects.filter(
                pk=int(body["vendor_id"]),
                branch=property_obj.branch,
                is_active=True,
            ).first()
            if body.get("vendor_id")
            else None
        )
        if body.get("vendor_id") and vendor is None:
            raise ValueError("vendor_id must identify an active vendor in the property branch.")
    except (KeyError, TypeError, ValueError, InvalidOperation, Property.DoesNotExist):
        return _problem(
            400,
            "Invalid expense",
            "property_id, category, description, amount, and effective_from are required.",
            "invalid-expense",
        )
    if _is_caretaker(request.user) and not PropertyCaretakerAssignment.objects.filter(
        property=property_obj,
        caretaker=request.user,
        ended_at__isnull=True,
    ).exists():
        return _problem(403, "Permission denied", "You are not assigned to this property.", "expense-access-denied")
    expense = PropertyExpense(
        property=property_obj,
        vendor=vendor,
        category=body["category"].strip(),
        description=body["description"].strip(),
        amount=amount,
        recurrence=recurrence,
        effective_from=effective_from,
        effective_to=effective_to,
        next_due_date=next_due_date,
        submitted_by=request.user,
    )
    try:
        expense.full_clean()
        expense.save()
    except ValidationError as exc:
        return _problem(400, "Invalid expense", str(exc.message_dict), "invalid-expense")
    record_audit_event(
        request,
        "property_expense.submitted",
        "PropertyExpense",
        new_values=_property_expense_payload(expense),
    )
    return JsonResponse({"expense": _property_expense_payload(expense)}, status=201)


@require_POST
def approve_property_expense(request, expense_id):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    expense = _accessible_property_expenses(request.user).filter(pk=expense_id).first()
    if expense is None:
        return _problem(404, "Expense not found", "The property expense is unavailable.", "expense-not-found")
    if expense.status != PropertyExpense.STATUS_SUBMITTED:
        return _problem(400, "Invalid expense", "Only submitted expenses can be approved or rejected.", "invalid-expense")
    if expense.submitted_by_id == request.user.pk:
        return _problem(403, "Permission denied", "The submitter cannot approve the same expense.", "expense-approval-denied")
    threshold = _property_expense_threshold(expense)
    if not _has_landlord_management_access(request.user):
        if (
            not _is_caretaker(request.user)
            or expense.amount > threshold
            or not PropertyCaretakerAssignment.objects.filter(
                property=expense.property,
                caretaker=request.user,
                ended_at__isnull=True,
            ).exists()
        ):
            return _problem(
                403,
                "Permission denied",
                "Only authorized property staff can approve expenses above the agreement threshold.",
                "expense-approval-denied",
            )
    body = _json_body(request) or {}
    approved = body.get("approved", True)
    if not isinstance(approved, bool):
        return _problem(400, "Invalid expense", "approved must be a boolean.", "invalid-expense")
    rejection_reason = body.get("rejection_reason")
    if not approved and (not isinstance(rejection_reason, str) or not rejection_reason.strip()):
        return _problem(400, "Invalid expense", "A rejection_reason is required when rejecting.", "invalid-expense")
    old_status = expense.status
    expense.status = PropertyExpense.STATUS_APPROVED if approved else PropertyExpense.STATUS_REJECTED
    expense.approved_by = request.user if approved else None
    expense.approved_at = timezone.now() if approved else None
    expense.rejection_reason = "" if approved else rejection_reason.strip()
    try:
        expense.full_clean()
        expense.save()
    except ValidationError as exc:
        return _problem(400, "Invalid expense", str(exc.message_dict), "invalid-expense")
    record_audit_event(
        request,
        "property_expense.approved" if approved else "property_expense.rejected",
        "PropertyExpense",
        old_values={"id": expense.pk, "status": old_status},
        new_values=_property_expense_payload(expense),
    )
    return JsonResponse({"expense": _property_expense_payload(expense)})


def _announcement_payload(announcement, include_management_fields=True):
    payload = {
        "id": announcement.pk,
        "branch_id": announcement.branch_id,
        "property_id": announcement.property_id,
        "title": announcement.title,
        "body": announcement.body,
        "publish_at": announcement.publish_at.isoformat() if announcement.publish_at else None,
        "expires_at": announcement.expires_at.isoformat() if announcement.expires_at else None,
        "status": announcement.status,
    }
    if include_management_fields:
        payload.update(
            {
                "created_by": announcement.created_by_id,
                "published_by": announcement.published_by_id,
                "created_at": announcement.created_at.isoformat(),
                "updated_at": announcement.updated_at.isoformat(),
            }
        )
    return payload


def _announcement_management_error(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not _has_landlord_management_access(request.user):
        return _problem(
            403,
            "Permission denied",
            "Only authorized property-management staff can manage announcements.",
            "announcement-access-denied",
        )
    return None


def _tenant_record_for_user(user):
    if not user.is_authenticated or not user.groups.filter(name=TENANT_PORTAL_ROLE).exists():
        return None
    return Tenant.objects.filter(portal_user=user).first()


@require_GET
def list_announcements(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    now = timezone.now()
    tenant = _tenant_record_for_user(request.user)
    if tenant is not None:
        active_property_ids = Tenancy.objects.filter(
            tenant=tenant,
            status=Tenancy.STATUS_ACTIVE,
        ).values("unit__property_id")
        announcements = Announcement.objects.filter(
            branch=tenant.branch,
            status=Announcement.STATUS_PUBLISHED,
            publish_at__lte=now,
        ).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        ).filter(
            models.Q(property__isnull=True) | models.Q(property_id__in=active_property_ids)
        )
        return JsonResponse(
            {
                "announcements": [
                    _announcement_payload(announcement, include_management_fields=False)
                    for announcement in announcements
                ]
            }
        )
    access_error = _announcement_management_error(request)
    if access_error:
        return access_error
    announcements = Announcement.objects.filter(
        branch__in=_accessible_branches(request.user)
    ).select_related("created_by", "published_by")
    if request.GET.get("property_id"):
        try:
            announcements = announcements.filter(property_id=int(request.GET["property_id"]))
        except ValueError:
            return _problem(400, "Invalid property", "property_id must be an integer.", "invalid-property")
    if request.GET.get("status"):
        announcements = announcements.filter(status=request.GET["status"])
    return JsonResponse({"announcements": [_announcement_payload(item) for item in announcements]})


@require_POST
def create_announcement(request):
    error = _announcement_management_error(request)
    if error:
        return error
    body = _json_body(request) or {}
    try:
        branch = Branch.objects.get(pk=int(body["branch_id"]))
        if not _accessible_branches(request.user).filter(pk=branch.pk).exists():
            return _problem(403, "Permission denied", "The branch is outside your scope.", "announcement-access-denied")
        property_obj = (
            Property.objects.filter(pk=int(body["property_id"]), branch=branch).first()
            if body.get("property_id")
            else None
        )
        if body.get("property_id") and property_obj is None:
            raise ValueError("property_id must identify a property in the branch.")
        if not isinstance(body.get("title"), str) or not body["title"].strip():
            raise ValueError("title is required.")
        if not isinstance(body.get("body"), str) or not body["body"].strip():
            raise ValueError("body is required.")
        publish_at = (
            datetime.fromisoformat(str(body["publish_at"]).replace("Z", "+00:00"))
            if body.get("publish_at")
            else None
        )
        expires_at = (
            datetime.fromisoformat(str(body["expires_at"]).replace("Z", "+00:00"))
            if body.get("expires_at")
            else None
        )
        if publish_at and timezone.is_naive(publish_at):
            publish_at = timezone.make_aware(publish_at)
        if expires_at and timezone.is_naive(expires_at):
            expires_at = timezone.make_aware(expires_at)
    except (KeyError, TypeError, ValueError, Branch.DoesNotExist):
        return _problem(
            400,
            "Invalid announcement",
            "branch_id, title, and body are required; dates must be valid ISO values.",
            "invalid-announcement",
        )
    announcement = Announcement(
        branch=branch,
        property=property_obj,
        title=body["title"].strip(),
        body=body["body"].strip(),
        publish_at=publish_at,
        expires_at=expires_at,
        created_by=request.user,
    )
    try:
        announcement.full_clean()
        announcement.save()
    except ValidationError as exc:
        return _problem(400, "Invalid announcement", str(exc.message_dict), "invalid-announcement")
    record_audit_event(
        request,
        "announcement.created",
        "Announcement",
        new_values=_announcement_payload(announcement),
    )
    return JsonResponse({"announcement": _announcement_payload(announcement)}, status=201)


@require_POST
def publish_announcement(request, announcement_id):
    error = _announcement_management_error(request)
    if error:
        return error
    announcement = Announcement.objects.filter(
        pk=announcement_id,
        branch__in=_accessible_branches(request.user),
    ).first()
    if announcement is None:
        return _problem(404, "Announcement not found", "The announcement is unavailable.", "announcement-not-found")
    if announcement.status == Announcement.STATUS_ARCHIVED:
        return _problem(400, "Invalid announcement", "Archived announcements cannot be published.", "invalid-announcement")
    body = _json_body(request) or {}
    publish_at = announcement.publish_at or timezone.now()
    if body.get("publish_at"):
        try:
            publish_at = datetime.fromisoformat(str(body["publish_at"]).replace("Z", "+00:00"))
            if timezone.is_naive(publish_at):
                publish_at = timezone.make_aware(publish_at)
        except (TypeError, ValueError):
            return _problem(400, "Invalid announcement", "publish_at must be a valid ISO date-time.", "invalid-announcement")
    announcement.publish_at = publish_at
    announcement.status = Announcement.STATUS_PUBLISHED
    announcement.published_by = request.user
    try:
        announcement.full_clean()
        announcement.save()
    except ValidationError as exc:
        return _problem(400, "Invalid announcement", str(exc.message_dict), "invalid-announcement")
    record_audit_event(
        request,
        "announcement.published",
        "Announcement",
        new_values=_announcement_payload(announcement),
    )
    return JsonResponse({"announcement": _announcement_payload(announcement)})
