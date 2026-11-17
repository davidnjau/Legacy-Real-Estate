import calendar
import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from users.arrears import arrears_for_accessible_invoices
from users.audit import record_audit_event
from users.models import (
    AgencyMembership,
    AgencySettings,
    ArrearsPolicy,
    ArrearsWaiver,
    AuditLog,
    Branch,
    Invoice,
    InvoiceWaiverRequest,
    LandlordRemittance,
    Payment,
    PaymentAllocation,
    Property,
    Unit,
)
from users.roles import SUPER_ADMIN_ROLE


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


def _director_membership(request, agency_id):
    if not request.user.is_authenticated:
        return None, _problem(
            401,
            "Authentication required",
            "Sign in as an agency Director first.",
            "authentication-required",
        )
    try:
        agency_id = int(agency_id)
    except (TypeError, ValueError):
        return None, _problem(400, "Invalid agency", "A valid agency_id is required.", "invalid-agency")
    membership = (
        AgencyMembership.objects.filter(
            profile__user=request.user,
            agency_id=agency_id,
            agency__is_active=True,
            is_active=True,
            roles__name=SUPER_ADMIN_ROLE,
        )
        .select_related("agency")
        .first()
    )
    if membership is None:
        return None, _problem(
            403,
            "Permission denied",
            "You cannot access data for this agency.",
            "director-membership-required",
        )
    return membership, None


def _branch_scope(request, membership):
    branches = Branch.objects.filter(agency=membership.agency)
    raw_branch_id = request.GET.get("branch_id")
    if raw_branch_id:
        try:
            branch_id = int(raw_branch_id)
        except ValueError:
            return None, _problem(400, "Invalid branch", "branch_id must be an integer.", "invalid-branch")
        branches = branches.filter(pk=branch_id)
        if not branches.exists():
            return None, _problem(404, "Branch not found", "The branch is not part of this agency.", "branch-not-found")
    return branches, None


def _month_range(value):
    if not value:
        today = timezone.localdate()
        return today.replace(day=1), today
    try:
        year, month = (int(part) for part in value.split("-", 1))
        start = date(year, month, 1)
    except (ValueError, TypeError):
        return None, None
    end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    return start, min(end, timezone.localdate())


def _month_parameter(request):
    value = request.GET.get("month", "")
    if value and (len(value) != 7 or value[4:5] != "-"):
        return None, None, _problem(400, "Invalid month", "month must use YYYY-MM format.", "invalid-month")
    start, end = _month_range(value)
    if start is None or end is None:
        return None, None, _problem(400, "Invalid month", "month must be a valid calendar month.", "invalid-month")
    if start > timezone.localdate():
        return None, None, _problem(400, "Invalid month", "Future months are not available.", "invalid-month")
    return start, end, None


def _month_invoice_totals(branches, start, end):
    invoices = Invoice.objects.filter(
        branch__in=branches,
        status=Invoice.STATUS_ISSUED,
        issued_at__date__range=(start, end),
    )
    billed = invoices.aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    allocations = PaymentAllocation.objects.filter(
        invoice__branch__in=branches,
        payment__received_at__date__range=(start, end),
    ).exclude(
        payment__instrument_status__in=(
            Payment.INSTRUMENT_PENDING,
            Payment.INSTRUMENT_BOUNCED,
        )
    )
    collected = allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    return billed, collected


def _branch_metrics(branches, start, end):
    rows = []
    for branch in branches.order_by("name", "id"):
        properties = Property.objects.filter(branch=branch)
        units = Unit.objects.filter(property__in=properties)
        unit_count = units.count()
        occupied = units.filter(status=Unit.STATUS_OCCUPIED).count()
        billed, collected = _month_invoice_totals(
            Branch.objects.filter(pk=branch.pk),
            start,
            end,
        )
        arrears_items = arrears_for_accessible_invoices(
            Invoice.objects.filter(branch=branch, status=Invoice.STATUS_ISSUED),
            timezone.localdate(),
        )
        arrears_total = sum(
            (item["total_due"] for item in arrears_items),
            Decimal("0.00"),
        )
        rows.append(
            {
                "id": branch.pk,
                "name": branch.name,
                "town": branch.town,
                "properties": properties.count(),
                "units": unit_count,
                "occupied_units": occupied,
                "occupancy_rate": round(occupied * 100 / unit_count, 1) if unit_count else 0,
                "billed": str(billed),
                "collected": str(collected),
                "arrears": str(arrears_total),
            }
        )
    return rows


@require_GET
def director_overview(request):
    membership, error = _director_membership(request, request.GET.get("agency_id"))
    if error:
        return error
    branches, error = _branch_scope(request, membership)
    if error:
        return error
    start, end, error = _month_parameter(request)
    if error:
        return error

    metrics = _branch_metrics(branches, start, end)
    totals = {
        "properties": sum(row["properties"] for row in metrics),
        "units": sum(row["units"] for row in metrics),
        "occupied_units": sum(row["occupied_units"] for row in metrics),
        "billed": str(sum((Decimal(row["billed"]) for row in metrics), Decimal("0.00"))),
        "collected": str(sum((Decimal(row["collected"]) for row in metrics), Decimal("0.00"))),
        "arrears": str(sum((Decimal(row["arrears"]) for row in metrics), Decimal("0.00"))),
    }
    totals["occupancy_rate"] = (
        round(totals["occupied_units"] * 100 / totals["units"], 1)
        if totals["units"]
        else 0
    )
    unmatched = (
        Payment.objects.filter(
            status=Payment.STATUS_UNMATCHED,
            received_at__date__lte=end,
        )
        .filter(
            Q(tenant__branch__in=branches)
            | Q(bank_statement_row__statement__imported_by__profile__agency_memberships=membership)
        )
        .select_related("tenant", "bank_statement_row")
        .order_by("-received_at", "-id")[:20]
    )
    return JsonResponse(
        {
            "agency": {"id": membership.agency_id, "name": membership.agency.name},
            "period": {"from": start.isoformat(), "to": end.isoformat()},
            "totals": totals,
            "branches": metrics,
            "unmatched_payments": [
                {
                    "id": payment.pk,
                    "reference": payment.reference,
                    "account_reference": payment.account_reference,
                    "amount": str(payment.amount),
                    "received_at": payment.received_at.isoformat(),
                    "tenant_name": payment.tenant.name if payment.tenant_id else None,
                    "branch_name": payment.tenant.branch.name if payment.tenant_id else None,
                }
                for payment in unmatched
            ],
        }
    )


@require_GET
def director_properties(request):
    membership, error = _director_membership(request, request.GET.get("agency_id"))
    if error:
        return error
    branches, error = _branch_scope(request, membership)
    if error:
        return error
    today = timezone.localdate()
    properties = (
        Property.objects.filter(branch__in=branches)
        .select_related("branch", "landlord")
        .annotate(
            unit_count=Count("units", distinct=True),
            occupied_count=Count(
                "units",
                filter=Q(units__status=Unit.STATUS_OCCUPIED),
                distinct=True,
            ),
        )
        .prefetch_related("landlord__management_agreements")
        .order_by("branch__name", "name", "id")
    )
    rows = []
    for item in properties:
        agreements = []
        if item.landlord_id:
            agreements = [
                agreement
                for agreement in item.landlord.management_agreements.all()
                if agreement.effective_from <= today
                and (agreement.effective_to is None or agreement.effective_to >= today)
            ]
        agreement = max(
            agreements,
            key=lambda row: (row.effective_from, row.pk),
            default=None,
        )
        rows.append(
            {
                "id": item.pk,
                "name": item.name,
                "property_type": item.property_type,
                "location": item.location,
                "address": item.address,
                "branch": {
                    "id": item.branch_id,
                    "name": item.branch.name,
                    "town": item.branch.town,
                },
                "landlord": (
                    {"id": item.landlord_id, "name": item.landlord.name}
                    if item.landlord_id
                    else None
                ),
                "unit_count": item.unit_count,
                "expected_unit_count": item.expected_unit_count,
                "default_unit_type": item.default_unit_type,
                "default_rent": str(item.default_rent),
                "occupied_count": item.occupied_count,
                "occupancy_rate": (
                    round(item.occupied_count * 100 / item.unit_count, 1)
                    if item.unit_count
                    else 0
                ),
                "commission_model": agreement.commission_model if agreement else None,
                "commission_rate": (
                    str(agreement.commission_rate)
                    if agreement and agreement.commission_rate is not None
                    else None
                ),
                "agreement_expiry": (
                    agreement.effective_to.isoformat()
                    if agreement and agreement.effective_to
                    else None
                ),
                "units": [
                    {
                        "id": unit.pk,
                        "code": unit.code,
                        "unit_type": unit.unit_type,
                        "status": unit.status,
                    }
                    for unit in item.units.order_by("code", "id")
                ],
            }
        )
    return JsonResponse(
        {
            "agency": {"id": membership.agency_id, "name": membership.agency.name},
            "branches": list(branches.order_by("name").values("id", "name", "town")),
            "properties": rows,
        }
    )


def _remittance_payload(remittance):
    return {
        "id": remittance.pk,
        "landlord_id": remittance.landlord_id,
        "landlord_name": remittance.landlord.name,
        "branch_id": remittance.landlord.branch_id,
        "branch_name": remittance.landlord.branch.name,
        "period_start": remittance.period_start.isoformat(),
        "period_end": remittance.period_end.isoformat(),
        "revision": remittance.revision,
        "collected_amount": str(remittance.collected_amount),
        "commission_amount": str(remittance.commission_amount),
        "expenses_amount": str(remittance.expenses_amount),
        "tax_amount": str(remittance.tax_amount) if remittance.tax_amount is not None else None,
        "net_payout": str(remittance.net_payout),
        "status": remittance.status,
        "prepared_by": remittance.prepared_by.get_full_name() or remittance.prepared_by.get_username(),
        "prepared_by_id": remittance.prepared_by_id,
        "reviewed_by": (
            remittance.reviewed_by.get_full_name() or remittance.reviewed_by.get_username()
            if remittance.reviewed_by_id
            else None
        ),
        "rejection_reason": remittance.rejection_reason,
        "payout_reference": remittance.payout_reference,
        "payout_date": remittance.payout_date.isoformat() if remittance.payout_date else None,
    }


@require_GET
def director_financials(request):
    membership, error = _director_membership(request, request.GET.get("agency_id"))
    if error:
        return error
    branches, error = _branch_scope(request, membership)
    if error:
        return error
    start, end, error = _month_parameter(request)
    if error:
        return error
    billed, collected = _month_invoice_totals(branches, start, end)
    remittances = LandlordRemittance.objects.filter(
        landlord__branch__in=branches,
        period_start__lte=end,
        period_end__gte=start,
    ).select_related("landlord__branch", "prepared_by", "reviewed_by")
    remittance_summary = remittances.aggregate(
        remittance_count=Count("id"),
        pending_count=Count(
            "id",
            filter=Q(status=LandlordRemittance.STATUS_AWAITING_APPROVAL),
        ),
        pending_amount=Sum(
            "net_payout",
            filter=Q(status=LandlordRemittance.STATUS_AWAITING_APPROVAL),
        ),
        approved_count=Count(
            "id",
            filter=Q(status=LandlordRemittance.STATUS_APPROVED),
        ),
        approved_amount=Sum(
            "net_payout",
            filter=Q(status=LandlordRemittance.STATUS_APPROVED),
        ),
        paid_count=Count(
            "id",
            filter=Q(status=LandlordRemittance.STATUS_PAID),
        ),
        paid_amount=Sum(
            "net_payout",
            filter=Q(status=LandlordRemittance.STATUS_PAID),
        ),
        tax_withheld=Sum(
            "tax_amount",
            filter=~Q(status=LandlordRemittance.STATUS_REJECTED),
        ),
    )
    pending_waivers = list(
        InvoiceWaiverRequest.objects.filter(
            status=InvoiceWaiverRequest.STATUS_PENDING,
            unit__property__branch__in=branches,
        )
        .select_related(
            "invoice__tenancy__tenant",
            "invoice__tenancy__unit__property",
            "unit__property__branch",
            "requested_by",
        )
        .prefetch_related("unit__tenancies__tenant")
        .order_by("created_at", "id")[:200]
    )
    pending_waiver_count = InvoiceWaiverRequest.objects.filter(
        status=InvoiceWaiverRequest.STATUS_PENDING,
        unit__property__branch__in=branches,
    ).count()
    approved_waivers = {
        row["invoice_id"]: row["total"] or Decimal("0.00")
        for row in ArrearsWaiver.objects.filter(
            invoice_id__in=[item.invoice_id for item in pending_waivers if item.invoice_id]
        )
        .values("invoice_id")
        .annotate(total=Sum("amount"))
    }
    remittance_records = list(
        remittances.order_by("-period_end", "-revision", "-id")[:200]
    )
    return JsonResponse(
        {
            "agency": {"id": membership.agency_id, "name": membership.agency.name},
            "period": {"from": start.isoformat(), "to": end.isoformat()},
            "summary": {
                "billed_rent": str(billed),
                "collected_rent": str(collected),
                "remittance_count": remittance_summary["remittance_count"] or 0,
                "pending_approval_count": remittance_summary["pending_count"] or 0,
                "pending_approval": str(remittance_summary["pending_amount"] or Decimal("0.00")),
                "approved_payout_count": remittance_summary["approved_count"] or 0,
                "approved_payouts": str(remittance_summary["approved_amount"] or Decimal("0.00")),
                "paid_payout_count": remittance_summary["paid_count"] or 0,
                "paid_payouts": str(remittance_summary["paid_amount"] or Decimal("0.00")),
                "tax_withheld": str(remittance_summary["tax_withheld"] or Decimal("0.00")),
            },
            "remittances": [_remittance_payload(item) for item in remittance_records],
            "pending_waiver_count": pending_waiver_count,
            "waiver_requests": [
                {
                    "id": item.pk,
                    "invoice_id": item.invoice_id,
                    "invoice_number": item.invoice.number if item.invoice_id else None,
                    "invoice_status": item.invoice.status if item.invoice_id else None,
                    "invoice_total": str(item.invoice.total) if item.invoice_id else None,
                    "waived_amount": str(approved_waivers.get(item.invoice_id, Decimal("0.00"))),
                    "net_amount_due": str(
                        max(
                            item.invoice.total - approved_waivers.get(item.invoice_id, Decimal("0.00")),
                            Decimal("0.00"),
                        )
                    ) if item.invoice_id else None,
                    "requested_amount": str(item.amount),
                    "reason": item.reason,
                    "requested_by": item.requested_by.get_full_name() or item.requested_by.get_username(),
                    "created_at": item.created_at.isoformat(),
                    "billing_month": item.period_start.strftime("%B %Y"),
                    "tenant_name": (
                        item.invoice.tenancy.tenant.name
                        if item.invoice_id
                        else next(
                            (
                                tenancy.tenant.name
                                for tenancy in item.unit.tenancies.all()
                                if tenancy.start_date <= item.period_start
                                and (tenancy.end_date is None or tenancy.end_date >= item.period_start)
                            ),
                            "No active tenancy",
                        )
                    ),
                    "unit_code": item.unit.code,
                    "property_name": item.unit.property.name,
                    "branch_name": item.unit.property.branch.name,
                }
                for item in pending_waivers
            ],
        }
    )


@require_POST
def director_remittance_decision(request, remittance_id):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        return _problem(400, "Invalid request", "A JSON object is required.", "invalid-request")
    membership, error = _director_membership(request, body.get("agency_id"))
    if error:
        return error
    action = body.get("action")
    review_note = str(body.get("review_note") or "").strip()
    if action not in {"approve", "reject"} or (action == "reject" and not review_note):
        return _problem(
            400,
            "Invalid decision",
            "Choose approve, or provide a review note when rejecting.",
            "invalid-landlord-remittance-decision",
        )
    with transaction.atomic():
        remittance = (
            LandlordRemittance.objects.select_for_update(of=("self",))
            .filter(
                pk=remittance_id,
                landlord__branch__agency=membership.agency,
            )
            .select_related("landlord__branch", "prepared_by", "reviewed_by")
            .first()
        )
        if remittance is None:
            return _problem(404, "Remittance not found", "The remittance is not part of this agency.", "landlord-remittance-not-found")
        if remittance.status != LandlordRemittance.STATUS_AWAITING_APPROVAL:
            return _problem(409, "Remittance unavailable", "Only a remittance awaiting approval can be reviewed.", "landlord-remittance-conflict")
        if remittance.prepared_by_id == request.user.pk:
            return _problem(403, "Permission denied", "The remittance preparer cannot review their own remittance.", "landlord-remittance-self-approval-denied")
        remittance.status = (
            LandlordRemittance.STATUS_REJECTED
            if action == "reject"
            else (
                LandlordRemittance.STATUS_NO_PAYOUT
                if remittance.net_payout == 0
                else LandlordRemittance.STATUS_APPROVED
            )
        )
        remittance.reviewed_by = request.user
        remittance.reviewed_at = timezone.now()
        remittance.rejection_reason = review_note if action == "reject" else ""
        try:
            remittance.full_clean()
            remittance.save(
                update_fields=[
                    "status",
                    "reviewed_by",
                    "reviewed_at",
                    "rejection_reason",
                    "updated_at",
                ]
            )
        except (ValidationError, ValueError, IntegrityError) as error:
            return _problem(400, "Invalid decision", str(error), "invalid-landlord-remittance-decision")
        record_audit_event(
            request,
            f"landlord_remittance.{remittance.status}",
            "LandlordRemittance",
            new_values=_remittance_payload(remittance),
            agency=membership.agency,
        )
    return JsonResponse({"remittance": _remittance_payload(remittance)})


@require_GET
def director_audit_logs(request):
    membership, error = _director_membership(request, request.GET.get("agency_id"))
    if error:
        return error
    logs = AuditLog.objects.filter(agency=membership.agency).select_related("user")
    action = request.GET.get("action", "").strip()
    user_search = request.GET.get("user", "").strip()
    start_value = request.GET.get("from", "").strip()
    end_value = request.GET.get("to", "").strip()
    try:
        if start_value:
            logs = logs.filter(timestamp__date__gte=date.fromisoformat(start_value))
        if end_value:
            logs = logs.filter(timestamp__date__lte=date.fromisoformat(end_value))
    except ValueError:
        return _problem(400, "Invalid date range", "from and to must use YYYY-MM-DD.", "invalid-date-range")
    if action:
        logs = logs.filter(action__icontains=action)
    if user_search:
        logs = logs.filter(
            Q(user__email__icontains=user_search)
            | Q(user__username__icontains=user_search)
            | Q(user__profile__name__icontains=user_search)
        )
    return JsonResponse(
        {
            "events": [
                {
                    "id": item.pk,
                    "timestamp": item.timestamp.isoformat(),
                    "user": (
                        item.user.profile.name
                        if item.user_id and hasattr(item.user, "profile")
                        else item.user.get_username()
                        if item.user_id
                        else "System"
                    ),
                    "email": item.user.email if item.user_id else None,
                    "action": item.action,
                    "entity": item.entity,
                    "old_values": item.old_values,
                    "new_values": item.new_values,
                    "ip_address": item.ip_address,
                }
                for item in logs.order_by("-timestamp", "-id")[:200]
            ]
        }
    )


def _settings_payload(settings_record, branches):
    branch_rows = []
    for branch in branches.order_by("name", "id"):
        policy = getattr(branch, "arrears_policy", None)
        branch_rows.append(
            {
                "id": branch.pk,
                "name": branch.name,
                "town": branch.town,
                "paybill_numbers": branch.paybill_numbers,
                "grace_period_days": policy.grace_days if policy else settings_record.grace_period_days,
                "penalty_type": policy.penalty_type if policy else settings_record.penalty_type,
                "penalty_rate": str(policy.penalty_rate if policy else settings_record.penalty_rate),
                "penalty_flat_fee": str(policy.penalty_flat_fee if policy else settings_record.penalty_flat_fee),
            }
        )
    return {
        "agency": {"id": settings_record.agency_id, "name": settings_record.agency.name},
        "settings": {
            "mri_tax_rate": str(settings_record.mri_tax_rate),
            "commission_vat_enabled": settings_record.commission_vat_enabled,
            "commission_vat_rate": str(settings_record.commission_vat_rate),
            "grace_period_days": settings_record.grace_period_days,
            "penalty_type": settings_record.penalty_type,
            "penalty_rate": str(settings_record.penalty_rate),
            "penalty_flat_fee": str(settings_record.penalty_flat_fee),
            "sms_templates": settings_record.sms_templates,
            "updated_at": settings_record.updated_at.isoformat(),
        },
        "branches": branch_rows,
    }


@require_http_methods(["GET", "PATCH"])
def director_settings(request):
    if request.method == "GET":
        agency_id = request.GET.get("agency_id")
    else:
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict):
            return _problem(400, "Invalid request", "A JSON object is required.", "invalid-request")
        agency_id = body.get("agency_id")
    membership, error = _director_membership(request, agency_id)
    if error:
        return error
    settings_record, _ = AgencySettings.objects.get_or_create(agency=membership.agency)
    branches = Branch.objects.filter(agency=membership.agency).select_related("arrears_policy")
    if request.method == "GET":
        return JsonResponse(_settings_payload(settings_record, branches))

    allowed = {
        "mri_tax_rate",
        "commission_vat_enabled",
        "commission_vat_rate",
        "grace_period_days",
        "penalty_type",
        "penalty_rate",
        "penalty_flat_fee",
        "sms_templates",
        "branch_paybills",
    }
    if set(body) - allowed - {"agency_id"}:
        return _problem(400, "Invalid settings", "The request contains unsupported settings.", "invalid-agency-settings")
    old_values = {
        "mri_tax_rate": str(settings_record.mri_tax_rate),
        "commission_vat_enabled": settings_record.commission_vat_enabled,
        "commission_vat_rate": str(settings_record.commission_vat_rate),
        "grace_period_days": settings_record.grace_period_days,
        "penalty_type": settings_record.penalty_type,
        "penalty_rate": str(settings_record.penalty_rate),
        "penalty_flat_fee": str(settings_record.penalty_flat_fee),
        "sms_templates": settings_record.sms_templates,
        "branch_paybills": {
            str(branch.pk): branch.paybill_numbers for branch in branches
        },
    }
    decimal_fields = ("mri_tax_rate", "commission_vat_rate", "penalty_rate", "penalty_flat_fee")
    try:
        for field in decimal_fields:
            if field in body:
                setattr(settings_record, field, Decimal(str(body[field])))
        if "grace_period_days" in body:
            value = body["grace_period_days"]
            if isinstance(value, bool):
                raise ValueError
            settings_record.grace_period_days = int(value)
    except (InvalidOperation, TypeError, ValueError):
        return _problem(400, "Invalid settings", "Tax and penalty rates must be valid numbers and grace_period_days must be an integer.", "invalid-agency-settings")
    if "commission_vat_enabled" in body:
        if not isinstance(body["commission_vat_enabled"], bool):
            return _problem(400, "Invalid settings", "commission_vat_enabled must be a boolean.", "invalid-agency-settings")
        settings_record.commission_vat_enabled = body["commission_vat_enabled"]
    if "penalty_type" in body:
        settings_record.penalty_type = body["penalty_type"]
    if "sms_templates" in body:
        templates = body["sms_templates"]
        if not isinstance(templates, dict) or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or len(key) > 80
            or len(value) > 500
            for key, value in templates.items()
        ):
            return _problem(400, "Invalid SMS templates", "SMS templates must be an object with text values no longer than 500 characters.", "invalid-agency-settings")
        settings_record.sms_templates = templates
    branch_paybills = body.get("branch_paybills", {})
    if not isinstance(branch_paybills, dict):
        return _problem(400, "Invalid branch Paybill", "branch_paybills must be an object keyed by assigned branch IDs.", "invalid-agency-settings")
    branch_ids = {str(branch.pk): branch for branch in branches}
    for branch_id, numbers in branch_paybills.items():
        if branch_id not in branch_ids:
            return _problem(400, "Invalid branch Paybill", "A Paybill entry references a branch outside this agency.", "invalid-agency-settings")
        if not isinstance(numbers, list) or any(
            not isinstance(number, str) or not number.strip() or len(number.strip()) > 32
            for number in numbers
        ):
            return _problem(400, "Invalid branch Paybill", "Each branch Paybill value must be a list of non-empty strings up to 32 characters.", "invalid-agency-settings")
    settings_record.updated_by = request.user
    try:
        settings_record.full_clean()
        with transaction.atomic():
            settings_record.save()
            if any(
                field in body
                for field in ("grace_period_days", "penalty_type", "penalty_rate", "penalty_flat_fee")
            ):
                for branch in branches:
                    ArrearsPolicy.objects.update_or_create(
                        branch=branch,
                        defaults={
                            "grace_days": settings_record.grace_period_days,
                            "penalty_type": settings_record.penalty_type,
                            "penalty_rate": settings_record.penalty_rate,
                            "penalty_flat_fee": settings_record.penalty_flat_fee,
                        },
                    )
            for branch_id, numbers in branch_paybills.items():
                branch = branch_ids[branch_id]
                branch.paybill_numbers = list(dict.fromkeys(number.strip() for number in numbers))
                branch.save(update_fields=["paybill_numbers"])
            record_audit_event(
                request,
                "agency.settings_updated",
                "AgencySettings",
                old_values=old_values,
                new_values={
                    "agency_id": membership.agency_id,
                    "mri_tax_rate": str(settings_record.mri_tax_rate),
                    "commission_vat_enabled": settings_record.commission_vat_enabled,
                    "commission_vat_rate": str(settings_record.commission_vat_rate),
                    "grace_period_days": settings_record.grace_period_days,
                    "penalty_type": settings_record.penalty_type,
                    "penalty_rate": str(settings_record.penalty_rate),
                    "penalty_flat_fee": str(settings_record.penalty_flat_fee),
                    "sms_templates": settings_record.sms_templates,
                    "branch_paybills": {
                        str(branch.pk): branch.paybill_numbers
                        for branch in branches
                    },
                },
                agency=membership.agency,
            )
    except (ValidationError, IntegrityError, ValueError) as error:
        return _problem(400, "Invalid settings", str(error), "invalid-agency-settings")
    return JsonResponse(_settings_payload(settings_record, branches))
