import calendar
import csv
import hashlib
import html
import json
import logging
import mimetypes
import re
import secrets
import smtplib
import uuid
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from io import BytesIO, StringIO

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.contrib.auth import (
    authenticate,
    get_user_model,
    login,
    logout,
    password_validation,
)
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, IntegrityError, models, transaction
from django.db.models import Count, DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Greatest
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from kombu.exceptions import OperationalError as BrokerOperationalError
from openpyxl import load_workbook

from users.access import accessible_branches
from users.arrears import arrears_for_accessible_invoices, invoice_arrears
from users.audit import record_audit_event
from users.billing import generate_monthly_invoice, month_end, water_reading_status
from users import daraja
from users.email import email_delivery_is_configured, send_email
from users.legacy_imports import import_legacy_data
from users.models import (
    AgencyMembership,
    Announcement,
    ArrearsPaymentPlan,
    ArrearsPaymentPlanInstalment,
    ArrearsWaiver,
    AuditLog,
    BankStatementImport,
    BankStatementRow,
    Block,
    Branch,
    CaretakerInvitation,
    DemandLetter,
    DepositAccount,
    DepositEntry,
    DirectorActivationChallenge,
    Floor,
    Invoice,
    InvoiceLine,
    InvoiceNumberSequence,
    InvoiceWaiverRequest,
    Landlord,
    LandlordActivationChallenge,
    LandlordRemittance,
    MaintenanceCostAllocation,
    MaintenanceQuotation,
    MaintenanceTicket,
    MaintenanceTicketComment,
    ManagementAgreement,
    MeterReading,
    MeterReadingDraft,
    MoveInCharge,
    MoveInChecklistItem,
    MoveInPackage,
    MoveInPayment,
    MoveInSettlement,
    MoveOutNotice,
    MoveOutSettlement,
    MpesaCallback,
    Payment,
    PaymentAllocation,
    Property,
    PropertyBillingSettings,
    PropertyCaretakerAssignment,
    PropertyDocument,
    PropertyExpense,
    PropertyInspection,
    PropertyRecurringCharge,
    PropertyUnitType,
    PropertyUnitTypeUtility,
    PropertyViewing,
    Receipt,
    RentStkRequest,
    SMSLoginChallenge,
    StaffActivationChallenge,
    Tenancy,
    Tenant,
    TenantActivationChallenge,
    TenantCredit,
    TenantDocument,
    TOTPLoginChallenge,
    Unit,
    UnitCharge,
    UnitReservation,
    UserProfile,
    UtilityConfiguration,
    Vendor,
    ViewingLead,
)
from users.move_in import confirmed_invoice_amount, sync_move_in_occupancy
from users.payment_allocations import (
    apply_tenant_credit,
    available_tenant_credit,
    lock_tenant_invoice,
)
from users.object_storage import (
    delete_private_object,
    read_private_object,
    upload_private_object,
)
from users.roles import (
    AGENCY_STAFF_ROLE_NAMES,
    BRANCH_MANAGER_ROLE,
    CARETAKER_ROLE,
    LANDLORD_MANAGEMENT_ROLE_NAMES,
    LANDLORD_PORTAL_ROLE,
    SUPER_ADMIN_ROLE,
    TENANT_PORTAL_ROLE,
    requires_finance_mfa,
)
from users.sms import queue_template_sms
from users.totp import (
    decrypt_secret,
    encrypt_secret,
    generate_secret,
    matching_counter,
    provisioning_uri,
)
from users.whatsapp import queue_template_whatsapp_if_enabled

logger = logging.getLogger(__name__)


def _send_password_reset_email(user, reset_url, *, initial_setup=False):
    if not settings.PUBLIC_SITE_URL:
        raise ImproperlyConfigured("PUBLIC_SITE_URL must be configured for password recovery.")

    if initial_setup:
        subject = "Set up your Legacy Real Estate account"
        text_content = (
            "A Director account has been provisioned for you.\n\n"
            f"Use this one-time link to choose your password: {reset_url}\n\n"
            "If you did not expect this invitation, contact the person who "
            "coordinated your account setup."
        )
        html_content = (
            "<p>A Director account has been provisioned for you.</p>"
            f'<p><a style="display:inline-block;padding:12px 20px;border-radius:8px;background-color:#123b2c;color:#ffffff;font-weight:700;text-decoration:none;" href="{html.escape(reset_url, quote=True)}">'
            "Choose your password</a></p>"
            "<p>This is a one-time link. If you did not expect this invitation, "
            "contact the person who coordinated your account setup.</p>"
        )
    else:
        subject = "Reset your Legacy Real Estate password"
        text_content = (
            "We received a request to reset your Legacy Real Estate password.\n\n"
            f"Use this link to choose a new password: {reset_url}\n\n"
            "If you did not request this change, you can ignore this email."
        )
        html_content = (
            "<p>We received a request to reset your Legacy Real Estate password.</p>"
            f'<p><a style="display:inline-block;padding:12px 20px;border-radius:8px;background-color:#123b2c;color:#ffffff;font-weight:700;text-decoration:none;" href="{html.escape(reset_url, quote=True)}">'
            "Choose a new password</a></p>"
            "<p>If you did not request this change, you can ignore this email.</p>"
        )
    send_email(
        user.email,
        subject,
        text_content,
        html_content=html_content,
    )


def _send_director_activation_email(user, challenge_id, code):
    lifetime_minutes = max(1, (settings.AUTH_DIRECTOR_ACTIVATION_TTL_SECONDS + 59) // 60)
    activation_url = (
        f"{settings.PUBLIC_SITE_URL.rstrip('/')}/activate-director"
        f"?challenge_id={challenge_id}"
    )
    text_content = (
        f"Hello {user.profile.name},\n\n"
        "Your agency Director account is ready to activate. "
        f"Your one-time activation code is {code}. It expires in {lifetime_minutes} minutes.\n\n"
        f"Activate your account: {activation_url}\n\n"
        "If you did not expect this invitation, contact your platform administrator."
    )
    html_content = (
        f"<p>Hello {html.escape(user.profile.name)},</p>"
        "<p>Your agency Director account is ready to activate.</p>"
        f'<p>Your one-time activation code is <span style="display:inline-block;padding:8px 14px;border-radius:8px;background-color:#e8f3ec;color:#123b2c;font-size:20px;font-weight:700;letter-spacing:3px;">{code}</span>. '
        f"It expires in {lifetime_minutes} minutes.</p>"
        f'<p><a style="display:inline-block;padding:12px 20px;border-radius:8px;background-color:#123b2c;color:#ffffff;font-weight:700;text-decoration:none;" href="{html.escape(activation_url, quote=True)}">Activate your account</a></p>'
        "<p>If you did not expect this invitation, contact your platform administrator.</p>"
    )
    send_email(
        user.email,
        "Activate your Legacy Real Estate Director account",
        text_content,
        html_content=html_content,
        recipient_name=user.profile.name,
    )


def _send_staff_activation_email(
    user,
    challenge_id,
    code,
    *,
    portal_name="agency staff",
    activation_path="activate-staff",
    contact="your agency Director",
):
    lifetime_minutes = max(1, (settings.AUTH_DIRECTOR_ACTIVATION_TTL_SECONDS + 59) // 60)
    activation_url = (
        f"{settings.PUBLIC_SITE_URL.rstrip('/')}/{activation_path}"
        f"?challenge_id={challenge_id}"
    )
    text_content = (
        f"Hello {user.profile.name},\n\n"
        f"Your {portal_name} account is ready to activate. "
        f"Your one-time activation code is {code}. It expires in {lifetime_minutes} minutes.\n\n"
        f"Activate your account: {activation_url}\n\n"
        f"If you did not expect this invitation, contact {contact}."
    )
    html_content = (
        f"<p>Hello {html.escape(user.profile.name)},</p>"
        f"<p>Your {html.escape(portal_name)} account is ready to activate.</p>"
        f'<p>Your one-time activation code is <span style="display:inline-block;padding:8px 14px;border-radius:8px;background-color:#e8f3ec;color:#123b2c;font-size:20px;font-weight:700;letter-spacing:3px;">{code}</span>. '
        f"It expires in {lifetime_minutes} minutes.</p>"
        f'<p><a style="display:inline-block;padding:12px 20px;border-radius:8px;background-color:#123b2c;color:#ffffff;font-weight:700;text-decoration:none;" href="{html.escape(activation_url, quote=True)}">Activate your account</a></p>'
        f"<p>If you did not expect this invitation, contact {html.escape(contact)}.</p>"
    )
    send_email(
        user.email,
        f"Activate your Legacy Real Estate {portal_name} account",
        text_content,
        html_content=html_content,
        recipient_name=user.profile.name,
    )


def _send_landlord_activation_email(user, challenge_id, code):
    _send_staff_activation_email(
        user,
        challenge_id,
        code,
        portal_name="landlord portal",
        activation_path="activate-landlord",
        contact="your Property Officer",
    )


def _send_caretaker_activation_email(email_address, property_name, activation_url, code):
    if not settings.PUBLIC_SITE_URL:
        raise ImproperlyConfigured("PUBLIC_SITE_URL must be configured for caretaker activation.")
    lifetime_minutes = max(1, (settings.AUTH_DIRECTOR_ACTIVATION_TTL_SECONDS + 59) // 60)
    text_content = (
        f"Your caretaker account for {property_name} is ready to activate.\n"
        f"One-time code: {code} (expires in {lifetime_minutes} minutes).\n\n"
        f"Activate your account: {activation_url}\n"
    )
    safe_property_name = html.escape(property_name)
    safe_url = html.escape(activation_url, quote=True)
    html_content = (
        f"<p>Your caretaker account for {safe_property_name} is ready to activate.</p>"
        f'<p>One-time code: <span style="display:inline-block;padding:8px 14px;border-radius:8px;background-color:#e8f3ec;color:#123b2c;font-size:20px;font-weight:700;letter-spacing:3px;">{code}</span>. It expires in {lifetime_minutes} minutes.</p>'
        f'<p><a style="display:inline-block;padding:12px 20px;border-radius:8px;background-color:#123b2c;color:#ffffff;font-weight:700;text-decoration:none;" href="{safe_url}">Activate your account</a></p>'
    )
    send_email(
        email_address,
        f"Activate your {property_name} caretaker account",
        text_content,
        html_content=html_content,
    )


def _send_tenant_activation_email(
    full_name,
    email,
    unit_code,
    property_name,
    activation_url,
    activation_code,
    *,
    phone_verification_required=False,
):
    if not settings.PUBLIC_SITE_URL:
        raise ImproperlyConfigured("PUBLIC_SITE_URL must be configured for tenant activation.")
    code_message = (
        f"Your six-digit activation OTP is {activation_code}.\n"
        + (
            "The same code was also sent by SMS to your registered phone.\n"
            if phone_verification_required
            else ""
        )
    )
    text_content = (
        f"Hi {full_name}, welcome to Legacy Real Estate!\n\n"
        f"Your tenant account for {property_name}, unit {unit_code}, has been successfully onboarded.\n"
        f"{code_message}"
        f"Use this secure link and code to create your password: {activation_url}\n\n"
        "The link expires in 48 hours."
    )
    safe_full_name = html.escape(full_name)
    safe_email = html.escape(email)
    safe_property_name = html.escape(property_name)
    safe_unit_code = html.escape(unit_code)
    safe_url = html.escape(activation_url, quote=True)
    phone_code_note = (
        "<p>The same code was also sent by SMS to your registered phone.</p>"
        if phone_verification_required
        else ""
    )
    code_html = (
        f'<p>Your six-digit activation OTP is <span style="display:inline-block;padding:8px 14px;border-radius:8px;background-color:#e8f3ec;color:#123b2c;font-size:20px;font-weight:700;letter-spacing:3px;">{html.escape(activation_code)}</span>.</p>'
        f"{phone_code_note}"
    )
    html_content = (
        f"<p>Hi {safe_full_name}, welcome to Legacy Real Estate!</p>"
        f"<p>Your tenant account for {safe_property_name}, unit {safe_unit_code}, has been successfully onboarded.</p>"
        f"{code_html}"
        f'<p><a style="display:inline-block;padding:12px 20px;border-radius:8px;background-color:#123b2c;color:#ffffff;font-weight:700;text-decoration:none;" href="{safe_url}">Activate your account</a></p>'
        f"<p>This link is for {safe_email} and expires in 48 hours.</p>"
    )
    send_email(
        email,
        "Welcome to Legacy Real Estate - activate your tenant account",
        text_content,
        html_content=html_content,
        recipient_name=full_name,
    )


def _new_tenant_activation_code():
    for _ in range(20):
        code = f"{secrets.randbelow(1_000_000):06d}"
        if not TenantActivationChallenge.objects.filter(
            code_hash=_activation_hash(code)
        ).exists():
            return code
    raise RuntimeError("Could not generate a unique tenant activation code.")


def _send_move_in_invoice_activation(request, invoice_id, *, force=False):
    invoice = (
        Invoice.objects.filter(
            pk=invoice_id,
            status=Invoice.STATUS_ISSUED,
            tenancy__unit__property__in=_accessible_properties(request.user),
        )
        .select_related("tenancy__tenant", "tenancy__unit__property")
        .first()
    )
    if invoice is None or not MoveInPackage.objects.filter(tenancy=invoice.tenancy).exists():
        return None
    if not InvoiceLine.objects.filter(
        invoice=invoice,
        metadata__move_in_charge_id__isnull=False,
    ).exists():
        return None
    confirmed_paid = confirmed_invoice_amount(invoice)
    if confirmed_paid < invoice.total:
        return None

    sync_move_in_occupancy(invoice.tenancy_id)
    tenant = invoice.tenancy.tenant
    if tenant.portal_user_id:
        return None
    if not tenant.email:
        return {"activation_email_sent": False, "activation_email_error": "The tenant has no email address."}

    now = timezone.now()
    token = secrets.token_urlsafe(32)
    activation_code = _new_tenant_activation_code()
    with transaction.atomic():
        locked_invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        paid_amount = (
            PaymentAllocation.objects.filter(invoice=locked_invoice)
            .exclude(
                payment__instrument_status__in=(
                    Payment.INSTRUMENT_PENDING,
                    Payment.INSTRUMENT_BOUNCED,
                )
            )
            .aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]
        )
        if paid_amount < locked_invoice.total:
            return None
        existing_sent = TenantActivationChallenge.objects.filter(
            tenant=tenant,
            unit=invoice.tenancy.unit,
            used_at__isnull=True,
            expires_at__gt=now,
            email_sent_at__isnull=False,
        ).exists()
        if existing_sent and not force:
            return {"activation_email_sent": True, "activation_email_error": None}
        TenantActivationChallenge.objects.filter(
            tenant=tenant,
            unit=invoice.tenancy.unit,
            used_at__isnull=True,
        ).update(used_at=now)
        challenge = TenantActivationChallenge.objects.create(
            tenant=tenant,
            unit=invoice.tenancy.unit,
            token_hash=_activation_hash(token),
            code_hash=_activation_hash(activation_code),
            expires_at=now + timedelta(hours=48),
        )

    activation_url = f"{settings.PUBLIC_SITE_URL.rstrip('/')}/activate?token={token}"
    try:
        _send_tenant_activation_email(
            tenant.name,
            tenant.email,
            invoice.tenancy.unit.code,
            invoice.tenancy.unit.property.name,
            activation_url,
            activation_code,
        )
    except (ImproperlyConfigured, RuntimeError) as exc:
        challenge.email_delivery_error = str(exc)[:500]
        challenge.save(update_fields=["email_delivery_error"])
        logger.error("Could not deliver a paid move-in activation email (%s).", type(exc).__name__)
        return {
            "activation_email_sent": False,
            "activation_email_error": f"Payment is confirmed, but the tenant activation email was not sent: {exc}",
        }

    challenge.email_sent_at = timezone.now()
    challenge.email_delivery_error = ""
    challenge.save(update_fields=["email_sent_at", "email_delivery_error"])
    record_audit_event(
        request,
        "tenant_activation.emailed",
        "TenantActivationChallenge",
        new_values={
            "id": challenge.pk,
            "tenant_id": tenant.pk,
            "tenancy_id": invoice.tenancy_id,
            "invoice_id": invoice.pk,
            "expires_at": challenge.expires_at.isoformat(),
        },
    )
    return {"activation_email_sent": True, "activation_email_error": None}


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
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    return bool(
        profile
        and profile.agency_memberships.filter(
            agency__is_active=True,
            is_active=True,
            roles__name__in=LANDLORD_MANAGEMENT_ROLE_NAMES,
        ).exists()
    )


def _property_officer_membership(user, *, agency_id=None, branch_id=None):
    profile = getattr(user, "profile", None)
    if not user.is_authenticated or profile is None:
        return None, _problem(
            401 if not user.is_authenticated else 403,
            "Authentication required" if not user.is_authenticated else "Permission denied",
            "Sign in as a Property Officer to manage landlord portal accounts.",
            "property-officer-required",
        )
    memberships = AgencyMembership.objects.filter(
        profile=profile,
        agency__is_active=True,
        is_active=True,
        roles__name="Property Officer / Agent",
    )
    if agency_id is not None:
        memberships = memberships.filter(agency_id=agency_id)
    if branch_id is not None:
        memberships = memberships.filter(branches__pk=branch_id)
    membership = memberships.select_related("agency").distinct().first()
    if membership is None:
        return None, _problem(
            403,
            "Permission denied",
            "You cannot manage landlord portal accounts outside your Property Officer branch scope.",
            "property-officer-scope-denied",
        )
    return membership, None


def _property_officer_branches(user):
    return Branch.objects.filter(
        pk__in=AgencyMembership.objects.filter(
            profile__user=user,
            agency__is_active=True,
            is_active=True,
            roles__name="Property Officer / Agent",
        ).values_list("branches__pk", flat=True)
    ).distinct()


def _viewing_lead_payload(lead):
    return {
        "id": lead.pk,
        "branch_id": lead.branch_id,
        "branch_name": lead.branch.name,
        "property_id": lead.property_id,
        "property_name": lead.property.name if lead.property_id else None,
        "unit_id": lead.unit_id,
        "unit_code": lead.unit.code if lead.unit_id else None,
        "full_name": lead.full_name,
        "phone": lead.phone,
        "email": lead.email,
        "preference": lead.preference,
        "budget": str(lead.budget) if lead.budget is not None else None,
        "status": lead.status,
        "notes": lead.notes,
        "created_at": lead.created_at.isoformat(),
        "viewings": [
            {
                "id": viewing.pk,
                "scheduled_for": viewing.scheduled_for.isoformat(),
                "status": viewing.status,
                "notes": viewing.notes,
                "assigned_to": viewing.assigned_to_id,
                "assigned_to_name": (
                    viewing.assigned_to.get_full_name() or viewing.assigned_to.get_username()
                    if viewing.assigned_to_id
                    else None
                ),
            }
            for viewing in lead.viewings.all()
        ],
    }


def _property_officer_lead_access(request):
    if not request.user.is_authenticated:
        return None, _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    _, error = _property_officer_membership(request.user)
    if error:
        return None, error
    branches = _property_officer_branches(request.user)
    if not branches.exists():
        return None, _problem(403, "Permission denied", "No Property Officer branches are assigned.", "property-officer-scope-denied")
    return branches, None


@require_http_methods(["GET", "POST"])
def property_officer_leads(request):
    branches, error = _property_officer_lead_access(request)
    if error:
        return error
    if request.method == "GET":
        leads = ViewingLead.objects.filter(branch__in=branches).select_related(
            "branch", "property", "unit"
        ).prefetch_related("viewings__assigned_to")
        return JsonResponse({"leads": [_viewing_lead_payload(lead) for lead in leads]})

    body = _json_body(request)
    required = ("branch_id", "full_name", "phone")
    if body is None or any(not str(body.get(field, "")).strip() for field in required):
        return _problem(400, "Invalid lead", "branch_id, full_name, and phone are required.", "invalid-lead")
    try:
        branch = branches.get(pk=int(body["branch_id"]))
    except (ValueError, TypeError, Branch.DoesNotExist, Branch.MultipleObjectsReturned):
        return _problem(404, "Branch not found", "The branch is unavailable or outside your access scope.", "branch-not-found")
    property_obj = None
    unit = None
    if body.get("property_id") not in (None, ""):
        try:
            property_obj = Property.objects.get(pk=int(body["property_id"]), branch=branch)
        except (ValueError, TypeError, Property.DoesNotExist):
            return _problem(400, "Invalid property", "The property must belong to the selected branch.", "invalid-lead")
    if body.get("unit_id") not in (None, ""):
        try:
            unit = Unit.objects.select_related("property").get(pk=int(body["unit_id"]), property__branch=branch)
        except (ValueError, TypeError, Unit.DoesNotExist):
            return _problem(400, "Invalid unit", "The unit must belong to the selected branch.", "invalid-lead")
        if property_obj and unit.property_id != property_obj.pk:
            return _problem(400, "Invalid unit", "The unit must belong to the selected property.", "invalid-lead")
        if property_obj is None:
            property_obj = unit.property
    budget = None
    if body.get("budget") not in (None, ""):
        try:
            budget = Decimal(str(body["budget"]))
        except (InvalidOperation, TypeError, ValueError):
            return _problem(400, "Invalid budget", "budget must be a non-negative number.", "invalid-budget")
        if not budget.is_finite() or budget < 0:
            return _problem(400, "Invalid budget", "budget must be a non-negative number.", "invalid-budget")
    lead = ViewingLead(
        branch=branch,
        property=property_obj,
        unit=unit,
        full_name=str(body["full_name"]).strip(),
        phone=str(body["phone"]).strip(),
        email=str(body.get("email", "")).strip(),
        preference=str(body.get("preference", "")).strip(),
        budget=budget,
        notes=str(body.get("notes", "")).strip(),
        created_by=request.user,
    )
    try:
        lead.full_clean()
        lead.save()
    except ValidationError as exc:
        return _problem(400, "Invalid lead", str(exc.message_dict), "invalid-lead")
    record_audit_event(request, "viewing_lead.created", "ViewingLead", new_values=_viewing_lead_payload(lead))
    return JsonResponse({"lead": _viewing_lead_payload(lead)}, status=201)


@require_POST
def update_property_officer_lead_status(request, lead_id):
    branches, error = _property_officer_lead_access(request)
    if error:
        return error
    lead = ViewingLead.objects.filter(pk=lead_id, branch__in=branches).select_related(
        "branch", "property", "unit"
    ).prefetch_related("viewings__assigned_to").first()
    if lead is None:
        return _problem(404, "Lead not found", "The lead is unavailable or outside your access scope.", "lead-not-found")
    body = _json_body(request)
    status = body.get("status") if body else None
    if not isinstance(status, str) or status not in dict(ViewingLead.STATUS_CHOICES):
        return _problem(400, "Invalid status", "The lead status is not supported.", "invalid-status")
    old_status = lead.status
    lead.status = status
    lead.notes = str(body.get("notes", lead.notes)).strip()
    lead.save(update_fields=["status", "notes", "updated_at"])
    payload = _viewing_lead_payload(lead)
    record_audit_event(
        request,
        "viewing_lead.status_changed",
        "ViewingLead",
        old_values={"id": lead.pk, "status": old_status},
        new_values={"id": lead.pk, "status": lead.status},
    )
    return JsonResponse({"lead": payload})


def _property_viewing_payload(viewing):
    return {
        "id": viewing.pk,
        "lead_id": viewing.lead_id,
        "lead_name": viewing.lead.full_name,
        "phone": viewing.lead.phone,
        "branch_id": viewing.lead.branch_id,
        "property_name": viewing.lead.property.name if viewing.lead.property_id else None,
        "unit_code": viewing.lead.unit.code if viewing.lead.unit_id else None,
        "scheduled_for": viewing.scheduled_for.isoformat(),
        "status": viewing.status,
        "notes": viewing.notes,
        "assigned_to": viewing.assigned_to_id,
        "assigned_to_name": (
            viewing.assigned_to.get_full_name() or viewing.assigned_to.get_username()
            if viewing.assigned_to_id
            else None
        ),
    }


@require_http_methods(["GET", "POST"])
def property_officer_viewings(request):
    branches, error = _property_officer_lead_access(request)
    if error:
        return error
    if request.method == "GET":
        viewings = PropertyViewing.objects.filter(lead__branch__in=branches).select_related(
            "lead__branch", "lead__property", "lead__unit", "assigned_to"
        )
        return JsonResponse({"viewings": [_property_viewing_payload(viewing) for viewing in viewings]})

    body = _json_body(request)
    try:
        lead_id = int(body["lead_id"]) if body else 0
    except (KeyError, ValueError, TypeError):
        lead_id = 0
    lead = ViewingLead.objects.filter(pk=lead_id, branch__in=branches).first()
    if lead is None:
        return _problem(404, "Lead not found", "Select a lead in your assigned branch.", "lead-not-found")
    scheduled_for = parse_datetime(str(body.get("scheduled_for", ""))) if body else None
    if scheduled_for is None:
        return _problem(400, "Invalid viewing", "scheduled_for must be a valid ISO date-time.", "invalid-viewing")
    if timezone.is_naive(scheduled_for):
        scheduled_for = timezone.make_aware(scheduled_for, timezone.get_current_timezone())
    assigned_to = None
    if body.get("assigned_to") not in (None, ""):
        try:
            assigned_to = get_user_model().objects.filter(
                pk=int(body["assigned_to"]),
                is_active=True,
                profile__agency_memberships__agency=lead.branch.agency,
                profile__agency_memberships__agency__is_active=True,
                profile__agency_memberships__is_active=True,
                profile__agency_memberships__branches=lead.branch,
                profile__agency_memberships__roles__name__in=AGENCY_STAFF_ROLE_NAMES,
            ).distinct().first()
        except (ValueError, TypeError):
            assigned_to = None
        if assigned_to is None:
            return _problem(400, "Invalid assignee", "The assignee must be active staff in the lead branch.", "invalid-assignee")
    viewing = PropertyViewing(
        lead=lead,
        scheduled_for=scheduled_for,
        notes=str(body.get("notes", "")).strip(),
        scheduled_by=request.user,
        assigned_to=assigned_to,
    )
    try:
        viewing.full_clean()
        viewing.save()
    except ValidationError as exc:
        return _problem(400, "Invalid viewing", str(exc.message_dict), "invalid-viewing")
    if lead.status == ViewingLead.STATUS_NEW:
        lead.status = ViewingLead.STATUS_VIEWING_BOOKED
        lead.save(update_fields=["status", "updated_at"])
    record_audit_event(request, "property_viewing.created", "PropertyViewing", new_values=_property_viewing_payload(viewing))
    return JsonResponse({"viewing": _property_viewing_payload(viewing)}, status=201)


@require_POST
def property_officer_legacy_import(request):
    branches, error = _property_officer_lead_access(request)
    if error:
        return error
    property_obj = None
    property_id = request.POST.get("property_id")
    if property_id:
        try:
            property_obj = Property.objects.select_related("branch").get(
                pk=int(property_id),
                branch__in=branches,
            )
        except (ValueError, TypeError, Property.DoesNotExist, Property.MultipleObjectsReturned):
            return _problem(
                404,
                "Property not found",
                "The property is unavailable or outside your access scope.",
                "property-not-found",
            )
        branch = property_obj.branch
    else:
        try:
            branch = branches.get(pk=int(request.POST.get("branch_id", "")))
        except (ValueError, TypeError, Branch.DoesNotExist, Branch.MultipleObjectsReturned):
            return _problem(404, "Branch not found", "The branch is unavailable or outside your access scope.", "branch-not-found")
    report = import_legacy_data(
        branch,
        request.user,
        request.FILES.getlist("files"),
        commit=request.POST.get("commit", "").casefold() == "true",
        property_obj=property_obj,
    )
    report["mode"] = "committed" if request.POST.get("commit", "").casefold() == "true" else "preview"
    if report["errors"]:
        return JsonResponse(report, status=400)
    if request.POST.get("commit", "").casefold() == "true":
        record_audit_event(
            request,
            "legacy_data.imported",
            "Branch",
            new_values={
                "branch_id": branch.pk,
                "property_id": property_obj.pk if property_obj else None,
                "created": report["created"],
                "skipped_count": len(report["skipped"]),
                "tenant_activations": {
                    "prepared": report.get("activation", {}).get("prepared", 0),
                    "sent": report.get("activation", {}).get("sent", 0),
                    "failed_count": len(
                        report.get("activation", {}).get("failed", [])
                    ),
                },
            },
        )
    return JsonResponse(report)


@require_http_methods(["PATCH"])
def update_property_officer_viewing(request, viewing_id):
    branches, error = _property_officer_lead_access(request)
    if error:
        return error
    viewing = PropertyViewing.objects.filter(
        pk=viewing_id, lead__branch__in=branches
    ).select_related("lead__branch", "lead__property", "lead__unit", "assigned_to").first()
    if viewing is None:
        return _problem(404, "Viewing not found", "The viewing is unavailable or outside your access scope.", "viewing-not-found")
    body = _json_body(request)
    status = body.get("status") if body else None
    if not isinstance(status, str) or status not in dict(PropertyViewing.STATUS_CHOICES):
        return _problem(400, "Invalid status", "The viewing status is not supported.", "invalid-status")
    old_status = viewing.status
    old_scheduled_for = viewing.scheduled_for.isoformat()
    viewing.status = status
    if body.get("notes") is not None:
        viewing.notes = str(body["notes"]).strip()
    if body.get("scheduled_for") is not None:
        scheduled_for = parse_datetime(str(body["scheduled_for"]))
        if scheduled_for is None:
            return _problem(400, "Invalid viewing", "scheduled_for must be a valid ISO date-time.", "invalid-viewing")
        if timezone.is_naive(scheduled_for):
            scheduled_for = timezone.make_aware(scheduled_for, timezone.get_current_timezone())
        viewing.scheduled_for = scheduled_for
    if "assigned_to" in body:
        if body["assigned_to"] in (None, ""):
            viewing.assigned_to = None
        else:
            try:
                viewing.assigned_to = get_user_model().objects.filter(
                    pk=int(body["assigned_to"]),
                    is_active=True,
                    profile__agency_memberships__agency=viewing.lead.branch.agency,
                    profile__agency_memberships__agency__is_active=True,
                    profile__agency_memberships__is_active=True,
                    profile__agency_memberships__branches=viewing.lead.branch,
                    profile__agency_memberships__roles__name__in=AGENCY_STAFF_ROLE_NAMES,
                ).distinct().first()
            except (ValueError, TypeError):
                viewing.assigned_to = None
            if viewing.assigned_to is None:
                return _problem(400, "Invalid assignee", "The assignee must be active staff in the lead branch.", "invalid-assignee")
    try:
        viewing.full_clean()
        viewing.save(update_fields=["status", "notes", "scheduled_for", "assigned_to", "updated_at"])
    except ValidationError as exc:
        return _problem(400, "Invalid viewing", str(exc.message_dict), "invalid-viewing")
    record_audit_event(
        request,
        "property_viewing.status_changed",
        "PropertyViewing",
        old_values={"id": viewing.pk, "status": old_status, "scheduled_for": old_scheduled_for},
        new_values={
            "id": viewing.pk,
            "status": viewing.status,
            "scheduled_for": viewing.scheduled_for.isoformat(),
            "assigned_to": viewing.assigned_to_id,
        },
    )
    return JsonResponse({"viewing": _property_viewing_payload(viewing)})


def _has_role(user, role_name):
    profile = getattr(user, "profile", None)
    if profile is not None and profile.agency_memberships.exists():
        return profile.agency_memberships.filter(
            agency__is_active=True,
            is_active=True,
            roles__name=role_name,
        ).exists()
    return user.groups.filter(name=role_name).exists()


def _is_caretaker(user):
    return _has_role(user, CARETAKER_ROLE)


def _accessible_branches(user):
    return accessible_branches(user)


MANAGER_ROLE_NAMES = (SUPER_ADMIN_ROLE, BRANCH_MANAGER_ROLE)


def _has_manager_access(user):
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    return bool(
        profile
        and profile.agency_memberships.filter(
            agency__is_active=True,
            is_active=True,
            roles__name__in=MANAGER_ROLE_NAMES,
        ).exists()
    )


def _has_director_access(user):
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    if profile and profile.accessible_branches(role_names=(SUPER_ADMIN_ROLE,)).exists():
        return True
    return user.groups.filter(name=SUPER_ADMIN_ROLE).exists()


def _accessible_manager_branches(user):
    if user.is_superuser:
        return Branch.objects.all()
    return accessible_branches(user, role_names=MANAGER_ROLE_NAMES)


def _accessible_manager_properties(user):
    return Property.objects.filter(branch__in=_accessible_manager_branches(user))


def _accessible_manager_tenants(user):
    return Tenant.objects.filter(
        branch__in=_accessible_manager_branches(user),
        is_provisional=False,
    )


def _accessible_landlords(user, role_names=LANDLORD_MANAGEMENT_ROLE_NAMES):
    profile = getattr(user, "profile", None)
    if profile is None or not _has_landlord_management_access(user):
        return Landlord.objects.none()
    branches = profile.accessible_branches(role_names=role_names)
    return Landlord.objects.filter(
        Q(branch__in=branches) | Q(properties__branch__in=branches)
    ).distinct()


def _accessible_properties(user, role_names=LANDLORD_MANAGEMENT_ROLE_NAMES):
    profile = getattr(user, "profile", None)
    if profile is None:
        return Property.objects.none()
    if _is_caretaker(user) and not _has_landlord_management_access(user):
        return _caretaker_selected_properties(user)
    manager_branches = profile.accessible_branches(role_names=role_names)
    caretaker_branches = profile.accessible_branches(role_names=(CARETAKER_ROLE,))
    return Property.objects.filter(
        Q(branch__in=manager_branches)
        | Q(
            branch__in=caretaker_branches,
            caretaker_assignments__caretaker=user,
            caretaker_assignments__ended_at__isnull=True,
        )
    ).distinct()


def _caretaker_assigned_properties(user):
    if not getattr(user, "profile", None) or not _is_caretaker(user):
        return Property.objects.none()
    return Property.objects.filter(
        branch__agency__is_active=True,
        caretaker_assignments__caretaker=user,
        caretaker_assignments__ended_at__isnull=True,
    ).distinct()


def _caretaker_selected_properties(user):
    properties = _caretaker_assigned_properties(user)
    profile = getattr(user, "profile", None)
    if profile is None:
        return properties.none()
    selected_id = profile.active_caretaker_property_id
    if selected_id is not None and properties.filter(pk=selected_id).exists():
        return properties.filter(pk=selected_id)
    first_property_id = properties.order_by("name", "pk").values_list("pk", flat=True).first()
    return properties.filter(pk=first_property_id) if first_property_id else properties.none()


def _accessible_tenants(user):
    profile = getattr(user, "profile", None)
    if profile is None or not _has_landlord_management_access(user):
        return Tenant.objects.none()
    return Tenant.objects.filter(
        branch__in=profile.accessible_branches(
            role_names=LANDLORD_MANAGEMENT_ROLE_NAMES
        ),
        is_provisional=False,
    )


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
    unit_types = [
        (unit_type, list(unit_type.utilities.all()))
        for unit_type in property_obj.unit_types.all()
    ]
    has_water_utility = any(
        utility.utility_type == UtilityConfiguration.UTILITY_WATER
        for _, utilities in unit_types
        for utility in utilities
    )
    if not unit_types:
        has_water_utility = (
            bool(property_obj.default_unit_type.strip())
            and property_obj.billing_mode == Property.BILLING_MODE_METERED_WATER
            and property_obj.default_water_rate > 0
        )
    return {
        "id": property_obj.pk,
        "branch_id": property_obj.branch_id,
        "landlord_id": property_obj.landlord_id,
        "name": property_obj.name,
        "property_type": property_obj.property_type,
        "location": property_obj.location,
        "address": property_obj.address or None,
        "currency_code": property_obj.currency_code,
        "timezone": property_obj.timezone,
        "rent_due_day": property_obj.rent_due_day,
        "billing_mode": property_obj.billing_mode,
        "expected_unit_count": property_obj.expected_unit_count,
        "default_unit_type": property_obj.default_unit_type,
        "default_rent": str(property_obj.default_rent),
        "security_deposit": str(property_obj.security_deposit),
        "move_in_letting_fee": str(property_obj.move_in_letting_fee),
        "water_deposit": str(property_obj.water_deposit),
        "default_garbage_charge": str(property_obj.default_garbage_charge),
        "default_water_rate": str(property_obj.default_water_rate),
        "has_water_utility": has_water_utility,
        "gps_coordinates": property_obj.gps_coordinates,
        "amenities": property_obj.amenities,
        "house_rules": property_obj.house_rules,
        "default_handover_items": property_obj.default_handover_items,
        "photos": property_obj.photos,
        "unit_types": [
            {
                "id": unit_type.pk,
                "name": unit_type.name,
                "unit_count": unit_type.unit_count,
                "monthly_rent": str(unit_type.monthly_rent),
                "security_deposit": str(unit_type.security_deposit),
                "utilities": [
                    {
                        "id": utility.pk,
                        "utility_type": utility.utility_type,
                        "name": utility.name,
                        "billing_method": utility.billing_method,
                        "fixed_amount": (
                            str(utility.fixed_amount)
                            if utility.fixed_amount is not None
                            else None
                        ),
                        "rate_per_unit": (
                            str(utility.rate_per_unit)
                            if utility.rate_per_unit is not None
                            else None
                        ),
                        "deposit_amount": (
                            str(utility.deposit_amount)
                            if utility.deposit_amount is not None
                            else None
                        ),
                    }
                    for utility in utilities
                ],
            }
            for unit_type, utilities in unit_types
        ],
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
                "phone": assignment.caretaker.profile.phone,
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


def _validated_handover_inventory(raw_items):
    if not isinstance(raw_items, list) or len(raw_items) > 30:
        raise ValueError("The handover inventory must contain no more than 30 items.")
    items = []
    seen_names = set()
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Handover item {index} must be an object.")
        name = item.get("name")
        quantity = item.get("quantity")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError(f"Handover item {index} needs a name of at most 100 characters.")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 0 <= quantity <= 32767:
            raise ValueError(f"Quantity for {name.strip()} must be a whole number from 0 to 32,767.")
        normalized_name = name.strip()
        if normalized_name.casefold() in seen_names:
            raise ValueError(f"Handover item {normalized_name} is listed more than once.")
        seen_names.add(normalized_name.casefold())
        items.append({"name": normalized_name, "quantity": quantity})
    return items


def _landlord_property_payload(property_obj):
    payload = _property_payload(property_obj)
    payload["recurringCharges"] = [
        {
            "id": charge.pk,
            "name": charge.name,
            "amount": str(charge.amount),
            "is_active": charge.is_active,
        }
        for charge in property_obj.recurring_charges.all()
    ]
    return payload


def _landlord_property_detail_payloads(properties, landlord):
    properties = list(properties)
    property_ids = [property_obj.pk for property_obj in properties]
    if not property_ids:
        return []

    today = timezone.localdate()
    units = list(
        Unit.objects.filter(property_id__in=property_ids)
        .exclude(status__in=(Unit.STATUS_DRAFT, Unit.STATUS_ARCHIVED))
        .select_related("floor", "unit_type_definition")
        .order_by("property_id", "code", "id")
    )
    unit_ids = [unit.pk for unit in units]
    active_tenancies = list(
        Tenancy.objects.filter(
            unit_id__in=unit_ids,
            status=Tenancy.STATUS_ACTIVE,
            start_date__lte=today,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .select_related("tenant")
        .order_by("unit_id", "-start_date", "-id")
    )
    tenancy_by_unit = {}
    for tenancy in active_tenancies:
        tenancy_by_unit.setdefault(tenancy.unit_id, tenancy)

    rent_charges = (
        UnitCharge.objects.filter(
            unit_id__in=unit_ids,
            charge_type=UnitCharge.CHARGE_RENT,
            effective_from__lte=today,
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=today))
        .order_by("unit_id", "-effective_from", "-id")
    )
    rent_by_unit = {}
    for charge in rent_charges:
        rent_by_unit.setdefault(charge.unit_id, charge.amount)

    balance_by_unit = {}
    tenancy_ids = [tenancy.pk for tenancy in active_tenancies]
    if tenancy_ids:
        invoices = list(
            Invoice.objects.filter(
                tenancy_id__in=tenancy_ids,
                status=Invoice.STATUS_ISSUED,
            )
            .select_related("branch__arrears_policy", "tenancy__unit")
            .order_by("id")
        )
        invoice_ids = [invoice.pk for invoice in invoices]
        allocations_by_invoice = {
            row["invoice_id"]: row["total"] or Decimal("0.00")
            for row in PaymentAllocation.objects.filter(
                invoice_id__in=invoice_ids,
                created_at__date__lte=today,
                payment__received_at__date__lte=today,
            )
            .exclude(
                payment__instrument_status__in=(
                    Payment.INSTRUMENT_PENDING,
                    Payment.INSTRUMENT_BOUNCED,
                )
            )
            .values("invoice_id")
            .annotate(total=Sum("amount"))
        }
        waivers_by_invoice = {
            row["invoice_id"]: row["total"] or Decimal("0.00")
            for row in ArrearsWaiver.objects.filter(
                invoice_id__in=invoice_ids,
                approved_at__date__lte=today,
            )
            .values("invoice_id")
            .annotate(total=Sum("amount"))
        }
        for invoice in invoices:
            balance_by_unit[invoice.tenancy.unit_id] = (
                balance_by_unit.get(invoice.tenancy.unit_id, Decimal("0.00"))
                + invoice_arrears(
                    invoice,
                    today,
                    allocated_amount=allocations_by_invoice.get(
                        invoice.pk, Decimal("0.00")
                    ),
                    waiver_amount=waivers_by_invoice.get(
                        invoice.pk, Decimal("0.00")
                    ),
                )["total_due"]
            )

    caretakers_by_property = {}
    caretaker_assignments = (
        PropertyCaretakerAssignment.objects.filter(
            property_id__in=property_ids,
            ended_at__isnull=True,
        )
        .order_by("property_id", "-assigned_at", "-id")
        .values(
            "property_id",
            "caretaker_id",
            "caretaker__profile__name",
            "caretaker__profile__phone",
            "caretaker__email",
        )
    )
    for assignment in caretaker_assignments:
        caretakers_by_property.setdefault(assignment["property_id"], []).append(
            {
                "id": assignment["caretaker_id"],
                "name": assignment["caretaker__profile__name"] or "",
                "phone": assignment["caretaker__profile__phone"] or "",
                "email": assignment["caretaker__email"],
            }
        )

    inspection_reports_by_property = {}
    inspection_reports = list(
        PropertyInspection.objects.filter(
            property_id__in=property_ids,
            status=PropertyInspection.STATUS_COMPLETED,
            shared_at__isnull=False,
        )
        .select_related("unit", "responsible_staff__profile")
        .order_by("-completed_at", "-id")
    )
    staff_names = dict(
        UserProfile.objects.filter(
            user_id__in={
                inspection.responsible_staff_id for inspection in inspection_reports
            }
        ).values_list("user_id", "name")
    )
    for inspection in inspection_reports:
        photos = []
        stored_photos = inspection.photos if isinstance(inspection.photos, list) else []
        for index, photo in enumerate(stored_photos):
            if (
                not isinstance(photo, dict)
                or not isinstance(photo.get("storage_key"), str)
                or not photo["storage_key"].strip()
            ):
                continue
            photos.append(
                {
                    "index": index,
                    "caption": str(photo.get("caption") or ""),
                    "content_type": str(photo.get("content_type") or ""),
                }
            )
        inspection_reports_by_property.setdefault(inspection.property_id, []).append(
            {
                "id": inspection.pk,
                "title": inspection.title,
                "inspection_type": inspection.inspection_type,
                "scheduled_for": inspection.scheduled_for.isoformat(),
                "completed_at": inspection.completed_at.isoformat()
                if inspection.completed_at
                else None,
                "unit_code": inspection.unit.code if inspection.unit else None,
                "responsible_staff": staff_names.get(
                    inspection.responsible_staff_id
                )
                or inspection.responsible_staff.get_full_name(),
                "checklist": inspection.checklist,
                "findings": inspection.findings,
                "recommendations": inspection.recommendations,
                "photos": photos,
            }
        )

    agreement = (
        ManagementAgreement.objects.filter(
            landlord=landlord,
            effective_from__lte=today,
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=today))
        .order_by("-effective_from", "-id")
        .first()
    )
    agreement_payload = _agreement_payload(agreement) if agreement else None
    units_by_property = {}
    for unit in units:
        tenancy = tenancy_by_unit.get(unit.pk)
        rent = rent_by_unit.get(unit.pk)
        if rent is None and unit.unit_type_definition_id:
            rent = unit.unit_type_definition.monthly_rent
        units_by_property.setdefault(unit.property_id, []).append(
            {
                "id": unit.pk,
                "code": unit.code,
                "unit_type": unit.unit_type,
                "monthly_rent": str(rent) if rent is not None else None,
                "status": unit.status,
                "tenant_name": tenancy.tenant.name if tenancy else None,
                "balance": str(balance_by_unit.get(unit.pk, Decimal("0.00"))),
            }
        )

    payloads = []
    for property_obj in properties:
        payloads.append(
            {
                "id": property_obj.pk,
                "branch_id": property_obj.branch_id,
                "branch_name": property_obj.branch.name,
                "name": property_obj.name,
                "property_type": property_obj.property_type,
                "location": property_obj.location,
                "address": property_obj.address or None,
                "expected_unit_count": property_obj.expected_unit_count,
                "management_agreement": agreement_payload,
                "caretakers": caretakers_by_property.get(property_obj.pk, []),
                "units": units_by_property.get(property_obj.pk, []),
                "inspection_reports": inspection_reports_by_property.get(
                    property_obj.pk, []
                ),
            }
        )
    return payloads


@require_GET
def get_landlord_inspection_photo(request, inspection_id, photo_index):
    error = _landlord_portal_error(request)
    if error:
        return error
    inspection = PropertyInspection.objects.filter(
        pk=inspection_id,
        property__in=_landlord_portal_properties(request.user),
        status=PropertyInspection.STATUS_COMPLETED,
        shared_at__isnull=False,
    ).first()
    if (
        inspection is None
        or not isinstance(inspection.photos, list)
        or photo_index >= len(inspection.photos)
    ):
        return _problem(404, "Photo not found", "The inspection photo is unavailable.", "inspection-photo-not-found")
    photo = inspection.photos[photo_index]
    storage_key = photo.get("storage_key") if isinstance(photo, dict) else None
    if not isinstance(storage_key, str):
        return _problem(404, "Photo not found", "The inspection photo is unavailable.", "inspection-photo-not-found")

    key_parts = storage_key.split("/")
    if (
        len(key_parts) < 3
        or key_parts[:2] != ["inspections", str(inspection.property_id)]
        or any(part in {"", ".", ".."} for part in key_parts[2:])
        or "\\" in storage_key
    ):
        return _problem(404, "Photo not found", "The inspection photo is unavailable.", "inspection-photo-not-found")
    content_type = photo.get("content_type") or mimetypes.guess_type(storage_key)[0]
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        return _problem(404, "Photo not found", "The inspection photo is unavailable.", "inspection-photo-not-found")
    try:
        content = read_private_object(storage_key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return _problem(404, "Photo not found", "The inspection photo is unavailable.", "inspection-photo-not-found")
        logger.exception("Could not retrieve landlord inspection photo.")
        return _problem(
            503,
            "Photo storage unavailable",
            "The inspection photo could not be loaded. Please try again later.",
            "inspection-photo-storage-unavailable",
        )
    except (BotoCoreError, ImproperlyConfigured, OSError):
        logger.exception("Could not retrieve landlord inspection photo.")
        return _problem(
            503,
            "Photo storage unavailable",
            "The inspection photo could not be loaded. Please try again later.",
            "inspection-photo-storage-unavailable",
        )

    record_audit_event(
        request,
        "landlord_inspection.photo_viewed",
        "PropertyInspection",
        new_values={"inspection_id": inspection.pk, "photo_index": photo_index},
    )
    response = HttpResponse(content, content_type=content_type)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = "inline"
    return response


def _unit_payload(unit):
    return {
        "id": unit.pk,
        "property_id": unit.property_id,
        "floor_id": unit.floor_id,
        "block_name": unit.floor.block.name if unit.floor_id else None,
        "floor_name": unit.floor.name if unit.floor_id else None,
        "code": unit.code,
        "unit_type": unit.unit_type,
        "unit_type_definition_id": unit.unit_type_definition_id,
        "bedrooms": unit.bedrooms,
        "characteristics": unit.characteristics,
        "meters": unit.meters,
        "listing_enabled": unit.listing_enabled,
        "status": unit.status,
    }


def _move_in_settlement_payload(settlement):
    return {
        "id": settlement.pk,
        "unit_id": settlement.unit_id,
        "tenant_id": settlement.tenant_id,
        "tenant_name": settlement.tenant_name,
        "tenant_email": settlement.tenant_email,
        "tenant_phone": settlement.tenant_phone,
        "occupation": settlement.occupation or None,
        "employer": settlement.employer or None,
        "next_of_kin_name": settlement.next_of_kin_name or None,
        "next_of_kin_phone": settlement.next_of_kin_phone or None,
        "next_of_kin_relationship": settlement.next_of_kin_relationship or None,
        "whatsapp_opt_in": settlement.whatsapp_opt_in,
        "deposit_amount": float(settlement.deposit_amount),
        "rent_amount": float(settlement.rent_amount),
        "garbage_amount": float(settlement.garbage_amount),
        "move_in_letting_fee": float(settlement.move_in_letting_fee),
        "water_deposit_amount": float(settlement.water_deposit_amount),
        "utility_deposit_amount": float(settlement.utility_deposit_amount),
        "utility_deposits": settlement.utility_deposits,
        "utility_charges": settlement.utility_charges,
        "total_required": float(settlement.total_required),
        "total_paid": float(settlement.total_paid),
        "deposit_paid_amount": float(settlement.deposit_paid_amount),
        "outstanding_balance": float(settlement.outstanding_balance),
        "expected_move_in_date": settlement.expected_move_in_date.isoformat(),
        "status": settlement.status,
        "activation_email_sent_at": (
            settlement.activation_email_sent_at.isoformat()
            if settlement.activation_email_sent_at
            else None
        ),
        "activation_email_error": settlement.activation_email_error or None,
        "activation_sms_queued_at": (
            settlement.activation_sms_queued_at.isoformat()
            if settlement.activation_sms_queued_at
            else None
        ),
        "activation_sms_error": settlement.activation_sms_error or None,
        "cancellation_reason": settlement.cancellation_reason or None,
        "booked_at": settlement.booked_at.isoformat() if settlement.booked_at else None,
        "activated_at": settlement.activated_at.isoformat() if settlement.activated_at else None,
        "created_at": settlement.created_at.isoformat(),
        "updated_at": settlement.updated_at.isoformat(),
        "units": {"unit_code": settlement.unit.code},
        "payments": [
            {
                "id": payment.pk,
                "amount": float(payment.amount),
                "method": payment.method,
                "payment_channel": payment.payment_channel or None,
                "transaction_reference": payment.transaction_reference or None,
                "payment_date": payment.payment_date.isoformat() if payment.payment_date else None,
                "mpesa_reference": payment.mpesa_reference or None,
                "status": payment.status,
                "created_at": payment.created_at.isoformat(),
            }
            for payment in settlement.payments.all()
        ],
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
        "is_provisional": tenant.is_provisional,
        "kra_pin": tenant.kra_pin or None,
        "occupation": tenant.occupation or None,
        "employer": tenant.employer or None,
        "next_of_kin_name": tenant.next_of_kin_name or None,
        "next_of_kin_phone": tenant.next_of_kin_phone or None,
        "next_of_kin_relationship": tenant.next_of_kin_relationship or None,
        "whatsapp_opted_in": tenant.whatsapp_opted_in_at is not None,
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
                "metadata": document.metadata,
                "uploaded_by": document.uploaded_by_id,
                "created_at": document.created_at.isoformat(),
                "download_path": (
                    f"/api/v1/tenants/{tenant.pk}/documents/{document.pk}"
                ),
            }
            for document in tenant.documents.all()
        ],
    }


def _tenancy_payload(tenancy):
    return {
        "id": tenancy.pk,
        "unit_id": tenancy.unit_id,
        "unit_code": tenancy.unit.code,
        "property_name": tenancy.unit.property.name,
        "tenant_id": tenancy.tenant_id,
        "tenant_name": tenancy.tenant.name,
        "tenancy_type": tenancy.tenancy_type,
        "start_date": tenancy.start_date.isoformat(),
        "end_date": tenancy.end_date.isoformat() if tenancy.end_date else None,
        "base_water_meter_reading": (
            str(tenancy.base_water_meter_reading)
            if tenancy.base_water_meter_reading is not None
            else None
        ),
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
    waivers = list(
        ArrearsWaiver.objects.filter(invoice=invoice)
        .select_related("approved_by__profile")
        .order_by("approved_at", "id")
    )
    waived_amount = sum((waiver.amount for waiver in waivers), Decimal("0.00"))
    return {
        "id": invoice.pk,
        "branch_id": invoice.branch_id,
        "tenancy_id": invoice.tenancy_id,
        "number": invoice.number,
        "invoice_type": invoice.invoice_type,
        "period_start": invoice.period_start.isoformat(),
        "period_end": invoice.period_end.isoformat(),
        "due_date": invoice.due_date.isoformat(),
        "status": invoice.status,
        "total": str(invoice.total),
        "waived_amount": str(waived_amount),
        "net_amount_due": str(max(invoice.total - waived_amount, Decimal("0.00"))),
        "approved_waivers": [
            {
                "amount": str(waiver.amount),
                "reason": waiver.reason,
                "approved_at": waiver.approved_at.isoformat(),
                "approved_by": (
                    waiver.approved_by.profile.name
                    if hasattr(waiver.approved_by, "profile")
                    else waiver.approved_by.get_full_name()
                ),
            }
            for waiver in waivers
        ],
        "issued_at": invoice.issued_at.isoformat() if invoice.issued_at else None,
        "lines": [_invoice_line_payload(line) for line in invoice.lines.all()],
    }


def _property_officer_billing_error(request):
    error = _property_access_error(request)
    if error:
        return error
    if not _has_landlord_management_access(request.user):
        return _problem(
            403,
            "Permission denied",
            "Property Officer access is required to manage billing drafts.",
            "billing-access-denied",
        )
    return None


def _billing_period_from_request(request, *, default_next_month=False):
    raw_month = request.GET.get("month") if request.method == "GET" else None
    if request.method != "GET":
        body = _json_body(request) or {}
        raw_month = body.get("month")
    if not raw_month and default_next_month:
        today = timezone.localdate()
        next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        return next_month
    if not isinstance(raw_month, str):
        return None
    try:
        period_start = datetime.strptime(raw_month, "%Y-%m").date()
    except ValueError:
        return None
    return period_start if period_start.strftime("%Y-%m") == raw_month else None


def _property_officer_waiver_payload(item):
    return {
        "id": item.pk,
        "invoice_id": item.invoice_id,
        "amount": str(item.amount),
        "reason": item.reason,
        "status": item.status,
        "decision_reason": item.decision_reason,
    }


def _property_officer_billing_invoice_payload(invoice):
    payload = _invoice_payload(invoice)
    approved_waivers = (
        ArrearsWaiver.objects.filter(invoice=invoice)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    payload.update(
        {
            "property_id": invoice.tenancy.unit.property_id,
            "property_name": invoice.tenancy.unit.property.name,
            "unit_id": invoice.tenancy.unit_id,
            "unit_code": invoice.tenancy.unit.code,
            "tenant_name": invoice.tenancy.tenant.name,
            "waived_amount": str(approved_waivers),
            "net_amount_due": str(max(invoice.total - approved_waivers, Decimal("0.00"))),
            "waiver_requests": [
                _property_officer_waiver_payload(item)
                for item in invoice.waiver_requests.all()
            ],
        }
    )
    return payload


def _billing_tenancy_readiness(tenancy, period_start):
    required, note = water_reading_status(tenancy.unit, period_start)
    return note is None, required, note


@require_GET
def property_officer_billing(request):
    error = _property_officer_billing_error(request)
    if error:
        return error
    period_start = _billing_period_from_request(request, default_next_month=True)
    if period_start is None:
        return _problem(400, "Invalid billing month", "month must use YYYY-MM format.", "invalid-billing-month")
    try:
        property_id = int(request.GET.get("property_id", ""))
    except (TypeError, ValueError):
        return _problem(400, "Invalid property", "Select a property to view its billing.", "invalid-property")
    properties = (
        Property.objects.all()
        if request.user.is_superuser
        else _accessible_properties(request.user)
    )
    property_obj = properties.filter(pk=property_id).first()
    if property_obj is None:
        return _problem(404, "Property not found", "The property is outside your access scope.", "property-not-found")
    properties = properties.filter(pk=property_obj.pk)
    invoices = (
        Invoice.objects.filter(
            tenancy__unit__property__in=properties,
            period_start=period_start,
            invoice_type=Invoice.TYPE_STANDARD,
            status__in=(Invoice.STATUS_DRAFT, Invoice.STATUS_ISSUED),
        )
        .select_related("tenancy__tenant", "tenancy__unit__property")
        .prefetch_related("lines", "waiver_requests")
        .order_by("tenancy__unit__property__name", "tenancy__unit__code", "id")
    )
    invoice_by_tenancy = {invoice.tenancy_id: invoice for invoice in invoices}
    tenancies = (
        Tenancy.objects.filter(
            unit__property__in=properties,
            status=Tenancy.STATUS_ACTIVE,
            start_date__lte=period_start,
        )
        .filter(models.Q(end_date__isnull=True) | models.Q(end_date__gte=period_start))
        .select_related("tenant", "unit__property")
        .order_by("unit__property__name", "unit__code", "id")
    )
    waiver_requests = (
        InvoiceWaiverRequest.objects.filter(
            unit__property=property_obj,
            period_start=period_start,
        )
        .select_related("invoice")
        .order_by("created_at", "id")
    )
    waivers_by_unit = {}
    for waiver in waiver_requests:
        waivers_by_unit.setdefault(waiver.unit_id, []).append(
            _property_officer_waiver_payload(waiver)
        )
    rows = []
    for tenancy in tenancies:
        invoice = invoice_by_tenancy.get(tenancy.pk)
        ready, water_reading_required, readiness_note = _billing_tenancy_readiness(
            tenancy,
            period_start,
        )
        rows.append(
            {
                "tenancy_id": tenancy.pk,
                "property_id": tenancy.unit.property_id,
                "property_name": tenancy.unit.property.name,
                "branch_id": tenancy.unit.property.branch_id,
                "unit_id": tenancy.unit_id,
                "unit_code": tenancy.unit.code,
                "tenant_name": tenancy.tenant.name,
                "water_reading_required": water_reading_required,
                "ready": ready,
                "readiness_note": readiness_note,
                "waiver_requests": waivers_by_unit.get(tenancy.unit_id, []),
                "invoice": (
                    _property_officer_billing_invoice_payload(invoice)
                    if invoice
                    else None
                ),
            }
        )
    record_audit_event(
        request,
        "property_officer_billing.viewed",
        "PropertyOfficerBilling",
        new_values={"period_start": period_start.isoformat(), "tenancy_count": len(rows)},
    )
    return JsonResponse(
        {
            "period_start": period_start.isoformat(),
            "period_end": month_end(period_start).isoformat(),
            "rows": rows,
            "summary": {
                "tenancies": len(rows),
                "drafts": sum(bool(row["invoice"] and row["invoice"]["status"] == Invoice.STATUS_DRAFT) for row in rows),
                "issued": sum(bool(row["invoice"] and row["invoice"]["status"] == Invoice.STATUS_ISSUED) for row in rows),
                "waiting_for_readings": sum(not row["ready"] and not row["invoice"] for row in rows),
                "ready_without_draft": sum(row["ready"] and not row["invoice"] for row in rows),
            },
        }
    )


@require_POST
def property_officer_billing_draft(request):
    error = _property_officer_billing_error(request)
    if error:
        return error
    period_start = _billing_period_from_request(request)
    if period_start is None or period_start.day != 1:
        return _problem(400, "Invalid billing month", "month must use YYYY-MM format.", "invalid-billing-month")
    current_month = timezone.localdate().replace(day=1)
    next_month = (current_month.replace(day=28) + timedelta(days=4)).replace(day=1)
    if period_start < current_month or period_start > next_month:
        return _problem(400, "Invalid billing month", "Only the current or next billing month can be drafted.", "invalid-billing-month")

    properties = _accessible_properties(request.user)
    body = _json_body(request) or {}
    try:
        property_id = int(body.get("property_id", ""))
    except (TypeError, ValueError):
        return _problem(400, "Invalid property", "Select a property before preparing drafts.", "invalid-property")
    properties = properties.filter(pk=property_id)
    if not properties.exists():
        return _problem(404, "Property not found", "The property is outside your access scope.", "property-not-found")

    tenancies = (
        Tenancy.objects.filter(
            unit__property__in=properties,
            status=Tenancy.STATUS_ACTIVE,
            start_date__lte=period_start,
        )
        .filter(models.Q(end_date__isnull=True) | models.Q(end_date__gte=period_start))
        .select_related("unit__property__branch")
        .order_by("unit__property__name", "unit__code", "id")
    )
    drafted = []
    held = []
    for tenancy in tenancies:
        try:
            invoice, _ = generate_monthly_invoice(
                tenancy,
                period_start,
                status=Invoice.STATUS_DRAFT,
            )
        except ValueError as generation_error:
            held.append(
                {
                    "tenancy_id": tenancy.pk,
                    "unit_code": tenancy.unit.code,
                    "reason": str(generation_error),
                }
            )
            continue
        if invoice.status == Invoice.STATUS_DRAFT:
            drafted.append(invoice)
    record_audit_event(
        request,
        "property_officer_billing.drafts_generated",
        "PropertyOfficerBilling",
        new_values={
            "period_start": period_start.isoformat(),
            "drafted_invoice_ids": [invoice.pk for invoice in drafted],
            "held": held,
        },
    )
    return JsonResponse(
        {
            "drafts": [_property_officer_billing_invoice_payload(invoice) for invoice in drafted],
            "held": held,
        }
    )


@require_POST
def property_officer_invoice_waiver(request, invoice_id=None):
    error = _property_officer_billing_error(request)
    if error:
        return error
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid waiver", "Expected a unit, billing month, amount, and reason.", "invalid-waiver")
    try:
        amount = Decimal(str(body.get("amount")))
    except (InvalidOperation, TypeError, ValueError):
        return _problem(400, "Invalid waiver", "Waiver amount must be a positive number.", "invalid-waiver")
    reason = body.get("reason")
    if not amount.is_finite() or amount <= 0 or not isinstance(reason, str) or not reason.strip():
        return _problem(400, "Invalid waiver", "Waiver amount and reason are required.", "invalid-waiver")
    accessible_properties = (
        Property.objects.all()
        if request.user.is_superuser
        else _accessible_properties(request.user)
    )
    if invoice_id is not None:
        invoice = (
            Invoice.objects.filter(
                pk=invoice_id,
                tenancy__unit__property__in=accessible_properties,
                status__in=(Invoice.STATUS_DRAFT, Invoice.STATUS_ISSUED),
            )
            .select_related("tenancy__unit__property")
            .first()
        )
        if invoice is None:
            return _problem(404, "Invoice not found", "The invoice is outside your access scope.", "invoice-not-found")
        property_id = invoice.tenancy.unit.property_id
        unit_id = invoice.tenancy.unit_id
        period_start = invoice.period_start
    else:
        try:
            property_id = int(body.get("property_id", ""))
            unit_id = int(body.get("unit_id", ""))
            month_value = body.get("month")
            period_start = datetime.strptime(str(month_value), "%Y-%m").date()
        except (TypeError, ValueError):
            return _problem(
                400,
                "Invalid waiver",
                "Select a property, unit, and valid billing month.",
                "invalid-waiver",
            )
        if period_start.strftime("%Y-%m") != month_value:
            return _problem(400, "Invalid billing month", "month must use YYYY-MM format.", "invalid-billing-month")
    current_month = timezone.localdate().replace(day=1)
    next_month = (current_month.replace(day=28) + timedelta(days=4)).replace(day=1)
    if period_start < current_month or period_start > next_month:
        return _problem(
            400,
            "Invalid billing month",
            "Waiver requests can only be opened for the current or next billing month.",
            "invalid-billing-month",
        )
    with transaction.atomic():
        unit = (
            Unit.objects.select_for_update()
            .filter(pk=unit_id, property_id=property_id, property__in=accessible_properties)
            .select_related("property__branch")
            .first()
        )
        if unit is None:
            return _problem(404, "Unit not found", "The unit is outside the selected property or your access scope.", "unit-not-found")
        tenancy = (
            Tenancy.objects.filter(
                unit=unit,
                status=Tenancy.STATUS_ACTIVE,
                start_date__lte=period_start,
            )
            .filter(models.Q(end_date__isnull=True) | models.Q(end_date__gte=period_start))
            .select_related("unit__property__branch")
            .order_by("-start_date", "-id")
            .first()
        )
        if tenancy is None:
            return _problem(400, "No active tenancy", "A waiver can only be opened for a unit with an active tenancy in the selected billing month.", "tenancy-not-found")
        invoice = Invoice.objects.filter(tenancy=tenancy, period_start=period_start).first()
        if invoice and invoice.status not in (Invoice.STATUS_DRAFT, Invoice.STATUS_ISSUED):
            return _problem(400, "Invalid invoice", "The billing period invoice cannot receive a waiver request.", "invalid-invoice")
        existing_request = InvoiceWaiverRequest.objects.filter(
            unit=unit,
            period_start=period_start,
        ).first()
        if existing_request:
            return _problem(
                409,
                "Waiver already requested",
                f"This unit already has a {existing_request.status} waiver request for the selected billing month.",
                "waiver-already-requested",
            )
        approved = (
            ArrearsWaiver.objects.filter(invoice=invoice)
            .aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        ) if invoice else Decimal("0.00")
        if invoice and amount > invoice.total - approved:
            return _problem(400, "Invalid waiver", "The waiver cannot exceed the remaining invoice amount.", "invalid-waiver")
        waiver = InvoiceWaiverRequest(
            invoice=invoice,
            unit=unit,
            period_start=period_start,
            amount=amount,
            reason=reason.strip(),
            requested_by=request.user,
        )
        try:
            waiver.full_clean()
            waiver.save()
            from users.tasks import queue_waiver_director_notifications

            try:
                queue_waiver_director_notifications(waiver)
            except (ImproperlyConfigured, ValueError) as notification_error:
                transaction.set_rollback(True)
                return _problem(
                    503,
                    "Director notification unavailable",
                    str(notification_error),
                    "director-notification-unavailable",
                )
        except ValidationError as validation_error:
            return _problem(400, "Invalid waiver", str(validation_error.message_dict), "invalid-waiver")
    record_audit_event(
        request,
        "invoice_waiver.requested",
        "InvoiceWaiverRequest",
        new_values={
            "id": waiver.pk,
            "invoice_id": invoice.pk if invoice else None,
            "unit_id": unit.pk,
            "period_start": period_start.isoformat(),
            "amount": str(amount),
            "reason": waiver.reason,
        },
    )
    return JsonResponse(
        {
            "waiver": {
                "id": waiver.pk,
                "invoice_id": invoice.pk if invoice else None,
                "unit_id": unit.pk,
                "period_start": period_start.isoformat(),
                "amount": str(amount),
                "reason": waiver.reason,
                "status": waiver.status,
            }
        },
        status=201,
    )


@require_POST
def decide_property_officer_invoice_waiver(request, waiver_id):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not _has_director_access(request.user):
        return _problem(403, "Permission denied", "Only the Director can decide invoice waivers.", "waiver-access-denied")
    body = _json_body(request)
    decision = body.get("decision") if body else None
    reason = body.get("reason", "") if body else ""
    agency_id = body.get("agency_id") if body else None
    branch_id = body.get("branch_id") if body else None
    if decision not in (InvoiceWaiverRequest.STATUS_APPROVED, InvoiceWaiverRequest.STATUS_REJECTED):
        return _problem(400, "Invalid decision", "decision must be approved or rejected.", "invalid-decision")
    if not isinstance(reason, str):
        return _problem(400, "Invalid decision", "reason must be text.", "invalid-decision")

    accessible_director_branches = (
        Branch.objects.all()
        if request.user.is_superuser
        else accessible_branches(request.user, role_names=(SUPER_ADMIN_ROLE,))
    )
    waiver_branches = accessible_director_branches
    if agency_id is not None:
        try:
            agency_id = int(agency_id)
        except (TypeError, ValueError):
            return _problem(400, "Invalid agency", "agency_id must be an integer.", "invalid-agency")
        waiver_branches = waiver_branches.filter(agency_id=agency_id)
        if not waiver_branches.exists():
            return _problem(404, "Waiver not found", "The pending waiver is unavailable.", "waiver-not-found")
        if branch_id is not None:
            try:
                branch_id = int(branch_id)
            except (TypeError, ValueError):
                return _problem(400, "Invalid branch", "branch_id must be an integer.", "invalid-branch")
            waiver_branches = waiver_branches.filter(pk=branch_id)
            if not waiver_branches.exists():
                return _problem(404, "Waiver not found", "The pending waiver is unavailable.", "waiver-not-found")
    with transaction.atomic():
        waiver = (
            InvoiceWaiverRequest.objects.select_for_update(of=("self",))
            .filter(
                pk=waiver_id,
                unit__property__branch__in=waiver_branches,
                status=InvoiceWaiverRequest.STATUS_PENDING,
            )
            .filter(
                models.Q(invoice__isnull=True)
                | models.Q(invoice__status__in=(Invoice.STATUS_DRAFT, Invoice.STATUS_ISSUED))
            )
            .select_related("invoice__tenancy", "unit")
            .first()
        )
        if waiver is None:
            return _problem(404, "Waiver not found", "The pending waiver is unavailable.", "waiver-not-found")
        if decision == InvoiceWaiverRequest.STATUS_REJECTED and not reason.strip():
            return _problem(400, "Invalid decision", "A rejection reason is required.", "invalid-decision")
        if decision == InvoiceWaiverRequest.STATUS_APPROVED and waiver.invoice_id:
            approved = (
                ArrearsWaiver.objects.filter(invoice=waiver.invoice)
                .aggregate(total=Sum("amount"))["total"]
                or Decimal("0.00")
            )
            if waiver.amount > waiver.invoice.total - approved:
                return _problem(400, "Invalid waiver", "The waiver exceeds the remaining invoice amount.", "invalid-waiver")
            ArrearsWaiver.objects.create(
                tenancy=waiver.invoice.tenancy,
                invoice=waiver.invoice,
                invoice_waiver_request=waiver,
                amount=waiver.amount,
                reason=waiver.reason,
                approved_by=request.user,
            )
        waiver.status = decision
        waiver.decided_by = request.user
        waiver.decided_at = timezone.now()
        waiver.decision_reason = reason.strip()
        waiver.save(
            update_fields=[
                "status",
                "decided_by",
                "decided_at",
                "decision_reason",
            ]
        )
    record_audit_event(
        request,
        f"invoice_waiver.{decision}",
        "InvoiceWaiverRequest",
        new_values={
            "id": waiver.pk,
            "invoice_id": waiver.invoice_id,
            "unit_id": waiver.unit_id,
            "period_start": waiver.period_start.isoformat(),
            "amount": str(waiver.amount),
            "reason": waiver.reason,
            "decision_reason": waiver.decision_reason,
        },
    )
    return JsonResponse({"waiver": {"id": waiver.pk, "status": waiver.status}})


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


def _ensure_payment_receipt(payment, *, notify_whatsapp=True):
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
    if (
        created
        and notify_whatsapp
        and payment.tenant.whatsapp_opted_in_at
    ):
        queue_template_whatsapp_if_enabled(
            payment.tenant.phone,
            "payment_receipt",
            {
                "amount": f"{payment.amount:,.2f}",
                "reference": payment.reference or receipt.number,
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
    activation_invoice_ids = []
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
                    activation_invoice_ids.append(invoice.pk)
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
    activation_results = [
        result
        for invoice_id in dict.fromkeys(activation_invoice_ids)
        if (result := _send_move_in_invoice_activation(request, invoice_id)) is not None
    ]
    return JsonResponse(
        {
            "payment": _payment_payload(payment),
            "tenant_activation": activation_results,
        },
        status=201,
    )


@require_POST
def create_offline_payment(request):
    error = _waiver_manager_error(request)
    if error:
        return error
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "A JSON object is required.", "invalid-request")
    try:
        tenant = _accessible_manager_tenants(request.user).get(pk=int(body.get("tenant_id")))
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
    activation_invoice_ids = []
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
                activation_invoice_ids.append(invoice.pk)
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
    activation_results = [
        result
        for invoice_id in dict.fromkeys(activation_invoice_ids)
        if (result := _send_move_in_invoice_activation(request, invoice_id)) is not None
    ]
    return JsonResponse(
        {
            "payment": _payment_payload(payment),
            "tenant_activation": activation_results,
        },
        status=201,
    )


@require_POST
def update_cheque_status(request, payment_id):
    error = _waiver_manager_error(request)
    if error:
        return error
    payment = Payment.objects.filter(
        pk=payment_id,
        source=Payment.SOURCE_CHEQUE,
        tenant__branch__in=_accessible_manager_branches(request.user),
    ).first()
    if payment is None:
        return _problem(404, "Cheque not found", "The cheque payment is unavailable.", "cheque-not-found")
    body = _json_body(request)
    status = body.get("status") if body else None
    if status not in {Payment.INSTRUMENT_CLEARED, Payment.INSTRUMENT_BOUNCED}:
        return _problem(400, "Invalid cheque status", "Status must be cleared or bounced.", "invalid-cheque-status")
    invoice_ids = list(
        PaymentAllocation.objects.filter(payment=payment).values_list("invoice_id", flat=True)
    )
    payment.instrument_status = status
    payment.clearing_date = timezone.localdate()
    payment.save(update_fields=["instrument_status", "clearing_date"])
    record_audit_event(
        request,
        "cheque.status_updated",
        "Payment",
        new_values={"id": payment.pk, "status": status, "clearing_date": payment.clearing_date.isoformat()},
    )
    activation_results = [
        result
        for invoice_id in dict.fromkeys(invoice_ids)
        if (result := _send_move_in_invoice_activation(request, invoice_id)) is not None
    ]
    return JsonResponse(
        {
            "payment": _payment_payload(payment),
            "tenant_activation": activation_results,
        }
    )


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
        tenant__branch__in=_accessible_manager_branches(request.user),
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
            if entry_type in {DepositEntry.TYPE_REFUND, DepositEntry.TYPE_DEDUCTION}:
                if amount > account.balance:
                    return _problem(
                        400,
                        "Invalid deposit reduction",
                        "A refund or deduction cannot exceed the deposit balance.",
                        "invalid-deposit-reduction",
                    )
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


def _move_out_staff_error(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not _has_landlord_management_access(request.user):
        return _problem(
            403,
            "Permission denied",
            "Only authorized property-management staff can manage move-outs.",
            "move-out-access-denied",
        )
    return None


def _move_out_available_caretakers(property_obj):
    assignments = (
        PropertyCaretakerAssignment.objects.filter(
            property=property_obj,
            ended_at__isnull=True,
            caretaker__is_active=True,
        )
        .select_related("caretaker")
        .order_by("caretaker__first_name", "caretaker__last_name", "caretaker__username")
    )
    caretakers = []
    for assignment in assignments:
        caretaker = assignment.caretaker
        if not _is_caretaker(caretaker) or not UserProfile.objects.filter(
            user=caretaker,
            branches=property_obj.branch,
        ).exists():
            continue
        caretakers.append(
            {
                "id": caretaker.pk,
                "name": caretaker.get_full_name().strip() or caretaker.get_username(),
            }
        )
    return caretakers


def _move_out_notice_payload(notice):
    tenancy = notice.tenancy
    return {
        "id": notice.pk,
        "tenancy_id": tenancy.pk,
        "tenant_name": tenancy.tenant.name,
        "property_id": tenancy.unit.property_id,
        "property_name": tenancy.unit.property.name,
        "unit_id": tenancy.unit_id,
        "unit_code": tenancy.unit.code,
        "requested_by": notice.requested_by_id,
        "proposed_move_out_date": notice.proposed_move_out_date.isoformat(),
        "previous_tenancy_end_date": (
            notice.previous_tenancy_end_date.isoformat()
            if notice.previous_tenancy_end_date
            else None
        ),
        "notice_period_days": tenancy.notice_period_days,
        "reason": notice.reason,
        "status": notice.status,
        "reviewed_by": notice.reviewed_by_id,
        "reviewed_at": notice.reviewed_at.isoformat() if notice.reviewed_at else None,
        "review_note": notice.review_note,
        "created_at": notice.created_at.isoformat(),
    }


def _move_out_settlement_payload(settlement):
    tenancy = settlement.tenancy
    return {
        "id": settlement.pk,
        "tenancy_id": tenancy.pk,
        "notice_id": settlement.notice_id,
        "exit_inspection_id": settlement.exit_inspection_id,
        "closing_utility_invoice_id": (
            settlement.exit_inspection.closing_utility_invoice_id
        ),
        "property_id": tenancy.unit.property_id,
        "property_name": tenancy.unit.property.name,
        "unit_id": tenancy.unit_id,
        "unit_code": tenancy.unit.code,
        "tenant_id": tenancy.tenant_id,
        "tenant_name": tenancy.tenant.name,
        "actual_move_out_date": settlement.actual_move_out_date.isoformat(),
        "final_readings": settlement.final_readings,
        "damage_items": settlement.damage_items,
        "maintenance_required": settlement.maintenance_required,
        "deposit_balance": str(settlement.deposit_balance),
        "arrears_amount": str(settlement.arrears_amount),
        "utility_amount": str(settlement.utility_amount),
        "damages_amount": str(settlement.damages_amount),
        "deduction_from_deposit": str(settlement.deduction_from_deposit),
        "refund_amount": str(settlement.refund_amount),
        "shortfall_amount": str(settlement.shortfall_amount),
        "status": settlement.status,
        "prepared_by": settlement.prepared_by_id,
        "reviewed_by": settlement.reviewed_by_id,
        "reviewed_at": settlement.reviewed_at.isoformat() if settlement.reviewed_at else None,
        "approval_hash": settlement.approval_hash,
        "rejection_reason": settlement.rejection_reason,
        "payout_reference": settlement.payout_reference,
        "payout_date": settlement.payout_date.isoformat() if settlement.payout_date else None,
        "payout_recorded_by": settlement.payout_recorded_by_id,
        "created_at": settlement.created_at.isoformat(),
    }


def _move_out_tenant(request):
    if not request.user.is_authenticated:
        return None, _problem(
            401,
            "Authentication required",
            "Sign in first.",
            "authentication-required",
        )
    if not _has_role(request.user, TENANT_PORTAL_ROLE):
        return None, _problem(
            403,
            "Permission denied",
            "Tenant portal access is required.",
            "tenant-portal-access-denied",
        )
    tenant = Tenant.objects.filter(portal_user=request.user).first()
    if tenant is None:
        return None, _problem(
            404,
            "Tenant record not found",
            "No tenant record is linked to this account.",
            "tenant-record-not-found",
        )
    return tenant, None


def _move_out_notice_date(body, tenancy):
    try:
        proposed_date = date.fromisoformat(str(body.get("proposed_move_out_date", "")))
    except ValueError as exc:
        raise ValueError("proposed_move_out_date must be a valid ISO date.") from exc
    earliest = timezone.localdate() + timedelta(days=tenancy.notice_period_days)
    if proposed_date < earliest:
        raise ValueError(
            f"The move-out date must be at least {tenancy.notice_period_days} days after notice."
        )
    if proposed_date < tenancy.start_date:
        raise ValueError("The move-out date cannot precede the tenancy start.")
    if tenancy.end_date and proposed_date > tenancy.end_date:
        raise ValueError("The move-out date cannot extend beyond the tenancy's existing end date.")
    return proposed_date


def _queue_property_staff_sms(property_obj, role_names, template_name, context, actor, request):
    branch_scope = Q(profile__branches=property_obj.branch) | Q(
        profile__agency_memberships__branches=property_obj.branch
    )
    staff = (
        get_user_model()
        .objects.filter(is_active=True)
        .filter(branch_scope)
        .select_related("profile")
        .distinct()
    )
    queued = 0
    for user in staff:
        if not any(_has_role(user, role_name) for role_name in role_names):
            continue
        if property_obj not in _accessible_properties(user):
            continue
        phone = getattr(getattr(user, "profile", None), "phone", "")
        if not phone:
            logger.warning(
                "Property notification SMS not queued: staff user %s has no phone number.",
                user.pk,
            )
            continue
        queue_template_sms(
            phone,
            template_name,
            context,
            actor=actor,
            request=request,
        )
        queued += 1
    if not queued:
        logger.warning(
            "No scoped recipients were available for SMS template %s on property %s.",
            template_name,
            property_obj.pk,
        )
    return queued


def _queue_move_out_staff_sms(property_obj, role_names, template_name, context, actor, request):
    return _queue_property_staff_sms(
        property_obj,
        role_names,
        template_name,
        context,
        actor,
        request,
    )


def _queue_move_out_tenant_sms(tenancy, template_name, context, actor, request):
    if not tenancy.tenant.phone:
        raise ValueError(f"Tenant {tenancy.tenant_id} has no phone number for move-out SMS.")
    queue_template_sms(
        tenancy.tenant.phone,
        template_name,
        context,
        actor=actor,
        request=request,
    )
    return True


def _queue_move_out_user_sms(user, template_name, context, actor, request):
    phone = getattr(getattr(user, "profile", None), "phone", "")
    if not phone:
        raise ValueError(f"Staff user {user.pk} has no phone number for move-out SMS.")
    queue_template_sms(
        phone,
        template_name,
        context,
        actor=actor,
        request=request,
    )
    return True


def _move_out_notice_context(notice):
    tenancy = notice.tenancy
    return {
        "tenant_name": tenancy.tenant.name,
        "property_name": tenancy.unit.property.name,
        "unit_code": tenancy.unit.code,
        "move_out_date": notice.proposed_move_out_date.isoformat(),
    }


def _move_out_settlement_context(settlement):
    tenancy = settlement.tenancy
    return {
        "tenant_name": tenancy.tenant.name,
        "property_name": tenancy.unit.property.name,
        "unit_code": tenancy.unit.code,
        "refund": settlement.refund_amount,
        "deductions": settlement.deduction_from_deposit,
        "shortfall": settlement.shortfall_amount,
    }


def _move_out_arrears(tenancy, as_of):
    invoices = Invoice.objects.filter(
        tenancy=tenancy,
        invoice_type=Invoice.TYPE_STANDARD,
        status=Invoice.STATUS_ISSUED,
    ).select_related("branch__arrears_policy").prefetch_related("arrears_waivers")
    return sum(
        (_move_out_invoice_due(invoice, as_of) for invoice in invoices),
        Decimal("0.00"),
    )


def _move_out_invoice_due(invoice, as_of, *, include_penalty=True):
    allocated = (
        PaymentAllocation.objects.filter(invoice=invoice)
        .exclude(
            payment__instrument_status__in=(
                Payment.INSTRUMENT_PENDING,
                Payment.INSTRUMENT_BOUNCED,
            )
        )
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    waived = (
        invoice.arrears_waivers.filter(approved_at__date__lte=timezone.localdate())
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    balance = invoice_arrears(
        invoice,
        as_of,
        allocated_amount=allocated,
        waiver_amount=waived,
    )
    return balance["total_due"] if include_penalty else balance["outstanding"]


def _move_out_utility_configs(unit, as_of):
    return list(
        UtilityConfiguration.objects.filter(
            unit=unit,
            billing_method=UtilityConfiguration.METHOD_METERED,
            effective_from__lte=as_of,
        )
        .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=as_of))
        .order_by("utility_type", "-effective_from", "-id")
    )


def _move_out_meter_baseline(unit, utility, as_of):
    meter = utility.metadata.get("meter") or unit.meters.get(utility.utility_type)
    if not isinstance(meter, str) or not meter.strip():
        return None
    reading = (
        MeterReading.objects.filter(unit=unit, meter=meter.strip(), period__lt=as_of)
        .order_by("-period", "-submitted_at", "-id")
        .first()
    )
    if reading is None:
        return None
    return {
        "period": reading.period.isoformat(),
        "current_value": str(reading.current_value),
    }


def _move_out_final_readings(unit, as_of, readings, request):
    if not isinstance(readings, list):
        raise ValueError("final_readings must be an array.")
    configurations = _move_out_utility_configs(unit, as_of)
    if not configurations and readings:
        raise ValueError("This unit has no metered utilities configured for the move-out date.")
    requested_by_type = {}
    for item in readings:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("utility_type"), str)
            or item["utility_type"] in requested_by_type
        ):
            raise ValueError("Each final reading must specify one unique utility_type.")
        requested_by_type[item["utility_type"]] = item
    configured_types = {configuration.utility_type for configuration in configurations}
    if set(requested_by_type) != configured_types:
        raise ValueError("A final reading is required for every metered utility configured on the unit.")

    reading_payloads = []
    total = Decimal("0.00")
    for utility in configurations:
        item = requested_by_type[utility.utility_type]
        meter = utility.metadata.get("meter") or unit.meters.get(utility.utility_type)
        if not isinstance(meter, str) or not meter.strip():
            raise ValueError(f"No meter identifier is configured for {utility.name}.")
        previous = (
            MeterReading.objects.filter(unit=unit, meter=meter.strip(), period__lt=as_of)
            .order_by("-period", "-submitted_at", "-id")
            .first()
        )
        if previous is None:
            raise ValueError(f"A prior meter reading is required to calculate {utility.name}.")
        try:
            current_value = Decimal(str(item.get("current_value")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"The final reading for {utility.name} must be a valid number.") from exc
        if not current_value.is_finite() or current_value < previous.current_value:
            raise ValueError(f"The final reading for {utility.name} cannot be below its previous reading.")

        reading = MeterReading.objects.filter(
            unit=unit,
            meter=meter.strip(),
            period=as_of,
        ).first()
        if reading:
            if (
                reading.current_value != current_value
                or reading.previous_value != previous.current_value
            ):
                raise ValueError(f"A different meter reading is already recorded for {utility.name}.")
        else:
            reading = MeterReading(
                unit=unit,
                meter=meter.strip(),
                period=as_of,
                previous_value=previous.current_value,
                current_value=current_value,
                submitted_by=request.user,
            )
            reading.full_clean()
            reading.save()
            reading_payload = _meter_reading_payload(reading)
            audit_source = getattr(request, "audit_source", "")
            if audit_source:
                reading_payload["source"] = audit_source
            record_audit_event(
                request,
                "meter_reading.created",
                "MeterReading",
                new_values=reading_payload,
            )

        usage = current_value - previous.current_value
        charge = max(usage * utility.rate_per_unit, utility.minimum_charge).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        total += charge
        reading_payloads.append(
            {
                "utility_type": utility.utility_type,
                "name": utility.name,
                "meter": meter.strip(),
                "period": as_of.isoformat(),
                "previous_value": str(previous.current_value),
                "current_value": str(current_value),
                "usage": str(usage),
                "rate_per_unit": str(utility.rate_per_unit),
                "minimum_charge": str(utility.minimum_charge),
                "amount": str(charge),
                "reading_id": reading.pk,
            }
        )
    return reading_payloads, total


def _move_out_invoice_balance_snapshot(tenancy, as_of, utility_invoice):
    balances = {
        str(invoice.pk): str(_move_out_invoice_due(invoice, as_of))
        for invoice in Invoice.objects.filter(
            tenancy=tenancy,
            invoice_type=Invoice.TYPE_STANDARD,
            status=Invoice.STATUS_ISSUED,
        ).select_related("branch__arrears_policy")
    }
    utility_balance = (
        _move_out_invoice_due(utility_invoice, as_of, include_penalty=False)
        if utility_invoice and utility_invoice.status == Invoice.STATUS_ISSUED
        else Decimal("0.00")
    )
    return {
        "standard_invoice_balances": balances,
        "utility_invoice_id": utility_invoice.pk if utility_invoice else None,
        "utility_invoice_balance": str(utility_balance),
    }


def _move_out_approval_hash(settlement):
    snapshot = {
        "id": settlement.pk,
        "tenancy_id": settlement.tenancy_id,
        "notice_id": settlement.notice_id,
        "exit_inspection_id": settlement.exit_inspection_id,
        "actual_move_out_date": settlement.actual_move_out_date.isoformat(),
        "final_readings": settlement.final_readings,
        "damage_items": settlement.damage_items,
        "maintenance_required": settlement.maintenance_required,
        "deposit_balance": str(settlement.deposit_balance),
        "arrears_amount": str(settlement.arrears_amount),
        "utility_amount": str(settlement.utility_amount),
        "damages_amount": str(settlement.damages_amount),
        "deduction_from_deposit": str(settlement.deduction_from_deposit),
        "refund_amount": str(settlement.refund_amount),
        "shortfall_amount": str(settlement.shortfall_amount),
        "invoice_balance_snapshot": settlement.invoice_balance_snapshot,
        "prepared_by_id": settlement.prepared_by_id,
        "reviewed_by_id": settlement.reviewed_by_id,
        "reviewed_at": settlement.reviewed_at.isoformat() if settlement.reviewed_at else None,
    }
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _move_out_settlement_balances_changed(settlement):
    if not settlement.invoice_balance_snapshot:
        return True
    current = _move_out_invoice_balance_snapshot(
        settlement.tenancy,
        settlement.actual_move_out_date,
        None,
    )
    return current != settlement.invoice_balance_snapshot


def _move_out_settlement_pdf(settlement):
    tenancy = settlement.tenancy
    lines = [
        "FINAL MOVE-OUT SETTLEMENT STATEMENT",
        f"Tenant: {tenancy.tenant.name}",
        f"Property: {tenancy.unit.property.name}",
        f"Unit: {tenancy.unit.code}",
        f"Move-out date: {settlement.actual_move_out_date.isoformat()}",
        f"Statement status: {settlement.status.replace('_', ' ').title()}",
        "",
        "SETTLEMENT",
        f"Security deposit held: KES {settlement.deposit_balance}",
        f"Rent and other arrears: KES {settlement.arrears_amount}",
        f"Final metered utilities deducted from deposit: KES {settlement.utility_amount}",
    ]
    for reading in settlement.final_readings:
        lines.append(
            f"{reading['name']}: {reading['usage']} units x KES "
            f"{reading['rate_per_unit']} = KES {reading['amount']}"
        )
    lines.extend([
        f"Damage deductions: KES {settlement.damages_amount}",
        f"Total deposit deduction: KES {settlement.deduction_from_deposit}",
        f"Refund due: KES {settlement.refund_amount}",
        f"Tenant shortfall: KES {settlement.shortfall_amount}",
        f"Payment reference: {settlement.payout_reference or 'Not recorded'}",
        f"Payout date: {settlement.payout_date.isoformat() if settlement.payout_date else 'Not recorded'}",
        "",
        "This statement is retained in the tenant portal. Contact property management",
        "if any settlement amount or final meter reading requires clarification.",
    ])
    return _simple_pdf(lines)


def _require_move_out_recalculation(settlement, request, reason):
    old_values = _move_out_settlement_payload(settlement)
    settlement.status = MoveOutSettlement.STATUS_RECALCULATION_REQUIRED
    settlement.reviewed_by = None
    settlement.reviewed_at = None
    settlement.approval_hash = ""
    settlement.rejection_reason = reason
    settlement.save(
        update_fields=[
            "status",
            "reviewed_by",
            "reviewed_at",
            "approval_hash",
            "rejection_reason",
            "updated_at",
        ]
    )
    record_audit_event(
        request,
        "move_out_settlement.recalculation_required",
        "MoveOutSettlement",
        old_values=old_values,
        new_values=_move_out_settlement_payload(settlement),
    )


def _validate_move_out_inspection_readings(unit, as_of, readings):
    if not isinstance(readings, list):
        raise ValueError("final_readings must be an array.")
    configurations = _move_out_utility_configs(unit, as_of)
    if not configurations and readings:
        raise ValueError("This unit has no metered utilities configured for the move-out date.")
    requested = {}
    for item in readings:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("utility_type"), str)
            or item["utility_type"] in requested
        ):
            raise ValueError("Each final reading must specify one unique utility_type.")
        requested[item["utility_type"]] = item
    if set(requested) != {item.utility_type for item in configurations}:
        raise ValueError("A final reading is required for every metered utility configured on the unit.")

    validated = []
    for utility in configurations:
        meter = utility.metadata.get("meter") or unit.meters.get(utility.utility_type)
        if not isinstance(meter, str) or not meter.strip():
            raise ValueError(f"No meter identifier is configured for {utility.name}.")
        previous = MeterReading.objects.filter(
            unit=unit,
            meter=meter.strip(),
            period__lt=as_of,
        ).order_by("-period", "-submitted_at", "-id").first()
        if previous is None:
            raise ValueError(f"A prior meter reading is required for {utility.name}.")
        try:
            current_value = Decimal(str(requested[utility.utility_type].get("current_value")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"The final reading for {utility.name} must be a valid number.") from exc
        if not current_value.is_finite() or current_value < previous.current_value:
            raise ValueError(f"The final reading for {utility.name} cannot be below its previous reading.")
        validated.append(
            {
                "utility_type": utility.utility_type,
                "current_value": str(current_value),
            }
        )
    return validated


def _move_out_damage_items(value):
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError("damage_items must be an array of at most 50 items.")
    items = []
    total = Decimal("0.00")
    for item in value:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("description"), str)
            or not item["description"].strip()
            or len(item["description"].strip()) > 255
        ):
            raise ValueError("Each damage item requires a description of at most 255 characters.")
        try:
            amount = Decimal(str(item.get("amount")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("Each damage amount must be a valid positive number.") from exc
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Each damage amount must be a valid positive number.")
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total += amount
        items.append({"description": item["description"].strip(), "amount": str(amount)})
    return items, total


@require_GET
def tenant_move_out_notice(request):
    tenant, error = _move_out_tenant(request)
    if error:
        return error
    tenancies = list(
        Tenancy.objects.filter(
            tenant=tenant,
            status=Tenancy.STATUS_ACTIVE,
            move_out_settlement__isnull=True,
        )
        .select_related("unit__property")
        .order_by("-start_date", "-id")
    )
    if not tenancies:
        return _problem(404, "Active tenancy not found", "No active tenancy is available.", "active-tenancy-not-found")
    tenancy_id = request.GET.get("tenancy_id")
    tenancy = next(
        (item for item in tenancies if str(item.pk) == tenancy_id),
        tenancies[0],
    )
    if tenancy_id and str(tenancy.pk) != tenancy_id:
        return _problem(404, "Tenancy not found", "The active tenancy is unavailable.", "tenancy-not-found")
    notice = tenancy.move_out_notices.first()
    return JsonResponse(
        {
            "tenancy_id": tenancy.pk,
            "unit_code": tenancy.unit.code,
            "property_name": tenancy.unit.property.name,
            "notice_period_days": tenancy.notice_period_days,
            "notice": _move_out_notice_payload(notice) if notice else None,
            "tenancies": [
                {
                    "id": item.pk,
                    "unit_code": item.unit.code,
                    "property_name": item.unit.property.name,
                    "notice_period_days": item.notice_period_days,
                    "notice": (
                        _move_out_notice_payload(item.move_out_notices.first())
                        if item.move_out_notices.exists()
                        else None
                    ),
                }
                for item in tenancies
            ],
        }
    )


@require_POST
def submit_tenant_move_out_notice(request):
    tenant, error = _move_out_tenant(request)
    if error:
        return error
    tenancies = Tenancy.objects.filter(
        tenant=tenant,
        status=Tenancy.STATUS_ACTIVE,
        move_out_settlement__isnull=True,
    ).select_related("unit__property").order_by("-start_date", "-id")
    requested_tenancy_id = body.get("tenancy_id") if (body := _json_body(request)) else None
    if body is None:
        return _problem(400, "Invalid request", "A JSON object is required.", "invalid-request")
    if requested_tenancy_id is None and tenancies.count() > 1:
        return _problem(
            400,
            "Select a tenancy",
            "tenancy_id is required when the tenant has multiple active tenancies.",
            "tenancy-selection-required",
        )
    try:
        tenancy = tenancies.get(pk=int(requested_tenancy_id)) if requested_tenancy_id is not None else tenancies.first()
    except (TypeError, ValueError, Tenancy.DoesNotExist):
        tenancy = None
    if tenancy is None:
        return _problem(404, "Active tenancy not found", "No active tenancy is available.", "active-tenancy-not-found")
    reason = body.get("reason", "")
    if reason is None:
        reason = ""
    if not isinstance(reason, str) or len(reason) > 2000:
        return _problem(400, "Invalid notice", "reason must be text of at most 2000 characters.", "invalid-move-out-notice")
    try:
        proposed_date = _move_out_notice_date(body, tenancy)
    except ValueError as exc:
        return _problem(400, "Invalid notice", str(exc), "invalid-move-out-notice")
    if tenancy.move_out_notices.filter(
        status__in=(MoveOutNotice.STATUS_SUBMITTED, MoveOutNotice.STATUS_ACCEPTED)
    ).exists():
        return _problem(409, "Notice already exists", "An active move-out notice already exists.", "move-out-notice-exists")
    try:
        with transaction.atomic():
            tenancy = Tenancy.objects.select_for_update().select_related(
                "tenant", "unit__property"
            ).get(pk=tenancy.pk)
            unit = Unit.objects.select_for_update().get(pk=tenancy.unit_id)
            if unit.status not in {Unit.STATUS_OCCUPIED, Unit.STATUS_NOTICE_GIVEN}:
                return _problem(409, "Unit unavailable", "The active tenancy unit is not occupied.", "move-out-unit-conflict")
            notice = MoveOutNotice(
                tenancy=tenancy,
                requested_by=request.user,
                proposed_move_out_date=proposed_date,
                previous_tenancy_end_date=tenancy.end_date,
                reason=reason.strip(),
            )
            notice.full_clean()
            notice.save()
            if unit.status == Unit.STATUS_OCCUPIED:
                unit.transition_to(Unit.STATUS_NOTICE_GIVEN)
                unit.save(update_fields=["status", "listing_enabled", "updated_at"])
            tenancy.end_date = proposed_date
            tenancy.save(update_fields=["end_date", "updated_at"])
            record_audit_event(
                request,
                "move_out_notice.submitted",
                "MoveOutNotice",
                new_values=_move_out_notice_payload(notice),
            )
            context = _move_out_notice_context(notice)
            _queue_move_out_staff_sms(
                tenancy.unit.property,
                (BRANCH_MANAGER_ROLE, SUPER_ADMIN_ROLE, "Property Officer / Agent"),
                "move_out_notice_submitted",
                context,
                request.user,
                request,
            )
    except (ValidationError, IntegrityError) as exc:
        detail = exc.message_dict if isinstance(exc, ValidationError) else "An active notice already exists."
        return _problem(409, "Notice unavailable", str(detail), "move-out-notice-conflict")
    except (ImproperlyConfigured, ValueError) as exc:
        logger.exception("Could not queue move-out notice notification.")
        return _problem(503, "Move-out notification unavailable", str(exc), "move-out-notification-unavailable")
    return JsonResponse({"notice": _move_out_notice_payload(notice)}, status=201)


@require_POST
def withdraw_tenant_move_out_notice(request, notice_id):
    tenant, error = _move_out_tenant(request)
    if error:
        return error
    notice = MoveOutNotice.objects.filter(
        pk=notice_id,
        tenancy__tenant=tenant,
        status=MoveOutNotice.STATUS_SUBMITTED,
    ).select_related("tenancy__unit__property", "tenancy__tenant", "requested_by").first()
    if notice is None:
        return _problem(404, "Notice not found", "The pending move-out notice is unavailable.", "move-out-notice-not-found")
    try:
        with transaction.atomic():
            tenancy = Tenancy.objects.select_for_update().get(pk=notice.tenancy_id)
            unit = Unit.objects.select_for_update().get(pk=tenancy.unit_id)
            notice.status = MoveOutNotice.STATUS_WITHDRAWN
            notice.save(update_fields=["status", "updated_at"])
            tenancy.end_date = notice.previous_tenancy_end_date
            tenancy.save(update_fields=["end_date", "updated_at"])
            if unit.status == Unit.STATUS_NOTICE_GIVEN:
                unit.transition_to(Unit.STATUS_OCCUPIED)
                unit.save(update_fields=["status", "listing_enabled", "updated_at"])
            record_audit_event(
                request,
                "move_out_notice.withdrawn",
                "MoveOutNotice",
                old_values={"id": notice.pk, "status": MoveOutNotice.STATUS_SUBMITTED},
                new_values={"id": notice.pk, "status": notice.status},
            )
            _queue_move_out_staff_sms(
                notice.tenancy.unit.property,
                (BRANCH_MANAGER_ROLE, SUPER_ADMIN_ROLE),
                "move_out_notice_withdrawn",
                _move_out_notice_context(notice),
                request.user,
                request,
            )
    except (ImproperlyConfigured, ValueError) as exc:
        logger.exception("Could not queue move-out notice withdrawal notification.")
        return _problem(503, "Move-out notification unavailable", str(exc), "move-out-notification-unavailable")
    return JsonResponse({"notice": _move_out_notice_payload(notice)})


@require_GET
def list_move_out_workflows(request):
    error = _move_out_staff_error(request)
    if error:
        return error
    properties = (
        Property.objects.all()
        if request.user.is_superuser
        else _accessible_properties(request.user)
    )
    notices = (
        MoveOutNotice.objects.filter(tenancy__unit__property__in=properties)
        .select_related(
            "tenancy__tenant",
            "tenancy__unit__property",
            "requested_by",
            "reviewed_by",
        )
    )
    settlements = (
        MoveOutSettlement.objects.filter(tenancy__unit__property__in=properties)
        .select_related(
            "tenancy__tenant",
            "tenancy__unit__property",
            "notice",
            "exit_inspection",
        )
    )
    candidates = []
    active_notices = notices.filter(status=MoveOutNotice.STATUS_ACCEPTED)
    for notice in active_notices:
        tenancy = notice.tenancy
        existing_settlement = MoveOutSettlement.objects.filter(tenancy=tenancy).first()
        if existing_settlement and existing_settlement.status not in {
            MoveOutSettlement.STATUS_REJECTED,
            MoveOutSettlement.STATUS_RECALCULATION_REQUIRED,
        }:
            continue
        if tenancy.status != Tenancy.STATUS_ACTIVE and not (
            existing_settlement
            and existing_settlement.status in {
                MoveOutSettlement.STATUS_REJECTED,
                MoveOutSettlement.STATUS_RECALCULATION_REQUIRED,
            }
        ):
            continue
        account = DepositAccount.objects.filter(tenancy=tenancy).first()
        candidates.append(
            {
                "tenancy_id": tenancy.pk,
                "unit_id": tenancy.unit_id,
                "notice_id": notice.pk,
                "tenant_name": tenancy.tenant.name,
                "property_name": tenancy.unit.property.name,
                "unit_code": tenancy.unit.code,
                "move_out_date": notice.proposed_move_out_date.isoformat(),
                "deposit_balance": str(account.balance if account else Decimal("0.00")),
                "arrears_amount": str(_move_out_arrears(tenancy, timezone.localdate())),
                "metered_utilities": [
                    {
                        "utility_type": utility.utility_type,
                        "name": utility.name,
                        "meter": utility.metadata.get("meter") or tenancy.unit.meters.get(utility.utility_type),
                        "rate_per_unit": str(utility.rate_per_unit),
                        "minimum_charge": str(utility.minimum_charge),
                        "previous_reading": _move_out_meter_baseline(
                            tenancy.unit,
                            utility,
                            notice.proposed_move_out_date,
                        ),
                    }
                    for utility in _move_out_utility_configs(tenancy.unit, notice.proposed_move_out_date)
                ],
            }
        )
    return JsonResponse(
        {
            "notices": [
                {
                    **_move_out_notice_payload(notice),
                    "available_caretakers": (
                        _move_out_available_caretakers(notice.tenancy.unit.property)
                        if notice.status == MoveOutNotice.STATUS_SUBMITTED
                        else []
                    ),
                }
                for notice in notices
            ],
            "settlements": [_move_out_settlement_payload(item) for item in settlements],
            "eligible_tenancies": candidates,
        }
    )


@require_POST
def create_staff_move_out_notice(request):
    error = _move_out_staff_error(request)
    if error:
        return error
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "A JSON object is required.", "invalid-request")
    reason = body.get("reason", "")
    if reason is None:
        reason = ""
    if not isinstance(reason, str) or len(reason) > 2000:
        return _problem(400, "Invalid notice", "reason must be text of at most 2000 characters.", "invalid-move-out-notice")
    try:
        tenancy = Tenancy.objects.filter(
            pk=int(body.get("tenancy_id")),
            status=Tenancy.STATUS_ACTIVE,
            unit__property__in=_accessible_properties(request.user),
        ).select_related("unit__property", "tenant").first()
    except (TypeError, ValueError):
        tenancy = None
    if tenancy is None:
        return _problem(404, "Tenancy not found", "The active tenancy is unavailable.", "tenancy-not-found")
    try:
        proposed_date = _move_out_notice_date(body, tenancy)
    except ValueError as exc:
        return _problem(400, "Invalid notice", str(exc), "invalid-move-out-notice")
    if tenancy.move_out_notices.filter(
        status__in=(MoveOutNotice.STATUS_SUBMITTED, MoveOutNotice.STATUS_ACCEPTED)
    ).exists():
        return _problem(409, "Notice already exists", "An active move-out notice already exists.", "move-out-notice-exists")
    try:
        with transaction.atomic():
            tenancy = Tenancy.objects.select_for_update().select_related(
                "tenant", "unit__property"
            ).get(pk=tenancy.pk)
            unit = Unit.objects.select_for_update().get(pk=tenancy.unit_id)
            if unit.status not in {Unit.STATUS_OCCUPIED, Unit.STATUS_NOTICE_GIVEN}:
                return _problem(409, "Unit unavailable", "The active tenancy unit is not occupied.", "move-out-unit-conflict")
            notice = MoveOutNotice(
                tenancy=tenancy,
                requested_by=request.user,
                proposed_move_out_date=proposed_date,
                previous_tenancy_end_date=tenancy.end_date,
                reason=reason.strip(),
            )
            notice.full_clean()
            notice.save()
            if unit.status == Unit.STATUS_OCCUPIED:
                unit.transition_to(Unit.STATUS_NOTICE_GIVEN)
                unit.save(update_fields=["status", "listing_enabled", "updated_at"])
            tenancy.end_date = proposed_date
            tenancy.save(update_fields=["end_date", "updated_at"])
            record_audit_event(
                request,
                "move_out_notice.staff_recorded",
                "MoveOutNotice",
                new_values=_move_out_notice_payload(notice),
            )
            _queue_move_out_tenant_sms(
                tenancy,
                "move_out_notice_updated",
                {
                    **_move_out_notice_context(notice),
                    "status": "recorded",
                    "note": "",
                },
                request.user,
                request,
            )
            _queue_move_out_staff_sms(
                tenancy.unit.property,
                (BRANCH_MANAGER_ROLE, SUPER_ADMIN_ROLE),
                "move_out_notice_submitted",
                _move_out_notice_context(notice),
                request.user,
                request,
            )
    except (ValidationError, IntegrityError) as exc:
        detail = exc.message_dict if isinstance(exc, ValidationError) else "An active notice already exists."
        return _problem(409, "Notice unavailable", str(detail), "move-out-notice-conflict")
    except (ImproperlyConfigured, ValueError) as exc:
        logger.exception("Could not queue staff-recorded move-out notice notification.")
        return _problem(503, "Move-out notification unavailable", str(exc), "move-out-notification-unavailable")
    return JsonResponse({"notice": _move_out_notice_payload(notice)}, status=201)


@require_POST
def decide_move_out_notice(request, notice_id):
    error = _move_out_staff_error(request)
    if error:
        return error
    body = _json_body(request)
    action = body.get("action") if body else None
    if action not in {"accept", "reject", "withdraw"}:
        return _problem(400, "Invalid decision", "action must be accept, reject, or withdraw.", "invalid-move-out-decision")
    with transaction.atomic():
        notice_reference = (
            MoveOutNotice.objects
            .filter(
                pk=notice_id,
                tenancy__unit__property__in=_accessible_properties(request.user),
            )
            .values("tenancy_id")
            .first()
        )
        if notice_reference is None:
            return _problem(404, "Notice not found", "The move-out notice is unavailable.", "move-out-notice-not-found")
        tenancy = (
            Tenancy.objects.select_for_update()
            .filter(
                pk=notice_reference["tenancy_id"],
                unit__property__in=_accessible_properties(request.user),
            )
            .first()
        )
        if tenancy is None:
            return _problem(404, "Notice not found", "The move-out notice is unavailable.", "move-out-notice-not-found")
        notice = (
            MoveOutNotice.objects.select_for_update()
            .filter(pk=notice_id, tenancy=tenancy)
            .select_related("tenancy__unit", "tenancy__tenant", "tenancy__unit__property", "requested_by")
            .first()
        )
        if notice is None:
            return _problem(404, "Notice not found", "The move-out notice is unavailable.", "move-out-notice-not-found")
        unit = Unit.objects.select_for_update().get(pk=tenancy.unit_id)
        caretaker = None
        if action == "accept":
            if notice.status != MoveOutNotice.STATUS_SUBMITTED:
                return _problem(409, "Notice unavailable", "Only a submitted notice can be accepted.", "move-out-notice-conflict")
            if tenancy.status != Tenancy.STATUS_ACTIVE:
                return _problem(409, "Tenancy unavailable", "Only an active tenancy can be accepted for move-out.", "move-out-notice-conflict")
            if unit.status not in {Unit.STATUS_OCCUPIED, Unit.STATUS_NOTICE_GIVEN}:
                return _problem(409, "Unit unavailable", "The tenancy unit is not in an occupiable status.", "move-out-unit-conflict")
            try:
                caretaker_id = int(body.get("caretaker_id"))
            except (TypeError, ValueError):
                return _problem(
                    400,
                    "Caretaker required",
                    "Select an active caretaker assigned to this property before accepting the notice.",
                    "move-out-caretaker-required",
                )
            caretaker = get_user_model().objects.filter(
                pk=caretaker_id,
                is_active=True,
            ).first()
            if (
                caretaker is None
                or not _is_caretaker(caretaker)
                or not UserProfile.objects.filter(
                    user=caretaker,
                    branches=unit.property.branch,
                ).exists()
                or not PropertyCaretakerAssignment.objects.filter(
                    property=unit.property,
                    caretaker=caretaker,
                    ended_at__isnull=True,
                ).exists()
            ):
                return _problem(
                    400,
                    "Invalid caretaker",
                    "The selected caretaker must be active, belong to the property branch, and be assigned to this property.",
                    "invalid-move-out-caretaker",
                )
            notice.status = MoveOutNotice.STATUS_ACCEPTED
            notice.reviewed_by = request.user
            notice.reviewed_at = timezone.now()
            notice.review_note = str(body.get("review_note") or "").strip()
            if unit.status == Unit.STATUS_OCCUPIED:
                unit.transition_to(Unit.STATUS_NOTICE_GIVEN)
                unit.save(update_fields=["status", "listing_enabled", "updated_at"])
            tenancy.end_date = notice.proposed_move_out_date
            tenancy.save(update_fields=["end_date", "updated_at"])
        elif action == "reject":
            if notice.status != MoveOutNotice.STATUS_SUBMITTED:
                return _problem(409, "Notice unavailable", "Only a submitted notice can be rejected.", "move-out-notice-conflict")
            note = str(body.get("review_note") or "").strip()
            if not note:
                return _problem(400, "Invalid decision", "A review_note is required when rejecting notice.", "invalid-move-out-decision")
            notice.status = MoveOutNotice.STATUS_REJECTED
            notice.reviewed_by = request.user
            notice.reviewed_at = timezone.now()
            notice.review_note = note
            tenancy.end_date = notice.previous_tenancy_end_date
            tenancy.save(update_fields=["end_date", "updated_at"])
            if unit.status == Unit.STATUS_NOTICE_GIVEN:
                unit.transition_to(Unit.STATUS_OCCUPIED)
                unit.save(update_fields=["status", "listing_enabled", "updated_at"])
        else:
            if notice.status != MoveOutNotice.STATUS_ACCEPTED:
                return _problem(409, "Notice unavailable", "Only an accepted notice can be withdrawn by staff.", "move-out-notice-conflict")
            if MoveOutSettlement.objects.filter(tenancy=tenancy).exists():
                return _problem(409, "Settlement exists", "A move-out settlement prevents notice withdrawal.", "move-out-settlement-exists")
            if notice.inspections.filter(
                status__in=(
                    PropertyInspection.STATUS_IN_PROGRESS,
                    PropertyInspection.STATUS_COMPLETED,
                )
            ).exists():
                return _problem(
                    409,
                    "Inspection already started",
                    "A notice cannot be withdrawn after its assigned exit inspection has started.",
                    "move-out-inspection-started",
                )
            notice.status = MoveOutNotice.STATUS_WITHDRAWN
            notice.reviewed_by = request.user
            notice.reviewed_at = timezone.now()
            notice.review_note = str(body.get("review_note") or "").strip()
            tenancy.end_date = notice.previous_tenancy_end_date
            tenancy.save(update_fields=["end_date", "updated_at"])
            if unit.status == Unit.STATUS_NOTICE_GIVEN:
                unit.transition_to(Unit.STATUS_OCCUPIED)
                unit.save(update_fields=["status", "listing_enabled", "updated_at"])
        notice.full_clean()
        notice.save()
        if action == "accept":
            inspection = PropertyInspection(
                property=unit.property,
                unit=unit,
                move_out_notice=notice,
                title=f"Move-out inspection - {unit.code}",
                inspection_type="move_out",
                scheduled_for=timezone.make_aware(
                    datetime.combine(
                        notice.proposed_move_out_date,
                        datetime.min.time(),
                    ).replace(hour=9)
                ),
                responsible_staff=caretaker,
                created_by=request.user,
                checklist=[
                    {"title": "Room condition and damage", "status": "pending"},
                    {"title": "Fixtures, keys, and access devices", "status": "pending"},
                    {"title": "Final utility meter readings", "status": "pending"},
                ],
            )
            inspection.full_clean()
            inspection.save()
            record_audit_event(
                request,
                "property_inspection.created",
                "PropertyInspection",
                new_values=_inspection_payload(inspection),
            )
        elif action == "withdraw":
            for inspection in notice.inspections.filter(
                status=PropertyInspection.STATUS_SCHEDULED
            ):
                inspection.status = PropertyInspection.STATUS_CANCELLED
                inspection.save(update_fields=["status", "updated_at"])
                record_audit_event(
                    request,
                    "property_inspection.cancelled",
                    "PropertyInspection",
                    new_values=_inspection_payload(inspection),
                )
        record_audit_event(
            request,
            f"move_out_notice.{notice.status}",
            "MoveOutNotice",
            new_values=_move_out_notice_payload(notice),
        )
        try:
            context = {
                **_move_out_notice_context(notice),
                "status": notice.status,
                "note": f" Note: {notice.review_note}" if notice.review_note else "",
            }
            _queue_move_out_tenant_sms(
                tenancy,
                "move_out_notice_updated",
                context,
                request.user,
                request,
            )
            if action == "accept":
                context["move_out_date"] = notice.proposed_move_out_date.isoformat()
                _queue_move_out_user_sms(
                    caretaker,
                    "move_out_inspection_assigned",
                    context,
                    request.user,
                    request,
                )
                _queue_move_out_tenant_sms(
                    tenancy,
                    "tenant_move_out_inspection_scheduled",
                    context,
                    request.user,
                    request,
                )
        except (ImproperlyConfigured, ValueError) as exc:
            transaction.set_rollback(True)
            logger.exception("Could not queue move-out notice decision notification.")
            return _problem(503, "Move-out notification unavailable", str(exc), "move-out-notification-unavailable")
    return JsonResponse({"notice": _move_out_notice_payload(notice)})


@require_POST
def create_move_out_settlement(request):
    error = _move_out_staff_error(request)
    if error:
        return error
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "A JSON object is required.", "invalid-request")
    try:
        tenancy_id = int(body.get("tenancy_id"))
        notice_id = int(body.get("notice_id"))
        inspection_id = int(body.get("exit_inspection_id"))
        actual_date = date.fromisoformat(str(body.get("actual_move_out_date", "")))
    except (TypeError, ValueError):
        return _problem(
            400,
            "Invalid settlement",
            "tenancy_id, notice_id, exit_inspection_id, and actual_move_out_date are required.",
            "invalid-move-out-settlement",
        )
    if actual_date > timezone.localdate():
        return _problem(400, "Invalid settlement", "The actual move-out date cannot be in the future.", "invalid-move-out-settlement")
    try:
        damage_items, damages_total = _move_out_damage_items(body.get("damage_items", []))
    except ValueError as exc:
        return _problem(400, "Invalid settlement", str(exc), "invalid-move-out-settlement")
    maintenance_required = body.get("maintenance_required")
    if maintenance_required is not None and not isinstance(maintenance_required, bool):
        return _problem(
            400,
            "Invalid settlement",
            "maintenance_required must be a boolean.",
            "invalid-move-out-settlement",
        )

    try:
        with transaction.atomic():
            tenancy = (
                Tenancy.objects.select_for_update()
                .filter(
                    pk=tenancy_id,
                    unit__property__in=_accessible_properties(request.user),
                )
                .select_related("tenant", "unit__property")
                .first()
            )
            notice = (
                MoveOutNotice.objects.select_for_update()
                .filter(
                    pk=notice_id,
                    tenancy=tenancy,
                    status=MoveOutNotice.STATUS_ACCEPTED,
                )
                .first()
            ) if tenancy else None
            inspection = (
                PropertyInspection.objects.filter(
                    pk=inspection_id,
                    property__in=_accessible_properties(request.user),
                )
                .first()
            )
            if tenancy is None or notice is None:
                return _problem(404, "Move-out notice not found", "The accepted notice is unavailable.", "move-out-notice-not-found")
            if tenancy.status != Tenancy.STATUS_ACTIVE and not MoveOutSettlement.objects.filter(
                tenancy=tenancy,
                status=MoveOutSettlement.STATUS_REJECTED,
            ).exists():
                return _problem(409, "Tenancy unavailable", "The tenancy has already been moved out.", "move-out-tenancy-conflict")
            settlement = MoveOutSettlement.objects.select_for_update().filter(tenancy=tenancy).first()
            if settlement and settlement.status not in {
                MoveOutSettlement.STATUS_REJECTED,
                MoveOutSettlement.STATUS_RECALCULATION_REQUIRED,
            }:
                return _problem(409, "Settlement already exists", "The tenancy already has a move-out settlement.", "move-out-settlement-exists")
            if inspection is None:
                return _problem(404, "Exit inspection not found", "The inspection is unavailable.", "exit-inspection-not-found")
            if (
                inspection.unit_id != tenancy.unit_id
                or inspection.status != PropertyInspection.STATUS_COMPLETED
                or inspection.inspection_type.lower() not in {"move_out", "exit"}
                or (
                    notice.inspections.exists()
                    and inspection.move_out_notice_id != notice.pk
                )
            ):
                return _problem(
                    400,
                    "Invalid exit inspection",
                    "Select a completed move_out or exit inspection for this tenancy unit.",
                    "invalid-exit-inspection",
                )
            if (
                inspection.actual_move_out_date is not None
                and inspection.actual_move_out_date != actual_date
            ):
                return _problem(
                    400,
                    "Invalid exit inspection",
                    "The settlement move-out date must match the caretaker's completed inspection.",
                    "invalid-exit-inspection",
                )
            if actual_date < tenancy.start_date:
                return _problem(400, "Invalid settlement", "The move-out date cannot precede the tenancy start.", "invalid-move-out-settlement")

            submitted_readings = body.get("final_readings")
            if submitted_readings is None:
                submitted_readings = inspection.final_readings or []
            final_readings, utility_total = _move_out_final_readings(
                tenancy.unit,
                actual_date,
                submitted_readings,
                request,
            )
            inspection.actual_move_out_date = actual_date
            inspection.final_readings = final_readings
            inspection.save(
                update_fields=[
                    "actual_move_out_date",
                    "final_readings",
                    "updated_at",
                ]
            )
            if "damage_items" not in body and inspection.damage_items:
                damage_items, damages_total = _move_out_damage_items(
                    inspection.damage_items
                )
            account = (
                DepositAccount.objects.select_for_update()
                .filter(tenancy=tenancy)
                .first()
            )
            deposit_balance = account.balance if account else Decimal("0.00")
            arrears_total = _move_out_arrears(tenancy, actual_date)
            total_deductions = arrears_total + utility_total + damages_total
            deduction = min(deposit_balance, total_deductions)
            refund = deposit_balance - deduction
            shortfall = total_deductions - deduction
            defaults = {
                "notice": notice,
                "exit_inspection": inspection,
                "actual_move_out_date": actual_date,
                "final_readings": final_readings,
                "damage_items": damage_items,
                "maintenance_required": (
                    maintenance_required
                    if maintenance_required is not None
                    else inspection.maintenance_required
                ),
                "deposit_balance": deposit_balance,
                "arrears_amount": arrears_total,
                "utility_amount": utility_total,
                "damages_amount": damages_total,
                "deduction_from_deposit": deduction,
                "refund_amount": refund,
                "shortfall_amount": shortfall,
                "invoice_balance_snapshot": _move_out_invoice_balance_snapshot(
                    tenancy,
                    actual_date,
                    None,
                ),
                "status": MoveOutSettlement.STATUS_AWAITING_APPROVAL,
                "prepared_by": request.user,
                "reviewed_by": None,
                "reviewed_at": None,
                "approval_hash": "",
                "rejection_reason": "",
                "payout_reference": None,
                "payout_date": None,
                "payout_recorded_by": None,
            }
            if settlement:
                for field, value in defaults.items():
                    setattr(settlement, field, value)
                settlement.full_clean()
                settlement.save()
            else:
                settlement = MoveOutSettlement(tenancy=tenancy, **defaults)
                settlement.full_clean()
                settlement.save()
            tenancy.end_date = actual_date
            tenancy.save(update_fields=["end_date", "updated_at"])
            unit = tenancy.unit
            if unit.status != Unit.STATUS_DEPOSIT_PENDING:
                unit.transition_to(Unit.STATUS_DEPOSIT_PENDING)
                unit.save(update_fields=["status", "listing_enabled", "updated_at"])
            record_audit_event(
                request,
                "move_out_settlement.submitted",
                "MoveOutSettlement",
                new_values=_move_out_settlement_payload(settlement),
            )
            try:
                context = _move_out_settlement_context(settlement)
                _queue_move_out_staff_sms(
                    tenancy.unit.property,
                    (BRANCH_MANAGER_ROLE, SUPER_ADMIN_ROLE),
                    "move_out_settlement_submitted",
                    context,
                    request.user,
                    request,
                )
                _queue_move_out_tenant_sms(
                    tenancy,
                    "move_out_settlement_submitted",
                    context,
                    request.user,
                    request,
                )
            except (ImproperlyConfigured, ValueError) as notification_error:
                transaction.set_rollback(True)
                logger.exception("Could not queue move-out settlement notification.")
                return _problem(
                    503,
                    "Move-out notification unavailable",
                    str(notification_error),
                    "move-out-notification-unavailable",
                )
    except ValueError as exc:
        return _problem(400, "Invalid final readings", str(exc), "invalid-final-readings")
    except ValidationError as exc:
        return _problem(400, "Invalid settlement", str(exc.message_dict), "invalid-move-out-settlement")
    except IntegrityError:
        return _problem(409, "Settlement conflict", "The move-out settlement or final reading already exists.", "move-out-settlement-conflict")
    return JsonResponse({"settlement": _move_out_settlement_payload(settlement)}, status=201)


@require_POST
def decide_move_out_settlement(request, settlement_id):
    error = _move_out_staff_error(request)
    if error:
        return error
    if not _has_role(request.user, BRANCH_MANAGER_ROLE):
        return _problem(
            403,
            "Permission denied",
            "Only a Branch Manager can approve a move-out settlement.",
            "move-out-approver-required",
        )
    body = _json_body(request)
    action = body.get("action") if body else None
    if action not in {"approve", "reject"}:
        return _problem(400, "Invalid decision", "action must be approve or reject.", "invalid-move-out-decision")
    review_note = str(body.get("review_note") or "").strip()
    if action == "reject" and not review_note:
        return _problem(400, "Invalid decision", "A review_note is required when rejecting a settlement.", "invalid-move-out-decision")
    with transaction.atomic():
        settlement = (
            MoveOutSettlement.objects.select_for_update()
            .filter(
                pk=settlement_id,
                tenancy__unit__property__in=_accessible_properties(request.user),
            )
            .select_related("tenancy__tenant", "tenancy__unit__property", "notice", "exit_inspection")
            .first()
        )
        if settlement is None:
            return _problem(404, "Settlement not found", "The move-out settlement is unavailable.", "move-out-settlement-not-found")
        if settlement.status != MoveOutSettlement.STATUS_AWAITING_APPROVAL:
            return _problem(409, "Settlement unavailable", "Only a settlement awaiting approval can be reviewed.", "move-out-settlement-conflict")
        if action == "approve" and settlement.prepared_by_id == request.user.pk:
            return _problem(403, "Permission denied", "The settlement preparer cannot approve their own settlement.", "move-out-self-approval-denied")
        if action == "approve" and _move_out_settlement_balances_changed(settlement):
            _require_move_out_recalculation(
                settlement,
                request,
                "Tenant invoice payments changed after settlement preparation.",
            )
            return _problem(
                409,
                "Settlement recalculation required",
                "Invoice balances changed after this settlement was prepared. Recalculate and resubmit it before approval.",
                "move-out-recalculation-required",
            )
        settlement.status = (
            MoveOutSettlement.STATUS_APPROVED
            if action == "approve"
            else MoveOutSettlement.STATUS_REJECTED
        )
        settlement.reviewed_by = request.user
        settlement.reviewed_at = timezone.now()
        settlement.approval_hash = (
            _move_out_approval_hash(settlement) if action == "approve" else ""
        )
        settlement.rejection_reason = review_note if action == "reject" else ""
        try:
            settlement.full_clean()
            settlement.save(update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "approval_hash",
                "rejection_reason",
                "updated_at",
            ])
        except ValidationError as exc:
            return _problem(400, "Invalid decision", str(exc.message_dict), "invalid-move-out-decision")
        record_audit_event(
            request,
            f"move_out_settlement.{settlement.status}",
            "MoveOutSettlement",
            new_values=_move_out_settlement_payload(settlement),
        )
        try:
            context = _move_out_settlement_context(settlement)
            if action == "approve":
                _queue_move_out_tenant_sms(
                    settlement.tenancy,
                    "move_out_settlement_approved",
                    context,
                    request.user,
                    request,
                )
                _queue_move_out_staff_sms(
                    settlement.tenancy.unit.property,
                    ("Accountant",),
                    "move_out_settlement_approved",
                    context,
                    request.user,
                    request,
                )
            else:
                _queue_move_out_tenant_sms(
                    settlement.tenancy,
                    "move_out_settlement_rejected",
                    {**context, "reason": review_note},
                    request.user,
                    request,
                )
        except (ImproperlyConfigured, ValueError) as notification_error:
            transaction.set_rollback(True)
            logger.exception("Could not queue move-out settlement decision notification.")
            return _problem(
                503,
                "Move-out notification unavailable",
                str(notification_error),
                "move-out-notification-unavailable",
            )
    return JsonResponse({"settlement": _move_out_settlement_payload(settlement)})


@require_POST
def record_move_out_payout(request, settlement_id):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not (
        request.user.is_superuser
        or (_has_role(request.user, "Accountant") and requires_finance_mfa(request.user))
    ):
        return _problem(403, "Permission denied", "Finance authorization is required to record a payout.", "move-out-payout-denied")
    settlement = MoveOutSettlement.objects.filter(
        pk=settlement_id,
        tenancy__unit__property__in=_accessible_properties(request.user),
    ).select_related("tenancy__tenant", "tenancy__unit__property", "notice", "exit_inspection").first()
    if settlement is None:
        return _problem(404, "Settlement not found", "The move-out settlement is unavailable.", "move-out-settlement-not-found")
    if settlement.status != MoveOutSettlement.STATUS_APPROVED:
        return _problem(409, "Settlement unavailable", "Only an approved settlement can be paid.", "move-out-settlement-conflict")
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "A JSON object is required.", "invalid-request")
    reference = body.get("payout_reference")
    if settlement.refund_amount > 0 and (
        not isinstance(reference, str) or not reference.strip() or len(reference.strip()) > 100
    ):
        return _problem(400, "Invalid payout", "A payout reference of at most 100 characters is required.", "invalid-move-out-payout")
    try:
        payout_date = date.fromisoformat(str(body.get("payout_date", "")))
    except ValueError:
        return _problem(400, "Invalid payout", "payout_date must be a valid ISO date.", "invalid-move-out-payout")
    if payout_date > timezone.localdate() or payout_date < settlement.actual_move_out_date:
        return _problem(400, "Invalid payout", "The payout date must be on or after move-out and cannot be in the future.", "invalid-move-out-payout")
    reference = reference.strip() if isinstance(reference, str) else ""
    try:
        with transaction.atomic():
            locked = MoveOutSettlement.objects.select_for_update().get(pk=settlement.pk)
            if locked.status != MoveOutSettlement.STATUS_APPROVED:
                return _problem(409, "Settlement unavailable", "Only an approved settlement can be paid.", "move-out-settlement-conflict")
            if _move_out_settlement_balances_changed(locked):
                _require_move_out_recalculation(
                    locked,
                    request,
                    "Tenant invoice payments changed after settlement approval.",
                )
                return _problem(
                    409,
                    "Settlement recalculation required",
                    "Invoice balances changed after approval. Recalculate and obtain approval again before recording payout.",
                    "move-out-recalculation-required",
                )
            if not secrets.compare_digest(
                locked.approval_hash,
                _move_out_approval_hash(locked),
            ):
                _require_move_out_recalculation(
                    locked,
                    request,
                    "The settlement no longer matches its immutable approval hash.",
                )
                return _problem(
                    409,
                    "Settlement approval changed",
                    "The approved settlement has changed. Recalculate and obtain approval again.",
                    "move-out-approval-hash-mismatch",
                )
            account = (
                DepositAccount.objects.select_for_update()
                .filter(tenancy=locked.tenancy)
                .first()
            )
            current_balance = account.balance if account else Decimal("0.00")
            if current_balance != locked.deposit_balance:
                return _problem(
                    409,
                    "Deposit balance changed",
                    "The deposit ledger changed after approval. Reject and recalculate this settlement before recording payout.",
                    "move-out-deposit-balance-changed",
                )
            if account and locked.deduction_from_deposit:
                account.balance -= locked.deduction_from_deposit
                DepositEntry.objects.create(
                    account=account,
                    entry_type=DepositEntry.TYPE_DEDUCTION,
                    amount=locked.deduction_from_deposit,
                    reference=f"MOVEOUT-{locked.pk}-DEDUCTION",
                    description=f"Approved move-out deductions for tenancy {locked.tenancy_id}",
                    created_by=request.user,
                )
            if account and locked.refund_amount:
                account.balance -= locked.refund_amount
                DepositEntry.objects.create(
                    account=account,
                    entry_type=DepositEntry.TYPE_REFUND,
                    amount=locked.refund_amount,
                    reference=reference,
                    description=f"Approved move-out refund for tenancy {locked.tenancy_id}",
                    created_by=request.user,
                )
            if account:
                account.save(update_fields=["balance", "updated_at"])
            locked.status = MoveOutSettlement.STATUS_PAID
            locked.payout_reference = reference or None
            locked.payout_date = payout_date
            locked.payout_recorded_by = request.user
            locked.full_clean()
            locked.save(update_fields=[
                "status",
                "payout_reference",
                "payout_date",
                "payout_recorded_by",
                "updated_at",
            ])
            unit = locked.tenancy.unit
            if unit.status == Unit.STATUS_DEPOSIT_PENDING:
                unit.transition_to(
                    Unit.STATUS_MAINTENANCE
                    if locked.maintenance_required
                    else Unit.STATUS_VACANT
                )
                unit.save(update_fields=["status", "listing_enabled", "updated_at"])
            locked.tenancy.status = Tenancy.STATUS_ENDED
            locked.tenancy.end_date = locked.actual_move_out_date
            locked.tenancy.save(update_fields=["status", "end_date", "updated_at"])
            record_audit_event(
                request,
                "move_out_settlement.paid",
                "MoveOutSettlement",
                new_values=_move_out_settlement_payload(locked),
            )
            try:
                _queue_move_out_tenant_sms(
                    locked.tenancy,
                    "move_out_settlement_paid",
                    {
                        **_move_out_settlement_context(locked),
                        "reference": reference or "No refund due",
                        "statement_url": (
                            f"{settings.PUBLIC_SITE_URL.rstrip('/')}/api/tenant/"
                            f"move-out-settlements/{locked.pk}/statement"
                        ),
                    },
                    request.user,
                    request,
                )
            except (ImproperlyConfigured, ValueError) as notification_error:
                transaction.set_rollback(True)
                logger.exception("Could not queue move-out payout notification.")
                return _problem(
                    503,
                    "Move-out notification unavailable",
                    str(notification_error),
                    "move-out-notification-unavailable",
                )
    except IntegrityError:
        return _problem(409, "Duplicate payout reference", "The payout reference has already been recorded.", "duplicate-move-out-payout")
    except ValidationError as exc:
        return _problem(400, "Invalid payout", str(exc.message_dict), "invalid-move-out-payout")
    return JsonResponse({"settlement": _move_out_settlement_payload(locked)})


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
                tenancy__unit__property__in=_accessible_manager_properties(request.user),
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
    activation_result = _send_move_in_invoice_activation(request, invoice.pk)
    return JsonResponse(
        {
            "payment": _payment_payload(payment),
            "tenant_activation": activation_result,
        }
    )


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
        "move_in_date": package.tenancy.start_date.isoformat(),
        "agreement_reference": package.agreement_reference,
        "generated_at": package.generated_at.isoformat(),
        "handover_inventory": package.handover_inventory,
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
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def _activation_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_ke_phone(phone):
    value = re.sub(r"[\s()+-]", "", str(phone or ""))
    if value.startswith("0") and len(value) == 10:
        value = f"254{value[1:]}"
    if not re.fullmatch(r"254[17]\d{8}", value):
        raise ValueError("Use a Kenyan mobile number such as 0712345678 or 254712345678.")
    return value


def _simple_pdf(lines):
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
    return _simple_pdf(lines)


def _payment_receipt_pdf(tenancy, invoice, payment, receipt):
    return _simple_pdf(
        [
            "MOVE-IN PAYMENT RECEIPT",
            f"Receipt: {receipt.number}",
            f"Invoice: {invoice.number}",
            f"Tenant: {tenancy.tenant.name}",
            f"Unit: {tenancy.unit.code}",
            f"Payment reference: {payment.reference}",
            f"Payment date: {payment.received_at.date().isoformat()}",
            f"Amount received: KES {receipt.amount}",
            f"Balance after payment: KES {receipt.balance_after}",
        ]
    )


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
    agencies = []
    if profile is None:
        name = user.get_full_name() or user.get_username()
        phone = None
        branches = Branch.objects.none()
        role_names = set(user.groups.values_list("name", flat=True))
    else:
        name = profile.name
        phone = profile.phone or None
        branches = profile.accessible_branches()
        memberships = profile.agency_memberships.filter(
            agency__is_active=True,
            is_active=True,
        ).select_related("agency").prefetch_related("roles")
        role_names = (
            set()
            if profile.agency_memberships.exists()
            else set(user.groups.values_list("name", flat=True))
        )
        for membership in memberships:
            membership_roles = sorted(
                membership.roles.values_list("name", flat=True)
            )
            role_names.update(membership_roles)
            agencies.append(
                {
                    "id": membership.agency_id,
                    "name": membership.agency.name,
                    "slug": membership.agency.slug,
                    "roles": membership_roles,
                    "branches": list(
                        branches.filter(agency_id=membership.agency_id)
                        .order_by("name")
                        .values("id", "name", "town")
                    ),
                }
            )
    return {
        "id": user.pk,
        "name": name,
        "email": user.email or None,
        "phone": phone,
        "roles": sorted(role_names),
        "branches": list(branches.order_by("name").values("id", "name", "town")),
        "agencies": agencies,
    }


@require_POST
def register_landlord_portal(request):
    return _problem(
        403,
        "Registration unavailable",
        "Landlord accounts are provisioned by their agency.",
        "landlord-registration-disabled",
    )


@require_POST
def register_tenant_portal(request):
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    name = body.get("name")
    email = body.get("email")
    phone = body.get("phone")
    password = body.get("password")
    if not all(isinstance(value, str) for value in (name, email, phone, password)):
        return _problem(400, "Invalid request", "name, email, phone, and password are required.", "invalid-request")
    name = name.strip()
    email = email.strip().lower()
    if not name or len(name) > 255:
        return _problem(400, "Invalid name", "Enter a valid full name.", "invalid-name")
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
    if UserProfile.objects.filter(phone=phone).exists():
        return _problem(409, "Phone already in use", "An account already exists for this phone number.", "phone-exists")

    try:
        with transaction.atomic():
            role, _ = Group.objects.get_or_create(name=TENANT_PORTAL_ROLE)
            user = user_model.objects.create_user(username=email, email=email, password=password)
            user.groups.add(role)
            profile = UserProfile.objects.create(user=user, name=name, phone=phone)
            profile.full_clean()
        login(request, user, backend="users.authentication.EmailOrPhoneBackend")
    except (ValidationError, IntegrityError) as error:
        return _problem(409, "Account exists", str(error), "account-exists")

    _record_auth_event(request, "tenant_portal.registered", user)
    return JsonResponse(
        {"user": _user_payload(user), "status": "awaiting_agency_linkage"},
        status=201,
    )


@require_GET
@ensure_csrf_cookie
def csrf_token(request):
    return JsonResponse({"csrfToken": get_token(request)})


def _director_staff_scope(request, agency_id):
    if not request.user.is_authenticated:
        return None, _problem(
            401,
            "Authentication required",
            "Sign in as an agency Director to manage staff.",
            "authentication-required",
        )
    profile = getattr(request.user, "profile", None)
    if profile is None:
        return None, _problem(
            403,
            "Permission denied",
            "An active Director agency membership is required.",
            "director-membership-required",
        )
    membership = AgencyMembership.objects.filter(
        profile=profile,
        agency_id=agency_id,
        agency__is_active=True,
        is_active=True,
        roles__name=SUPER_ADMIN_ROLE,
    ).select_related("agency").first()
    if membership is None:
        return None, _problem(
            403,
            "Permission denied",
            "You cannot manage staff for this agency.",
            "director-membership-required",
        )
    return membership, None


def _staff_membership_payload(membership):
    user = membership.profile.user
    roles = sorted(
        membership.roles.filter(name__in=AGENCY_STAFF_ROLE_NAMES).values_list(
            "name", flat=True
        )
    )
    return {
        "membership_id": membership.pk,
        "user_id": user.pk,
        "name": membership.profile.name,
        "email": user.email,
        "phone": membership.profile.phone or None,
        "roles": roles,
        "branches": list(
            membership.branches.order_by("name").values("id", "name", "town")
        ),
        "active": membership.is_active,
        "account_active": user.is_active,
        "status": (
            "suspended"
            if not membership.is_active
            else "pending_activation"
            if not user.is_active
            else "active"
        ),
    }


def _issue_staff_activation(membership):
    code = f"{secrets.randbelow(1_000_000):06d}"
    StaffActivationChallenge.objects.filter(membership=membership).delete()
    challenge = StaffActivationChallenge.objects.create(
        membership=membership,
        code_hash=make_password(code),
        expires_at=timezone.now()
        + timedelta(seconds=settings.AUTH_DIRECTOR_ACTIVATION_TTL_SECONDS),
    )
    return challenge, code


def _issue_landlord_activation(landlord):
    code = f"{secrets.randbelow(1_000_000):06d}"
    LandlordActivationChallenge.objects.filter(landlord=landlord).delete()
    challenge = LandlordActivationChallenge.objects.create(
        landlord=landlord,
        code_hash=make_password(code),
        expires_at=timezone.now()
        + timedelta(seconds=settings.AUTH_DIRECTOR_ACTIVATION_TTL_SECONDS),
    )
    return challenge, code


@require_http_methods(["GET", "POST"])
def director_staff(request):
    if request.method == "GET":
        try:
            agency_id = int(request.GET.get("agency_id", ""))
        except (TypeError, ValueError):
            return _problem(400, "Invalid agency", "A valid agency_id is required.", "invalid-agency")
        director_membership, error = _director_staff_scope(request, agency_id)
        if error:
            return error
        branches = list(
            Branch.objects.filter(agency=director_membership.agency)
            .order_by("name")
            .values("id", "name", "town")
        )
        memberships = (
            AgencyMembership.objects.filter(
                agency=director_membership.agency,
                roles__name__in=AGENCY_STAFF_ROLE_NAMES,
            )
            .select_related("profile__user")
            .prefetch_related("roles", "branches")
            .distinct()
            .order_by("profile__name", "profile__user__email")
        )
        return JsonResponse(
            {
                "agency": {
                    "id": director_membership.agency_id,
                    "name": director_membership.agency.name,
                },
                "branches": branches,
                "roles": list(AGENCY_STAFF_ROLE_NAMES),
                "staff": [_staff_membership_payload(item) for item in memberships],
            }
        )

    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    try:
        agency_id = int(body.get("agency_id"))
    except (TypeError, ValueError):
        return _problem(400, "Invalid agency", "A valid agency_id is required.", "invalid-agency")
    director_membership, error = _director_staff_scope(request, agency_id)
    if error:
        return error

    name = body.get("name")
    email = body.get("email")
    phone = body.get("phone")
    role_name = body.get("role")
    branch_ids = body.get("branch_ids")
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name.strip()) > 255
        or not isinstance(email, str)
        or not isinstance(phone, str)
        or role_name not in AGENCY_STAFF_ROLE_NAMES
        or not isinstance(branch_ids, list)
        or not branch_ids
        or any(not isinstance(item, int) or isinstance(item, bool) for item in branch_ids)
    ):
        return _problem(
            400,
            "Invalid staff details",
            "Name, email, phone, an eligible staff role, and at least one branch are required.",
            "invalid-staff",
        )
    email = email.strip().lower()
    try:
        validate_email(email)
        normalized_phone = _normalize_ke_phone(phone)
    except (ValidationError, ValueError) as validation_error:
        return _problem(400, "Invalid staff details", str(validation_error), "invalid-staff")

    branches = list(
        Branch.objects.filter(
            agency=director_membership.agency,
            pk__in=set(branch_ids),
        )
    )
    if len(branches) != len(set(branch_ids)):
        return _problem(
            400,
            "Invalid branch scope",
            "Every selected branch must belong to this agency.",
            "invalid-branch-scope",
        )

    user_model = get_user_model()
    role, _ = Group.objects.get_or_create(name=role_name)
    challenge = None
    activation_code = None
    try:
        with transaction.atomic():
            user = user_model.objects.select_for_update().filter(
                Q(username__iexact=email) | Q(email__iexact=email)
            ).first()
            if user is None:
                if UserProfile.objects.filter(phone__iexact=normalized_phone).exists():
                    return _problem(409, "Phone already in use", "An account already uses this phone.", "phone-in-use")
                user = user_model.objects.create_user(
                    username=email,
                    email=email,
                    password=None,
                    is_active=False,
                    is_staff=False,
                    is_superuser=False,
                )
                profile = UserProfile(user=user, name=name.strip(), phone=normalized_phone)
                profile.full_clean()
                profile.save()
            else:
                if (user.email or "").strip().lower() != email:
                    return _problem(
                        409,
                        "Account conflict",
                        "This email is already used as another account's username.",
                        "account-conflict",
                    )
                profile = getattr(user, "profile", None)
                if profile is None:
                    return _problem(409, "Account conflict", "This account needs administrator review.", "account-conflict")
                if profile.name != name.strip() or profile.phone != normalized_phone:
                    return _problem(
                        409,
                        "Account details differ",
                        "The verified name and phone must match the existing account.",
                        "account-details-mismatch",
                    )
                if not user.is_active and user.has_usable_password():
                    return _problem(
                        409,
                        "Account needs review",
                        "This inactive account already has a password and cannot be invited.",
                        "account-review-required",
                    )

            staff_membership, _ = AgencyMembership.objects.get_or_create(
                profile=profile,
                agency=director_membership.agency,
            )
            if staff_membership.roles.filter(name=role_name).exists():
                return _problem(
                    409,
                    "Staff role exists",
                    "This person already has that role in the agency. Edit their existing staff record.",
                    "staff-role-exists",
                )
            staff_membership.is_active = True
            staff_membership.save(update_fields=["is_active"])
            staff_membership.roles.add(role)
            staff_membership.branches.set(branches)
            if not user.is_active:
                challenge, activation_code = _issue_staff_activation(staff_membership)
            record_audit_event(
                request,
                "agency_staff.created",
                "AgencyMembership",
                new_values={
                    "user_id": user.pk,
                    "agency_id": director_membership.agency_id,
                    "role": role_name,
                    "branch_ids": sorted(branch_ids),
                    "activation_required": not user.is_active,
                },
            )
    except (IntegrityError, ValidationError) as validation_error:
        return _problem(400, "Invalid staff details", str(validation_error), "invalid-staff")

    if challenge and activation_code:
        try:
            _send_staff_activation_email(user, challenge.pk, activation_code)
        except (ImproperlyConfigured, RuntimeError, smtplib.SMTPException, OSError) as mail_error:
            challenge.delete()
            record_audit_event(
                request,
                "agency_staff.activation_email_failed",
                "AgencyMembership",
                new_values={
                    "user_id": user.pk,
                    "agency_id": director_membership.agency_id,
                    "error_type": type(mail_error).__name__,
                },
            )
            return JsonResponse(
                {
                    "detail": "Staff access was created, but the activation email failed. Use Resend activation to try again.",
                    "staff": _staff_membership_payload(staff_membership),
                },
                status=503,
            )
    return JsonResponse(
        {
            "staff": _staff_membership_payload(staff_membership),
            "activation_email_sent": bool(challenge),
        },
        status=201,
    )


@require_http_methods(["PATCH", "DELETE"])
def director_staff_detail(request, membership_id):
    try:
        staff_membership = (
            AgencyMembership.objects.select_related("agency", "profile__user")
            .filter(pk=membership_id, roles__name__in=AGENCY_STAFF_ROLE_NAMES)
            .distinct()
            .get()
        )
    except AgencyMembership.DoesNotExist:
        return _problem(404, "Staff not found", "This staff record is unavailable.", "staff-not-found")
    director_membership, error = _director_staff_scope(request, staff_membership.agency_id)
    if error:
        return error
    if staff_membership.agency_id != director_membership.agency_id:
        return _problem(404, "Staff not found", "This staff record is unavailable.", "staff-not-found")
    old_values = _staff_membership_payload(staff_membership)
    if request.method == "DELETE":
        if staff_membership.profile_id == request.user.profile.pk:
            return _problem(400, "Cannot suspend yourself", "Another Director must manage your access.", "self-suspension")
        staff_membership.is_active = False
        staff_membership.save(update_fields=["is_active"])
        StaffActivationChallenge.objects.filter(membership=staff_membership).delete()
        record_audit_event(
            request,
            "agency_staff.suspended",
            "AgencyMembership",
            old_values=old_values,
            new_values={"active": False},
        )
        return JsonResponse({"staff": _staff_membership_payload(staff_membership)})

    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    role_name = body.get("role")
    branch_ids = body.get("branch_ids")
    active = body.get("active", staff_membership.is_active)
    if (
        role_name not in AGENCY_STAFF_ROLE_NAMES
        or not isinstance(branch_ids, list)
        or not branch_ids
        or any(not isinstance(item, int) or isinstance(item, bool) for item in branch_ids)
        or not isinstance(active, bool)
    ):
        return _problem(
            400,
            "Invalid staff details",
            "Choose an eligible role, at least one branch, and a valid active state.",
            "invalid-staff",
        )
    branches = list(
        Branch.objects.filter(
            agency=staff_membership.agency,
            pk__in=set(branch_ids),
        )
    )
    if len(branches) != len(set(branch_ids)):
        return _problem(
            400,
            "Invalid branch scope",
            "Every selected branch must belong to this agency.",
            "invalid-branch-scope",
        )
    role, _ = Group.objects.get_or_create(name=role_name)
    with transaction.atomic():
        staff_membership = AgencyMembership.objects.select_for_update().get(
            pk=staff_membership.pk
        )
        staff_membership.roles.remove(*staff_membership.roles.filter(name__in=AGENCY_STAFF_ROLE_NAMES))
        staff_membership.roles.add(role)
        staff_membership.branches.set(branches)
        staff_membership.is_active = active
        staff_membership.save(update_fields=["is_active"])
        if not active:
            StaffActivationChallenge.objects.filter(membership=staff_membership).delete()
        record_audit_event(
            request,
            "agency_staff.updated",
            "AgencyMembership",
            old_values=old_values,
            new_values={
                "role": role_name,
                "branch_ids": sorted(branch_ids),
                "active": active,
            },
        )
    staff_membership = AgencyMembership.objects.select_related(
        "agency", "profile__user"
    ).prefetch_related("roles", "branches").get(pk=staff_membership.pk)
    return JsonResponse({"staff": _staff_membership_payload(staff_membership)})


@require_POST
def resend_staff_activation(request, membership_id):
    try:
        staff_membership = (
            AgencyMembership.objects.select_related("agency", "profile__user")
            .filter(pk=membership_id, roles__name__in=AGENCY_STAFF_ROLE_NAMES)
            .distinct()
            .get()
        )
    except AgencyMembership.DoesNotExist:
        return _problem(404, "Staff not found", "This staff record is unavailable.", "staff-not-found")
    director_membership, error = _director_staff_scope(request, staff_membership.agency_id)
    if error:
        return error
    if staff_membership.agency_id != director_membership.agency_id:
        return _problem(404, "Staff not found", "This staff record is unavailable.", "staff-not-found")
    user = staff_membership.profile.user
    if user.is_active:
        return _problem(409, "Already activated", "This staff account is already active.", "already-activated")
    if not staff_membership.is_active:
        return _problem(409, "Staff suspended", "Reactivate this staff membership before sending an invitation.", "staff-suspended")
    challenge, code = _issue_staff_activation(staff_membership)
    try:
        _send_staff_activation_email(user, challenge.pk, code)
    except (ImproperlyConfigured, RuntimeError, smtplib.SMTPException, OSError) as error:
        challenge.delete()
        record_audit_event(
            request,
            "agency_staff.activation_email_failed",
            "AgencyMembership",
            new_values={
                "user_id": user.pk,
                "agency_id": staff_membership.agency_id,
                "error_type": type(error).__name__,
            },
        )
        return _problem(503, "Email unavailable", "The activation email could not be sent.", "activation-email-failed")
    record_audit_event(
        request,
        "agency_staff.activation_resent",
        "AgencyMembership",
        new_values={"user_id": user.pk, "agency_id": staff_membership.agency_id},
    )
    return JsonResponse({"activation_email_sent": True})


@require_POST
def activate_director(request):
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    challenge_id = body.get("challenge_id")
    code = body.get("code")
    password = body.get("password")
    if (
        not isinstance(challenge_id, str)
        or not isinstance(code, str)
        or not code.isascii()
        or not re.fullmatch(r"\d{6}", code)
        or not isinstance(password, str)
    ):
        return _problem(
            400,
            "Invalid request",
            "A challenge ID, six-digit code, and password are required.",
            "invalid-request",
        )

    with transaction.atomic():
        try:
            challenge = DirectorActivationChallenge.objects.select_for_update().select_related(
                "user"
            ).get(pk=challenge_id, user__is_active=False)
        except (DirectorActivationChallenge.DoesNotExist, ValueError):
            return _problem(400, "Invalid activation", "The activation code is invalid.", "invalid-activation")
        user = challenge.user
        if (
            challenge.is_expired()
            or challenge.attempts >= settings.AUTH_MFA_MAX_ATTEMPTS
        ):
            challenge.delete()
            return _problem(
                400,
                "Activation expired",
                "This activation code has expired. Contact your administrator for a new invitation.",
                "activation-expired",
            )
        if not check_password(code, challenge.code_hash):
            challenge.attempts += 1
            if challenge.attempts >= settings.AUTH_MFA_MAX_ATTEMPTS:
                challenge.delete()
            else:
                challenge.save(update_fields=["attempts"])
            return _problem(400, "Invalid code", "The activation code is invalid.", "invalid-activation")
        try:
            password_validation.validate_password(password, user=user)
        except ValidationError as error:
            return _problem(
                400,
                "Invalid password",
                " ".join(error.messages),
                "invalid-password",
            )
        user.set_password(password)
        user.is_active = True
        user.save(update_fields=["password", "is_active"])
        challenge.delete()

    _record_auth_event(request, "director.activation_succeeded", user)
    return JsonResponse({"status": "activated"})


@require_POST
def activate_staff(request):
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    challenge_id = body.get("challenge_id")
    code = body.get("code")
    password = body.get("password")
    if (
        not isinstance(challenge_id, str)
        or not isinstance(code, str)
        or not code.isascii()
        or not re.fullmatch(r"\d{6}", code)
        or not isinstance(password, str)
    ):
        return _problem(
            400,
            "Invalid request",
            "A challenge ID, six-digit code, and password are required.",
            "invalid-request",
        )

    with transaction.atomic():
        try:
            challenge = (
                StaffActivationChallenge.objects.select_for_update()
                .select_related("membership__profile__user")
                .get(
                    pk=challenge_id,
                    membership__is_active=True,
                    membership__agency__is_active=True,
                    membership__profile__user__is_active=False,
                )
            )
        except (StaffActivationChallenge.DoesNotExist, ValueError):
            return _problem(
                400,
                "Invalid activation",
                "The activation code is invalid.",
                "invalid-activation",
            )
        if not challenge.membership.roles.filter(
            name__in=AGENCY_STAFF_ROLE_NAMES
        ).exists():
            return _problem(
                400,
                "Invalid activation",
                "The activation code is invalid.",
                "invalid-activation",
            )
        user = challenge.membership.profile.user
        if (
            challenge.is_expired()
            or challenge.attempts >= settings.AUTH_MFA_MAX_ATTEMPTS
        ):
            challenge.delete()
            return _problem(
                400,
                "Activation expired",
                "This activation code has expired. Ask your agency Director to resend the invitation.",
                "activation-expired",
            )
        if not check_password(code, challenge.code_hash):
            challenge.attempts += 1
            if challenge.attempts >= settings.AUTH_MFA_MAX_ATTEMPTS:
                challenge.delete()
            else:
                challenge.save(update_fields=["attempts"])
            return _problem(400, "Invalid code", "The activation code is invalid.", "invalid-activation")
        try:
            password_validation.validate_password(password, user=user)
        except ValidationError as validation_error:
            return _problem(
                400,
                "Invalid password",
                " ".join(validation_error.messages),
                "invalid-password",
            )
        user.set_password(password)
        user.is_active = True
        user.save(update_fields=["password", "is_active"])
        StaffActivationChallenge.objects.filter(
            membership__profile__user=user
        ).delete()

    _record_auth_event(request, "agency_staff.activation_succeeded", user)
    return JsonResponse({"status": "activated"})


def _issue_totp_challenge(request, user):
    profile = getattr(user, "profile", None)
    if profile is None:
        return _problem(
            403,
            "Authenticator unavailable",
            "A user profile is required for authenticator verification.",
            "mfa-profile-required",
        )
    setup_required = not profile.totp_enabled or not profile.totp_secret_encrypted
    secret = generate_secret() if setup_required else None
    TOTPLoginChallenge.objects.filter(user=user).delete()
    challenge = TOTPLoginChallenge.objects.create(
        user=user,
        setup_secret_encrypted=encrypt_secret(secret) if secret else None,
        expires_at=timezone.now()
        + timedelta(seconds=settings.AUTH_MFA_CHALLENGE_TTL_SECONDS),
    )
    result = {
        "mfa_required": True,
        "mfa_method": "totp",
        "setup_required": setup_required,
        "challenge_id": str(challenge.pk),
    }
    if secret:
        result["secret"] = secret
        result["provisioning_uri"] = provisioning_uri(secret, user.email or user.username)
    return JsonResponse(result, status=202)


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
        _record_auth_event(request, "login.totp_challenge_issued", user)
        return _issue_totp_challenge(request, user)

    login(request, user, backend="users.authentication.EmailOrPhoneBackend")
    _record_auth_event(request, "login.succeeded", user)
    return JsonResponse({"user": _user_payload(user)})


@require_POST
def verify_totp_challenge(request):
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    challenge_id = body.get("challenge_id")
    code = body.get("code")
    normalized_code = (
        re.sub(r"[-\s]", "", code).upper() if isinstance(code, str) else ""
    )
    is_totp_code = (
        isinstance(code, str)
        and len(code) == 6
        and code.isascii()
        and code.isdigit()
    )
    is_recovery_code = bool(re.fullmatch(r"[A-F0-9]{8}", normalized_code))
    if (
        not isinstance(challenge_id, str)
        or not isinstance(code, str)
        or not (is_totp_code or is_recovery_code)
    ):
        return _problem(
            400,
            "Invalid request",
            "A challenge ID and authenticator code are required.",
            "invalid-request",
        )

    response = None
    user = None
    recovery_codes = []
    with transaction.atomic():
        try:
            challenge = TOTPLoginChallenge.objects.select_for_update().select_related(
                "user"
            ).get(pk=challenge_id, user__is_active=True)
        except (TOTPLoginChallenge.DoesNotExist, ValueError):
            _record_auth_event(request, "login.totp_failed")
            response = _problem(401, "Invalid challenge", "The challenge is invalid.", "invalid-mfa")
        else:
            user = challenge.user
            profile = UserProfile.objects.select_for_update().get(user_id=user.pk)
            if (
                challenge.is_expired()
                or challenge.attempts >= settings.AUTH_MFA_MAX_ATTEMPTS
            ):
                challenge.delete()
                _record_auth_event(request, "login.totp_failed", user)
                response = _problem(401, "Expired challenge", "Sign in again to continue.", "expired-mfa")
            else:
                is_setup = challenge.setup_secret_encrypted is not None
                secret = (
                    decrypt_secret(challenge.setup_secret_encrypted)
                    if is_setup
                    else decrypt_secret(profile.totp_secret_encrypted)
                    if profile.totp_secret_encrypted
                    else None
                )
                code_counter = (
                    matching_counter(secret, code)
                    if secret and is_totp_code
                    else None
                )
                valid_totp = (
                    code_counter is not None
                    and (
                        profile.totp_last_used_counter is None
                        or code_counter > profile.totp_last_used_counter
                    )
                )
                recovery_hashes = list(profile.totp_recovery_codes)
                matched_recovery = None
                if not valid_totp and not is_setup and is_recovery_code:
                    matched_recovery = next(
                        (
                            stored
                            for stored in recovery_hashes
                            if check_password(normalized_code, stored)
                        ),
                        None,
                    )
                if not valid_totp and matched_recovery is None:
                    challenge.attempts += 1
                    if challenge.attempts >= settings.AUTH_MFA_MAX_ATTEMPTS:
                        challenge.delete()
                    else:
                        challenge.save(update_fields=["attempts"])
                    _record_auth_event(request, "login.totp_failed", user)
                    response = _problem(401, "Invalid code", "The authenticator code is invalid.", "invalid-mfa")
                elif is_setup:
                    profile.totp_secret_encrypted = challenge.setup_secret_encrypted
                    profile.totp_enabled = True
                    profile.totp_last_used_counter = code_counter
                    recovery_codes = [secrets.token_hex(4).upper() for _ in range(10)]
                    profile.totp_recovery_codes = [make_password(item) for item in recovery_codes]
                    profile.save(
                        update_fields=[
                            "totp_secret_encrypted",
                            "totp_enabled",
                            "totp_last_used_counter",
                            "totp_recovery_codes",
                        ]
                    )
                    challenge.delete()
                else:
                    update_fields = []
                    if valid_totp:
                        profile.totp_last_used_counter = code_counter
                        update_fields.append("totp_last_used_counter")
                    if matched_recovery is not None:
                        recovery_hashes.remove(matched_recovery)
                        profile.totp_recovery_codes = recovery_hashes
                        update_fields.append("totp_recovery_codes")
                    if update_fields:
                        profile.save(update_fields=update_fields)
                    challenge.delete()

    if response is not None:
        return response
    login(request, user, backend="users.authentication.EmailOrPhoneBackend")
    _record_auth_event(request, "login.succeeded", user)
    result = {"user": _user_payload(user)}
    if recovery_codes:
        result["recovery_codes"] = recovery_codes
    return JsonResponse(result)


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


@require_POST
def request_password_reset(request):
    body = _json_body(request)
    if body is None or not isinstance(body.get("email"), str):
        return _problem(400, "Invalid request", "A valid email address is required.", "invalid-request")
    email = body["email"].strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        return _problem(400, "Invalid email", "Enter a valid email address.", "invalid-email")

    if not settings.PUBLIC_SITE_URL or not email_delivery_is_configured():
        return _problem(503, "Recovery unavailable", "Password recovery is not configured.", "recovery-unavailable")

    user = get_user_model().objects.filter(email__iexact=email, is_active=True).first()
    if user is not None:
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        reset_url = f"{settings.PUBLIC_SITE_URL}/reset-password?uid={uid}&token={token}"
        try:
            _send_password_reset_email(user, reset_url)
        except (ImproperlyConfigured, RuntimeError) as error:
            logger.error("Could not deliver a password reset email (%s).", type(error).__name__)

    return JsonResponse(
        {
            "success": True,
            "message": "If an active account uses that email, a password reset link will be sent.",
        }
    )


@require_POST
def confirm_password_reset(request):
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    uid = body.get("uid")
    token = body.get("token")
    password = body.get("password")
    if not all(isinstance(value, str) and value for value in (uid, token, password)):
        return _problem(400, "Invalid request", "A reset link and new password are required.", "invalid-request")

    try:
        user_id = urlsafe_base64_decode(uid).decode()
        user = get_user_model()._default_manager.get(pk=user_id, is_active=True)
    except (ValueError, UnicodeDecodeError, get_user_model().DoesNotExist):
        user = None
    if user is None or not default_token_generator.check_token(user, token):
        return _problem(400, "Invalid reset link", "This password reset link is invalid or has expired.", "invalid-reset-link")
    try:
        password_validation.validate_password(password, user)
    except ValidationError as error:
        return _problem(400, "Invalid password", "; ".join(error.messages), "invalid-password")

    user.set_password(password)
    user.save(update_fields=["password"])
    record_audit_event(request, "password_reset.completed", "User", actor=user)
    return JsonResponse({"success": True, "message": "Password reset successfully."})


@require_GET
def current_user(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    return JsonResponse({"user": _user_payload(request.user)})


def _portal_profile_access_error(request, role):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    role_name = LANDLORD_PORTAL_ROLE if role == "landlord" else CARETAKER_ROLE
    if not _has_role(request.user, role_name):
        return _problem(403, "Permission denied", "This portal profile is unavailable.", "portal-profile-denied")
    if not hasattr(request.user, "profile"):
        return _problem(404, "Profile not found", "The user profile is unavailable.", "profile-not-found")
    return None


@require_http_methods(["GET", "PATCH"])
def portal_profile(request, role):
    error = _portal_profile_access_error(request, role)
    if error:
        return error

    profile = request.user.profile
    if request.method == "GET":
        payload = {
            "full_name": profile.name,
            "email": request.user.email,
            "phone": profile.phone,
        }
        if role == "caretaker":
            payload.update(
                role="caretaker",
                status="active" if request.user.is_active else "inactive",
                created_at=request.user.date_joined.isoformat(),
            )
        return JsonResponse({"profile": payload})

    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    full_name = body.get("fullName")
    phone = body.get("phone")
    email = body.get("email", request.user.email)
    if (
        not isinstance(full_name, str)
        or not full_name.strip()
        or len(full_name.strip()) > 120
        or not isinstance(phone, str)
        or not isinstance(email, str)
        or not email.strip()
    ):
        return _problem(
            400,
            "Invalid profile",
            "Name, email, and phone are required; the name must be 120 characters or fewer.",
            "invalid-profile",
        )
    try:
        validate_email(email.strip())
        normalized_phone = _normalize_ke_phone(phone)
    except (ValidationError, ValueError) as exc:
        return _problem(400, "Invalid profile", str(exc), "invalid-profile")

    user_model = get_user_model()
    normalized_email = email.strip().lower()
    if user_model.objects.filter(email__iexact=normalized_email).exclude(pk=request.user.pk).exists():
        return _problem(409, "Email already in use", "Choose another email address.", "email-already-in-use")
    if user_model.objects.filter(username__iexact=normalized_email).exclude(pk=request.user.pk).exists():
        return _problem(409, "Email already in use", "Choose another email address.", "email-already-in-use")

    old_email = request.user.email
    profile.name = full_name.strip()
    profile.phone = normalized_phone
    request.user.email = normalized_email
    if request.user.username.lower() == old_email.lower():
        request.user.username = normalized_email
    try:
        with transaction.atomic():
            profile.full_clean()
            profile.save(update_fields=["name", "phone"])
            request.user.save(update_fields=["email", "username"])
    except (ValidationError, IntegrityError) as exc:
        return _problem(400, "Invalid profile", str(exc), "invalid-profile")
    record_audit_event(request, f"{role}_profile.updated", "UserProfile")
    return JsonResponse({"success": True, "message": "Profile updated successfully."})


@require_http_methods(["GET", "POST"])
def portal_onboarding(request, role):
    error = _portal_profile_access_error(request, role)
    if error:
        return error

    profile = request.user.profile
    if request.method == "GET":
        payload = {
            "fullName": profile.name,
            "tutorialCompleted": profile.tutorial_completed,
        }
        if role == "landlord":
            property_name = (
                _landlord_portal_properties(request.user)
                .order_by("pk")
                .values_list("name", flat=True)
                .first()
            )
            payload.update(
                propertyName=property_name or "",
                onboardingCompleted=profile.onboarding_completed,
            )
        return JsonResponse(payload)

    body = _json_body(request)
    if body is None:
        body = {}
    profile.onboarding_completed = True
    profile.tutorial_completed = body.get("tutorialCompleted") is not False
    profile.save(update_fields=["onboarding_completed", "tutorial_completed"])
    record_audit_event(
        request,
        f"{role}_onboarding.completed",
        "UserProfile",
        new_values={
            "onboarding_completed": profile.onboarding_completed,
            "tutorial_completed": profile.tutorial_completed,
        },
    )
    return JsonResponse({"success": True})


def _tenant_portal_error(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not _has_role(request.user, TENANT_PORTAL_ROLE):
        return _problem(403, "Permission denied", "A tenant portal account is required.", "tenant-portal-required")
    return None


@require_http_methods(["GET", "PATCH"])
def tenant_portal_profile(request):
    error = _tenant_portal_error(request)
    if error:
        return error
    tenant = Tenant.objects.filter(portal_user=request.user).first()
    if tenant is None:
        return _problem(404, "Tenant not found", "The tenant profile is unavailable.", "tenant-not-found")
    tenancy = (
        Tenancy.objects.filter(
            tenant=tenant,
            status=Tenancy.STATUS_ACTIVE,
            move_out_settlement__isnull=True,
        )
        .select_related("unit__floor", "unit__property")
        .order_by("-start_date", "-id")
        .first()
    )
    settlement = MoveInSettlement.objects.filter(tenant=tenant).order_by("-created_at", "-id").first()
    if request.method == "GET":
        deposit = None
        if tenancy:
            deposit = DepositAccount.objects.filter(tenancy=tenancy).first()
        configured_deposit = (
            settlement.deposit_amount
            if settlement
            else (deposit.target_amount if deposit else Decimal("0.00"))
        )
        water_deposit = settlement.water_deposit_amount if settlement else Decimal("0.00")
        unit = tenancy.unit if tenancy else None
        return JsonResponse(
            {
                "profile": {
                    "fullName": tenant.name,
                    "email": request.user.email or tenant.email,
                    "phone": tenant.phone,
                    "moveInDate": tenancy.start_date.isoformat() if tenancy else None,
                    "securityDeposit": float(configured_deposit),
                    "waterDeposit": float(water_deposit),
                    "deposit": float(configured_deposit),
                    "unitCode": unit.code if unit else None,
                    "floor": unit.floor.name if unit and unit.floor else None,
                }
            }
        )

    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    full_name = body.get("fullName")
    email = body.get("email")
    phone = body.get("phone")
    if not all(isinstance(value, str) and value.strip() for value in (full_name, email, phone)):
        return _problem(400, "Invalid profile", "Name, email, and phone are required.", "invalid-profile")
    full_name = full_name.strip()
    email = email.strip().lower()
    if len(full_name) > 120:
        return _problem(400, "Invalid profile", "The name must be 120 characters or fewer.", "invalid-profile")
    try:
        validate_email(email)
        normalized_phone = _normalize_ke_phone(phone)
    except (ValidationError, ValueError) as exc:
        return _problem(400, "Invalid profile", str(exc), "invalid-profile")
    user_model = get_user_model()
    if user_model.objects.filter(
        Q(email__iexact=email) | Q(username__iexact=email)
    ).exclude(pk=request.user.pk).exists():
        return _problem(409, "Email already in use", "Choose another email address.", "email-already-in-use")
    if Tenant.objects.filter(email__iexact=email).exclude(pk=tenant.pk).exists():
        return _problem(409, "Email already in use", "Choose another email address.", "email-already-in-use")
    old_email = request.user.email
    profile, _ = UserProfile.objects.get_or_create(
        user=request.user,
        defaults={"name": tenant.name, "phone": tenant.phone},
    )
    try:
        with transaction.atomic():
            tenant.name = full_name
            tenant.email = email
            tenant.phone = normalized_phone
            tenant.full_clean()
            tenant.save(update_fields=["name", "email", "phone", "updated_at"])
            profile.name = full_name
            profile.phone = normalized_phone
            profile.full_clean()
            profile.save(update_fields=["name", "phone"])
            request.user.email = email
            if request.user.username.casefold() == old_email.casefold():
                request.user.username = email
            request.user.save(update_fields=["email", "username"])
    except (ValidationError, IntegrityError) as exc:
        detail = "; ".join(exc.messages) if isinstance(exc, ValidationError) else "The profile conflicts with an existing account."
        return _problem(400, "Invalid profile", detail, "invalid-profile")
    record_audit_event(request, "tenant_profile.updated", "Tenant")
    return JsonResponse({"success": True, "message": "Profile updated successfully."})


@require_http_methods(["GET", "POST"])
def tenant_portal_onboarding(request):
    error = _tenant_portal_error(request)
    if error:
        return error
    profile = UserProfile.objects.filter(user=request.user).first()
    if profile is None:
        return _problem(404, "Profile not found", "The tenant profile is unavailable.", "profile-not-found")
    tenant = Tenant.objects.filter(portal_user=request.user).first()
    if request.method == "GET":
        tenancy = (
            Tenancy.objects.filter(
                tenant=tenant,
                status=Tenancy.STATUS_ACTIVE,
                move_out_settlement__isnull=True,
            )
            .select_related("unit__property")
            .order_by("-start_date", "-id")
            .first()
            if tenant
            else None
        )
        settlement = (
            MoveInSettlement.objects.filter(tenant=tenant)
            .order_by("-created_at", "-id")
            .first()
            if tenant
            else None
        )
        billing_settings = (
            PropertyBillingSettings.objects.filter(property=tenancy.unit.property).first()
            if tenancy
            else None
        )
        move_in_settlement = None
        if settlement:
            utility_deposits = settlement.utility_deposits
            if not utility_deposits:
                utility_deposits = [
                    {"name": name, "amount": str(amount)}
                    for name, amount in (
                        ("Water deposit", settlement.water_deposit_amount),
                        ("Utility deposits", settlement.utility_deposit_amount),
                    )
                    if amount > 0
                ]
            move_in_settlement = {
                "status": settlement.status,
                "total_required": float(settlement.total_required),
                "total_paid": float(settlement.total_paid),
                "outstanding_balance": float(settlement.outstanding_balance),
                "deposit_amount": float(settlement.deposit_amount),
                "rent_amount": float(settlement.rent_amount),
                "first_month_rent_amount": float(
                    _move_in_first_month_amount(
                        settlement.rent_amount,
                        settlement.expected_move_in_date,
                    )
                ),
                "utility_deposits": utility_deposits,
                "garbage_amount": float(settlement.garbage_amount),
                "water_deposit_amount": float(settlement.water_deposit_amount),
            }
        return JsonResponse(
            {
                "fullName": profile.name,
                "tutorialCompleted": profile.tutorial_completed,
                "isMigrated": bool(tenancy and tenancy.unit.is_migrated),
                "moveInSettlement": move_in_settlement,
                "transitionSettings": (
                    {"first_online_billing_month": billing_settings.first_online_billing_month}
                    if billing_settings
                    else None
                ),
            }
        )

    body = _json_body(request) or {}
    profile.tutorial_completed = body.get("tutorialCompleted") is not False
    profile.save(update_fields=["tutorial_completed"])
    record_audit_event(
        request,
        "tenant_onboarding.completed",
        "UserProfile",
        new_values={"tutorial_completed": profile.tutorial_completed},
    )
    return JsonResponse({"success": True})


@require_GET
def tenant_unit_status(request):
    error = _tenant_portal_error(request)
    if error:
        return error
    tenant = Tenant.objects.filter(portal_user=request.user).first()
    tenancy = (
        Tenancy.objects.filter(
            tenant=tenant,
            status=Tenancy.STATUS_ACTIVE,
            move_out_settlement__isnull=True,
        )
        .select_related("unit")
        .order_by("-start_date", "-id")
        .first()
        if tenant
        else None
    )
    if tenancy is None and tenant:
        tenancy = (
            Tenancy.objects.filter(tenant=tenant)
            .select_related("unit")
            .order_by("-start_date", "-id")
            .first()
        )
    if tenancy is None:
        return JsonResponse({"status": "no_unit", "unitCode": None})
    status = {
        Unit.STATUS_VACANT: "vacant",
        Unit.STATUS_BOOKED: "booked",
        Unit.STATUS_OCCUPIED: "occupied",
        Unit.STATUS_NOTICE_GIVEN: "notice_given",
        Unit.STATUS_DEPOSIT_PENDING: "deposit_pending",
        Unit.STATUS_MAINTENANCE: "maintenance",
        Unit.STATUS_ARCHIVED: "archived",
        Unit.STATUS_DRAFT: "draft",
    }.get(tenancy.unit.status, "unknown")
    return JsonResponse(
        {
            "status": status,
            "unitCode": tenancy.unit.code,
            "assignmentStatus": tenancy.status,
            "moveInDate": tenancy.start_date.isoformat(),
        }
    )


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


def _landlord_portal_account_payload(landlord):
    user = landlord.portal_user
    return {
        "id": landlord.pk,
        "name": landlord.name,
        "email": landlord.email,
        "phone": landlord.phone,
        "branch": {
            "id": landlord.branch_id,
            "name": landlord.branch.name,
            "town": landlord.branch.town,
        },
        "account_status": (
            "not_invited"
            if user is None
            else "active"
            if user.is_active
            else "pending_activation"
        ),
    }


@require_http_methods(["GET", "POST"])
def property_officer_landlords(request):
    if request.method == "GET":
        try:
            agency_id = int(request.GET.get("agency_id", ""))
        except (TypeError, ValueError):
            return _problem(400, "Invalid agency", "A valid agency_id is required.", "invalid-agency")
        membership, error = _property_officer_membership(
            request.user,
            agency_id=agency_id,
        )
        if error:
            return error
        landlords = (
            Landlord.objects.filter(
                branch__in=membership.branches.all(),
            )
            .select_related("branch", "portal_user")
            .order_by("name", "id")
        )
        properties = (
            Property.objects.filter(branch__in=membership.branches.all())
            .select_related("branch", "landlord")
            .order_by("branch__name", "name", "id")
        )
        return JsonResponse({
            "agency": {"id": membership.agency_id, "name": membership.agency.name},
            "branches": list(
                membership.branches.order_by("name").values("id", "name", "town")
            ),
            "landlords": [_landlord_portal_account_payload(item) for item in landlords],
            "properties": [
                {
                    "id": item.pk,
                    "name": item.name,
                    "branch_id": item.branch_id,
                    "branch_name": item.branch.name,
                    "landlord_id": item.landlord_id,
                    "landlord_name": item.landlord.name if item.landlord_id else None,
                }
                for item in properties
            ],
        })

    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in as a Property Officer first.", "authentication-required")
    if not hasattr(request.user, "profile"):
        return _problem(403, "Permission denied", "A Property Officer profile is required.", "property-officer-required")
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")

    landlord_id = body.get("landlord_id")
    if landlord_id is not None:
        try:
            landlord_id = int(landlord_id)
        except (TypeError, ValueError):
            return _problem(400, "Invalid landlord", "landlord_id must be an integer.", "invalid-landlord")
        landlord = (
            Landlord.objects.filter(
                pk=landlord_id,
                branch__in=request.user.profile.accessible_branches(
                    role_names=("Property Officer / Agent",)
                ),
            )
            .select_related("branch", "portal_user")
            .first()
        )
        if landlord is None:
            return _problem(404, "Landlord not found", "This landlord is outside your branch scope.", "landlord-not-found")
        membership, error = _property_officer_membership(
            request.user,
            branch_id=landlord.branch_id,
        )
        if error:
            return error
        email = landlord.email.strip().lower()
        name = landlord.name
        phone = landlord.phone
        if not email:
            return _problem(400, "Email required", "Add an email address to the landlord record before sending an invitation.", "landlord-email-required")
    else:
        required = ("name", "email", "phone", "id_or_registration_number")
        if any(not isinstance(body.get(field), str) or not body[field].strip() for field in required):
            return _problem(
                400,
                "Invalid landlord",
                "Branch, name, email, phone, and identity or registration number are required.",
                "invalid-landlord",
            )
        try:
            raw_branch_id = body.get("branch_id")
            if isinstance(raw_branch_id, bool):
                raise ValueError
            branch_id = int(raw_branch_id)
        except (TypeError, ValueError):
            return _problem(400, "Invalid branch", "branch_id must be an integer.", "invalid-branch")
        membership, error = _property_officer_membership(
            request.user,
            branch_id=branch_id,
        )
        if error:
            return error
        name = body["name"].strip()
        email = body["email"].strip().lower()
        phone = body["phone"].strip()
        try:
            validate_email(email)
            phone = _normalize_ke_phone(phone)
        except (ValidationError, ValueError) as validation_error:
            return _problem(400, "Invalid landlord", str(validation_error), "invalid-landlord")

    raw_property_id = body.get("property_id")
    if raw_property_id in (None, ""):
        property_id = None
    else:
        try:
            if isinstance(raw_property_id, bool):
                raise ValueError
            property_id = int(raw_property_id)
        except (TypeError, ValueError):
            return _problem(400, "Invalid property", "property_id must be an integer.", "invalid-property")

    user_model = get_user_model()
    if user_model.objects.filter(Q(username__iexact=email) | Q(email__iexact=email)).exists():
        return _problem(409, "Account exists", "This email already belongs to an account.", "account-exists")
    if UserProfile.objects.filter(phone__iexact=phone).exists():
        return _problem(409, "Phone already in use", "An account already uses this phone number.", "phone-in-use")

    challenge = None
    activation_code = None
    try:
        with transaction.atomic():
            property_obj = None
            if property_id is not None:
                property_obj = Property.objects.select_for_update().filter(
                    pk=property_id,
                    branch_id=landlord.branch_id if landlord_id is not None else branch_id,
                    branch__in=membership.branches.all(),
                ).first()
                if property_obj is None:
                    return _problem(
                        404,
                        "Property not found",
                        "The property is outside your branch scope.",
                        "property-not-found",
                    )

            if landlord_id is None:
                landlord = Landlord.objects.select_for_update().filter(
                    branch_id=branch_id,
                    id_or_registration_number=body["id_or_registration_number"].strip(),
                ).first()
                if (
                    property_obj
                    and property_obj.landlord_id
                    and (landlord is None or property_obj.landlord_id != landlord.pk)
                ):
                    return _problem(
                        409,
                        "Property already assigned",
                        "Choose a property that is not assigned to another landlord.",
                        "property-already-assigned",
                    )
                if landlord is None:
                    landlord = Landlord(
                        branch_id=branch_id,
                        name=name,
                        id_or_registration_number=body["id_or_registration_number"].strip(),
                        kra_pin=(body.get("kra_pin") or "").strip(),
                        phone=phone,
                        email=email,
                    )
                    landlord.full_clean()
                    landlord.save()
                else:
                    if landlord.portal_user_id:
                        return _problem(409, "Account exists", "A portal account is already linked to this landlord.", "landlord-account-exists")
                    if (
                        landlord.name != name
                        or landlord.phone != phone
                        or (landlord.email and landlord.email.strip().lower() != email)
                    ):
                        return _problem(409, "Landlord record differs", "The identity already exists in this branch; match its name, phone, and email to invite it.", "landlord-record-conflict")
                    if not landlord.email:
                        landlord.email = email
                        landlord.save(update_fields=["email", "updated_at"])
            else:
                if landlord.portal_user_id:
                    return _problem(409, "Account exists", "A portal account is already linked to this landlord.", "landlord-account-exists")
                if property_obj and property_obj.landlord_id not in (None, landlord.pk):
                    return _problem(
                        409,
                        "Property already assigned",
                        "Choose a property that is not assigned to another landlord.",
                        "property-already-assigned",
                    )

            if property_obj and property_obj.landlord_id != landlord.pk:
                property_obj.landlord = landlord
                property_obj.save(update_fields=["landlord"])
                record_audit_event(
                    request,
                    "property.landlord_assigned",
                    "Property",
                    new_values={
                        "property_id": property_obj.pk,
                        "landlord_id": landlord.pk,
                        "branch_id": property_obj.branch_id,
                    },
                )

            role, _ = Group.objects.get_or_create(name=LANDLORD_PORTAL_ROLE)
            user = user_model.objects.create_user(
                username=email,
                email=email,
                is_active=False,
                is_staff=False,
                is_superuser=False,
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
            profile = UserProfile(user=user, name=name, phone=phone)
            profile.full_clean()
            profile.save()
            user.groups.add(role)
            landlord.portal_user = user
            landlord.save(update_fields=["portal_user", "updated_at"])
            landlord = Landlord.objects.select_related("branch", "portal_user").get(pk=landlord.pk)
            challenge, activation_code = _issue_landlord_activation(landlord)
            record_audit_event(
                request,
                "landlord.portal_account_invited",
                "Landlord",
                new_values={
                    "landlord_id": landlord.pk,
                    "user_id": user.pk,
                    "branch_id": landlord.branch_id,
                    "agency_id": membership.agency_id,
                },
            )
    except (IntegrityError, ValidationError) as validation_error:
        return _problem(400, "Invalid landlord", str(validation_error), "invalid-landlord")

    try:
        _send_landlord_activation_email(user, challenge.pk, activation_code)
    except (ImproperlyConfigured, RuntimeError, smtplib.SMTPException, OSError) as mail_error:
        challenge.delete()
        record_audit_event(
            request,
            "landlord.portal_activation_email_failed",
            "Landlord",
            new_values={
                "landlord_id": landlord.pk,
                "user_id": user.pk,
                "error_type": type(mail_error).__name__,
            },
        )
        return JsonResponse(
            {
                "detail": "The landlord account was created, but the activation email failed. Retry the invitation from this page.",
                "landlord": _landlord_portal_account_payload(landlord),
            },
            status=503,
        )

    record_audit_event(
        request,
        "landlord.portal_activation_email_sent",
        "Landlord",
        new_values={"landlord_id": landlord.pk, "user_id": user.pk},
    )
    return JsonResponse(
        {
            "landlord": _landlord_portal_account_payload(landlord),
            "activation_email_sent": True,
        },
        status=201,
    )


@require_POST
def resend_landlord_activation(request, landlord_id):
    if not request.user.is_authenticated:
        return _problem(
            401,
            "Authentication required",
            "Sign in as a Property Officer first.",
            "authentication-required",
        )
    profile = getattr(request.user, "profile", None)
    if profile is None:
        return _problem(
            403,
            "Permission denied",
            "A Property Officer profile is required.",
            "property-officer-required",
        )
    landlord = (
        Landlord.objects.select_related("branch", "portal_user")
        .filter(
            pk=landlord_id,
            branch__in=profile.accessible_branches(
                role_names=("Property Officer / Agent",)
            ),
        )
        .first()
    )
    if landlord is None:
        return _problem(404, "Landlord not found", "This landlord is outside your branch scope.", "landlord-not-found")
    _, error = _property_officer_membership(request.user, branch_id=landlord.branch_id)
    if error:
        return error
    if landlord.portal_user_id is None:
        return _problem(409, "Account not invited", "Create the landlord portal account before resending activation.", "landlord-account-missing")
    if landlord.portal_user.is_active:
        return _problem(409, "Already activated", "This landlord account is already active.", "already-activated")
    if not landlord.email:
        return _problem(400, "Email required", "Add an email address to the landlord record before sending an invitation.", "landlord-email-required")

    challenge, code = _issue_landlord_activation(landlord)
    try:
        _send_landlord_activation_email(landlord.portal_user, challenge.pk, code)
    except (ImproperlyConfigured, RuntimeError, smtplib.SMTPException, OSError) as mail_error:
        challenge.delete()
        record_audit_event(
            request,
            "landlord.portal_activation_email_failed",
            "Landlord",
            new_values={
                "landlord_id": landlord.pk,
                "user_id": landlord.portal_user_id,
                "error_type": type(mail_error).__name__,
            },
        )
        return _problem(503, "Email unavailable", "The activation email could not be sent.", "activation-email-failed")
    record_audit_event(
        request,
        "landlord.portal_activation_resent",
        "Landlord",
        new_values={"landlord_id": landlord.pk, "user_id": landlord.portal_user_id},
    )
    return JsonResponse({"activation_email_sent": True})


@require_POST
def activate_landlord(request):
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    challenge_id = body.get("challenge_id")
    code = body.get("code")
    password = body.get("password")
    if (
        not isinstance(challenge_id, str)
        or not isinstance(code, str)
        or not code.isascii()
        or not re.fullmatch(r"\d{6}", code)
        or not isinstance(password, str)
    ):
        return _problem(
            400,
            "Invalid request",
            "A challenge ID, six-digit code, and password are required.",
            "invalid-request",
        )

    with transaction.atomic():
        try:
            challenge = LandlordActivationChallenge.objects.select_for_update().select_related(
                "landlord__portal_user"
            ).get(
                pk=challenge_id,
                landlord__portal_user__is_active=False,
            )
        except (LandlordActivationChallenge.DoesNotExist, ValueError):
            return _problem(400, "Invalid activation", "The activation code is invalid.", "invalid-activation")
        if challenge.is_expired() or challenge.attempts >= settings.AUTH_MFA_MAX_ATTEMPTS:
            challenge.delete()
            return _problem(
                400,
                "Activation expired",
                "This activation code has expired. Ask the Property Officer to resend the invitation.",
                "activation-expired",
            )
        if not check_password(code, challenge.code_hash):
            challenge.attempts += 1
            if challenge.attempts >= settings.AUTH_MFA_MAX_ATTEMPTS:
                challenge.delete()
            else:
                challenge.save(update_fields=["attempts"])
            return _problem(400, "Invalid code", "The activation code is invalid.", "invalid-activation")
        user = challenge.landlord.portal_user
        try:
            password_validation.validate_password(password, user=user)
        except ValidationError as validation_error:
            return _problem(
                400,
                "Invalid password",
                " ".join(validation_error.messages),
                "invalid-password",
            )
        user.set_password(password)
        user.is_active = True
        user.save(update_fields=["password", "is_active"])
        LandlordActivationChallenge.objects.filter(landlord=challenge.landlord).delete()

    _record_auth_event(request, "landlord.portal_activation_succeeded", user)
    return JsonResponse({"status": "activated"})


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


def _property_read_access_error(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if _has_role(request.user, LANDLORD_PORTAL_ROLE):
        return None
    return _property_access_error(request)


def _properties_visible_to_user(user):
    if _has_role(user, LANDLORD_PORTAL_ROLE):
        return _landlord_portal_properties(user)
    return _accessible_properties(user)


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


@require_http_methods(["GET", "PATCH"])
def caretaker_properties(request):
    error = _caretaker_dashboard_error(request)
    if error:
        return error
    profile = UserProfile.objects.filter(user=request.user).first()
    if profile is None:
        return _problem(403, "Profile required", "A caretaker profile is required.", "profile-required")
    properties = _caretaker_assigned_properties(request.user).select_related("branch").order_by(
        "name", "pk"
    )
    if request.method == "PATCH":
        body = _json_body(request)
        raw_property_id = body.get("activePropertyId") if body is not None else None
        if isinstance(raw_property_id, bool) or not isinstance(raw_property_id, (int, str)):
            return _problem(400, "Invalid property", "Select an assigned property.", "invalid-property")
        try:
            property_id = int(raw_property_id)
        except (TypeError, ValueError):
            return _problem(400, "Invalid property", "Select an assigned property.", "invalid-property")
        property_obj = properties.filter(pk=property_id).first()
        if property_obj is None:
            return _problem(404, "Property not found", "The property is not assigned to your account.", "property-not-found")
        profile.active_caretaker_property = property_obj
        profile.save(update_fields=["active_caretaker_property"])
        record_audit_event(
            request,
            "caretaker.active_property.changed",
            "UserProfile",
            new_values={"active_caretaker_property_id": property_obj.pk},
        )
        return JsonResponse({"activePropertyId": property_obj.pk})

    active_property_id = profile.active_caretaker_property_id
    if active_property_id is None or not properties.filter(pk=active_property_id).exists():
        active_property_id = properties.values_list("pk", flat=True).first()
    return JsonResponse(
        {
            "properties": [
                {
                    "id": property_obj.pk,
                    "name": property_obj.name,
                    "branch_name": property_obj.branch.name,
                }
                for property_obj in properties
            ],
            "activePropertyId": active_property_id,
        }
    )


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
            group, _ = Group.objects.get_or_create(name=CARETAKER_ROLE)
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
        profile__agency_memberships__agency=property_obj.branch.agency,
        profile__agency_memberships__agency__is_active=True,
        profile__agency_memberships__roles__name=CARETAKER_ROLE,
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
    if not _has_role(request.user, LANDLORD_PORTAL_ROLE):
        return _problem(403, "Permission denied", "A landlord portal account is required.", "landlord-portal-required")
    return None


def _caretaker_invitation_properties(request):
    if _has_role(request.user, LANDLORD_PORTAL_ROLE):
        return _landlord_portal_properties(request.user)
    if _has_landlord_management_access(request.user):
        return _accessible_properties(request.user)
    return Property.objects.none()


def _caretaker_invitation_access_error(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not (
        _has_role(request.user, LANDLORD_PORTAL_ROLE)
        or _has_landlord_management_access(request.user)
    ):
        return _problem(
            403,
            "Permission denied",
            "A landlord or property-management account is required to manage caretakers.",
            "caretaker-management-required",
        )
    return None


def _landlord_property_values(body, *, landlord, creating):
    values = {}
    string_fields = {
        "name": ("name", 120),
        "property_type": ("propertyType", 100),
        "location": ("location", 255),
        "address": ("address", 255),
        "currency_code": ("currencyCode", 3),
        "timezone": ("timezone", 64),
        "billing_mode": ("billingMode", 20),
    }
    defaults = {
        "property_type": "residential",
        "location": body.get("address") or landlord.branch.town,
        "address": "",
        "currency_code": "KES",
        "timezone": "Africa/Nairobi",
        "billing_mode": Property.BILLING_MODE_METERED_WATER,
    }
    for field, (request_key, max_length) in string_fields.items():
        if request_key in body or creating:
            value = body.get(request_key, defaults.get(field))
            if value is None and field == "address":
                value = ""
            if not isinstance(value, str):
                raise ValidationError(f"{request_key} must be text.")
            value = value.strip()
            if field == "currency_code":
                value = value.upper()
            if field in {"name", "property_type", "location", "currency_code", "timezone", "billing_mode"} and not value:
                raise ValidationError(f"{request_key} is required.")
            if len(value) > max_length:
                raise ValidationError(f"{request_key} must be {max_length} characters or fewer.")
            values[field] = value

    decimal_fields = {
        "default_rent": ("defaultRent", 2),
        "security_deposit": ("securityDeposit", 2),
        "water_deposit": ("waterDeposit", 2),
        "default_garbage_charge": ("garbageCharge", 2),
        "default_water_rate": ("waterRate", 4),
    }
    for field, (request_key, decimal_places) in decimal_fields.items():
        if request_key not in body and not creating:
            continue
        raw_value = body.get(request_key, 0)
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float, str, Decimal)):
            raise ValidationError(f"{request_key} must be a non-negative number.")
        try:
            value = Decimal(str(raw_value))
        except InvalidOperation as error:
            raise ValidationError(f"{request_key} must be a non-negative number.") from error
        if not value.is_finite() or value < 0:
            raise ValidationError(f"{request_key} must be a non-negative number.")
        if max(0, -value.as_tuple().exponent) > decimal_places:
            raise ValidationError(f"{request_key} allows at most {decimal_places} decimal places.")
        values[field] = value

    if "rentDueDay" in body or creating:
        raw_due_day = body.get("rentDueDay", 10)
        if isinstance(raw_due_day, bool):
            raise ValidationError("rentDueDay must be a whole number between 1 and 28.")
        try:
            due_day = int(raw_due_day)
        except (TypeError, ValueError) as error:
            raise ValidationError("rentDueDay must be a whole number between 1 and 28.") from error
        if str(raw_due_day).strip() not in {str(due_day), f"{due_day}.0"} or not 1 <= due_day <= 28:
            raise ValidationError("rentDueDay must be a whole number between 1 and 28.")
        values["rent_due_day"] = due_day

    if creating and "location" not in body:
        values["location"] = str(body.get("address") or landlord.branch.town).strip()
    return values


def _landlord_recurring_charge_values(value):
    if not isinstance(value, list):
        raise ValidationError("recurringCharges must be an array.")
    if len(value) > 20:
        raise ValidationError("A property can have at most 20 recurring utility charges.")
    charges = []
    seen_names = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValidationError("Every recurring charge must be an object.")
        name = item.get("name")
        amount = item.get("amount")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise ValidationError("Every recurring charge needs a name of 80 characters or fewer.")
        normalized_name = name.strip().casefold()
        if normalized_name in seen_names:
            raise ValidationError("Recurring charge names must be unique within the property.")
        seen_names.add(normalized_name)
        if isinstance(amount, bool) or not isinstance(amount, (int, float, str, Decimal)):
            raise ValidationError("Every recurring charge needs a non-negative amount.")
        try:
            amount = Decimal(str(amount))
        except InvalidOperation as error:
            raise ValidationError("Every recurring charge needs a non-negative amount.") from error
        if not amount.is_finite() or amount < 0 or max(0, -amount.as_tuple().exponent) > 2:
            raise ValidationError("Every recurring charge needs a non-negative amount with at most two decimal places.")
        charges.append(
            {
                "name": name.strip(),
                "amount": amount,
                "is_active": item.get("isActive") is not False,
            }
        )
    return charges


@require_GET
def landlord_dashboard(request):
    error = _landlord_portal_error(request)
    if error:
        return error

    today = timezone.localdate()
    period_start = today.replace(day=1)
    next_period_start = (
        date(today.year + 1, 1, 1)
        if today.month == 12
        else date(today.year, today.month + 1, 1)
    )
    period_end = next_period_start - timedelta(days=1)
    properties = list(
        _landlord_portal_properties(request.user).values("id", "name", "location")
    )
    property_ids = [property_obj["id"] for property_obj in properties]
    metrics_by_property = {
        property_id: {
            "total_units": 0,
            "occupied_units": 0,
            "expected_rent": Decimal("0.00"),
            "collected_rent": Decimal("0.00"),
            "arrears": Decimal("0.00"),
            "approved_expenses": Decimal("0.00"),
            "pending_expenses": Decimal("0.00"),
            "pending_expense_count": 0,
            "open_maintenance_count": 0,
        }
        for property_id in property_ids
    }

    if property_ids:
        unit_rows = (
            Unit.objects.filter(property_id__in=property_ids)
            .exclude(status=Unit.STATUS_ARCHIVED)
            .values("property_id")
            .annotate(
                total_units=Count("id"),
                occupied_units=Count("id", filter=Q(status=Unit.STATUS_OCCUPIED)),
            )
        )
        for row in unit_rows:
            metrics = metrics_by_property[row["property_id"]]
            metrics["total_units"] = row["total_units"]
            metrics["occupied_units"] = row["occupied_units"]

        money_field = DecimalField(max_digits=14, decimal_places=2)
        zero_money = Value(Decimal("0.00"), output_field=money_field)
        valid_allocations = (
            PaymentAllocation.objects.filter(invoice_id=OuterRef("pk"))
            .exclude(
                payment__instrument_status__in=(
                    Payment.INSTRUMENT_PENDING,
                    Payment.INSTRUMENT_BOUNCED,
                )
            )
            .values("invoice_id")
            .annotate(total=Sum("amount"))
            .values("total")[:1]
        )
        invoice_waivers = (
            ArrearsWaiver.objects.filter(invoice_id=OuterRef("pk"))
            .values("invoice_id")
            .annotate(total=Sum("amount"))
            .values("total")[:1]
        )
        scoped_invoices = Invoice.objects.filter(
            tenancy__unit__property_id__in=property_ids,
            status=Invoice.STATUS_ISSUED,
        ).annotate(
            collected_amount=Coalesce(
                Subquery(valid_allocations, output_field=money_field),
                zero_money,
            ),
            waived_amount=Coalesce(
                Subquery(invoice_waivers, output_field=money_field),
                zero_money,
            ),
        )
        current_invoice_totals = (
            scoped_invoices.filter(
                period_start__gte=period_start,
                period_start__lt=next_period_start,
            )
            .values("tenancy__unit__property_id")
            .annotate(
                expected=Sum("total"),
                collected=Sum("collected_amount"),
            )
        )
        for row in current_invoice_totals:
            metrics = metrics_by_property[row["tenancy__unit__property_id"]]
            metrics["expected_rent"] = row["expected"] or Decimal("0.00")
            metrics["collected_rent"] = row["collected"] or Decimal("0.00")

        overdue_totals = (
            scoped_invoices.filter(due_date__lt=today)
            .annotate(
                outstanding=Greatest(
                    F("total") - F("collected_amount") - F("waived_amount"),
                    zero_money,
                    output_field=money_field,
                )
            )
            .values("tenancy__unit__property_id")
            .annotate(total=Sum("outstanding"))
        )
        for row in overdue_totals:
            metrics_by_property[row["tenancy__unit__property_id"]]["arrears"] = (
                row["total"] or Decimal("0.00")
            )

        expense_rows = (
            PropertyExpense.objects.filter(
                property_id__in=property_ids,
                effective_from__gte=period_start,
                effective_from__lt=next_period_start,
            )
            .values("property_id", "status")
            .annotate(total=Sum("amount"), count=Count("id"))
        )
        for row in expense_rows:
            metrics = metrics_by_property[row["property_id"]]
            if row["status"] == PropertyExpense.STATUS_APPROVED:
                metrics["approved_expenses"] = row["total"] or Decimal("0.00")
            elif row["status"] == PropertyExpense.STATUS_SUBMITTED:
                metrics["pending_expenses"] = row["total"] or Decimal("0.00")
                metrics["pending_expense_count"] = row["count"]

        maintenance_rows = (
            MaintenanceTicket.objects.filter(property_id__in=property_ids)
            .exclude(
                status__in=(
                    MaintenanceTicket.STATUS_COMPLETED,
                    MaintenanceTicket.STATUS_CLOSED,
                )
            )
            .values("property_id")
            .annotate(total=Count("id"))
        )
        for row in maintenance_rows:
            metrics_by_property[row["property_id"]]["open_maintenance_count"] = row["total"]

    property_summaries = []
    summary = {
        "total_units": 0,
        "occupied_units": 0,
        "expected_rent": Decimal("0.00"),
        "collected_rent": Decimal("0.00"),
        "arrears": Decimal("0.00"),
        "approved_expenses": Decimal("0.00"),
        "pending_expenses": Decimal("0.00"),
        "pending_expense_count": 0,
        "open_maintenance_count": 0,
    }
    for property_obj in properties:
        metrics = metrics_by_property[property_obj["id"]]
        summary["total_units"] += metrics["total_units"]
        summary["occupied_units"] += metrics["occupied_units"]
        summary["expected_rent"] += metrics["expected_rent"]
        summary["collected_rent"] += metrics["collected_rent"]
        summary["arrears"] += metrics["arrears"]
        summary["approved_expenses"] += metrics["approved_expenses"]
        summary["pending_expenses"] += metrics["pending_expenses"]
        summary["pending_expense_count"] += metrics["pending_expense_count"]
        summary["open_maintenance_count"] += metrics["open_maintenance_count"]
        property_summaries.append(
            {
                **property_obj,
                **metrics,
                "occupancy_rate": (
                    metrics["occupied_units"] / metrics["total_units"] * 100
                    if metrics["total_units"]
                    else 0
                ),
            }
        )

    pending_high_cost_repairs = []
    if property_ids:
        submitted_quotations = (
            MaintenanceQuotation.objects.filter(
                ticket__property_id__in=property_ids,
                status=MaintenanceQuotation.STATUS_SUBMITTED,
            )
            .select_related("ticket__property", "ticket__unit", "vendor")
            .order_by("-created_at", "-id")
        )
        approval_threshold = None
        for quotation in submitted_quotations:
            if approval_threshold is None:
                approval_threshold = _maintenance_approval_threshold(quotation.ticket)
            if quotation.amount <= approval_threshold:
                continue
            pending_high_cost_repairs.append(
                {
                    "id": quotation.pk,
                    "ticket_id": quotation.ticket_id,
                    "ticket_title": quotation.ticket.title,
                    "property_name": quotation.ticket.property.name,
                    "unit_code": (
                        quotation.ticket.unit.code
                        if quotation.ticket.unit_id
                        else None
                    ),
                    "vendor_name": quotation.vendor.name,
                    "amount": str(quotation.amount),
                    "approval_threshold": str(approval_threshold),
                    "submitted_at": quotation.created_at.isoformat(),
                }
            )

    record_audit_event(
        request,
        "landlord_dashboard.viewed",
        "LandlordDashboard",
        new_values={
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "property_count": len(properties),
            "pending_high_cost_quote_count": len(pending_high_cost_repairs),
        },
    )
    landlord = Landlord.objects.filter(portal_user=request.user).first()
    projected_net_payout = None
    projected_net_payout_status = "not_configured"
    if landlord is not None:
        statement, commission_status, tax_status = _build_landlord_statement(
            landlord, period_start, period_end
        )
        projected_net_payout = statement["summary"]["net_payout"]
        projected_net_payout_status = (
            commission_status
            if commission_status != "configured"
            else tax_status
            if tax_status == "not_configured"
            else "configured"
        )

    collections = []
    if property_ids:
        recent_allocations = (
            PaymentAllocation.objects.filter(
                invoice__status=Invoice.STATUS_ISSUED,
                invoice__tenancy__unit__property_id__in=property_ids,
                payment__source__in=(Payment.SOURCE_MPESA, Payment.SOURCE_BANK),
                payment__received_at__date__lte=today,
            )
            .exclude(
                payment__instrument_status__in=(
                    Payment.INSTRUMENT_PENDING,
                    Payment.INSTRUMENT_BOUNCED,
                )
            )
            .select_related(
                "payment",
                "invoice__tenancy__tenant",
                "invoice__tenancy__unit__property",
            )
            .order_by("-payment__received_at", "-id")[:10]
        )
        collections = [
            {
                "id": allocation.pk,
                "received_at": allocation.payment.received_at.isoformat(),
                "amount": str(allocation.amount),
                "source": allocation.payment.source,
                "reference": allocation.payment.reference,
                "property_name": allocation.invoice.tenancy.unit.property.name,
                "unit_code": allocation.invoice.tenancy.unit.code,
                "tenant_name": allocation.invoice.tenancy.tenant.name,
            }
            for allocation in recent_allocations
        ]
    return JsonResponse(
        {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "summary": {
                **summary,
                "projected_net_payout": projected_net_payout,
                "projected_net_payout_status": projected_net_payout_status,
                "occupancy_rate": (
                    summary["occupied_units"] / summary["total_units"] * 100
                    if summary["total_units"]
                    else 0
                ),
            },
            "properties": property_summaries,
            "collections": collections,
            "pending_high_cost_repairs": pending_high_cost_repairs,
        },
        json_dumps_params={"default": str},
    )


@require_http_methods(["GET", "POST", "PATCH"])
def list_landlord_properties(request):
    error = _landlord_portal_error(request)
    if error:
        return error

    body = None
    if request.method in {"POST", "PATCH"}:
        body = _json_body(request)
        if body is None:
            return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")

    properties = _landlord_portal_properties(request.user).select_related(
        "branch"
    ).order_by("created_at", "pk")
    if request.method == "POST":
        landlord = Landlord.objects.filter(portal_user=request.user).first()
        if landlord is None:
            return _problem(403, "Landlord account required", "Link a landlord record to this portal account first.", "landlord-account-required")
        profile = UserProfile.objects.filter(user=request.user).first()
        if profile is None:
            return _problem(403, "Profile required", "A landlord profile is required to manage properties.", "profile-required")
        try:
            property_values = _landlord_property_values(body, landlord=landlord, creating=True)
            recurring_charges = _landlord_recurring_charge_values(
                body.get("recurringCharges", [])
            )
            property_obj = Property(branch=landlord.branch, landlord=landlord, **property_values)
            active_property_is_valid = properties.filter(
                pk=profile.active_property_id
            ).exists()
            with transaction.atomic():
                property_obj.full_clean()
                property_obj.save()
                for charge_values in recurring_charges:
                    charge = PropertyRecurringCharge(property=property_obj, **charge_values)
                    charge.full_clean()
                    charge.save()
                if not active_property_is_valid:
                    profile.active_property = property_obj
                    profile.save(update_fields=["active_property"])
                    record_audit_event(
                        request,
                        "landlord_active_property.changed",
                        "UserProfile",
                        new_values={"active_property_id": property_obj.pk},
                    )
                record_audit_event(
                    request,
                    "landlord_property.created",
                    "Property",
                    new_values={
                        "property_id": property_obj.pk,
                        "branch_id": property_obj.branch_id,
                        "name": property_obj.name,
                        "property_type": property_obj.property_type,
                        "location": property_obj.location,
                        "address": property_obj.address or None,
                        "configuration": {
                            "currency_code": property_obj.currency_code,
                            "timezone": property_obj.timezone,
                            "rent_due_day": property_obj.rent_due_day,
                            "billing_mode": property_obj.billing_mode,
                            "default_rent": str(property_obj.default_rent),
                            "security_deposit": str(property_obj.security_deposit),
                            "water_deposit": str(property_obj.water_deposit),
                            "default_garbage_charge": str(property_obj.default_garbage_charge),
                            "default_water_rate": str(property_obj.default_water_rate),
                        },
                        "recurring_charges": [
                            {
                                "name": charge["name"],
                                "amount": str(charge["amount"]),
                                "is_active": charge["is_active"],
                            }
                            for charge in recurring_charges
                        ],
                    },
                )
        except ValidationError as validation_error:
            return _problem(400, "Invalid property", "; ".join(validation_error.messages), "invalid-property")
        except IntegrityError:
            return _problem(400, "Invalid property", "The property or recurring charges conflict with existing records.", "invalid-property")
        return JsonResponse(
            {
                "property": _landlord_property_payload(property_obj),
                "activePropertyId": profile.active_property_id,
            },
            status=201,
        )

    if request.method == "PATCH":
        if "activePropertyId" in body:
            if len(body) != 1:
                return _problem(400, "Invalid request", "Select an active property separately from property edits.", "invalid-request")
            if isinstance(body["activePropertyId"], bool):
                return _problem(400, "Invalid property", "activePropertyId must be an integer.", "invalid-property")
            try:
                property_id = int(body["activePropertyId"])
            except (TypeError, ValueError):
                return _problem(400, "Invalid property", "activePropertyId must be an integer.", "invalid-property")
            property_obj = properties.filter(pk=property_id).first()
            if property_obj is None:
                return _problem(404, "Property not found", "The property is not linked to this landlord account.", "property-not-found")
            profile = UserProfile.objects.filter(user=request.user).first()
            if profile is None:
                return _problem(403, "Profile required", "A landlord profile is required to manage properties.", "profile-required")
            profile.active_property = property_obj
            profile.save(update_fields=["active_property"])
            record_audit_event(
                request,
                "landlord_active_property.changed",
                "UserProfile",
                new_values={"active_property_id": property_obj.pk},
            )
            return JsonResponse({"activePropertyId": property_obj.pk})

        if isinstance(body.get("propertyId"), bool):
            return _problem(400, "Invalid property", "propertyId must be an integer.", "invalid-property")
        try:
            property_id = int(body.get("propertyId"))
        except (TypeError, ValueError):
            return _problem(400, "Invalid property", "propertyId must be an integer.", "invalid-property")
        property_obj = properties.filter(pk=property_id).first()
        if property_obj is None:
            return _problem(404, "Property not found", "The property is not linked to this landlord account.", "property-not-found")
        try:
            property_values = _landlord_property_values(body, landlord=property_obj.landlord, creating=False)
            has_recurring_charges = "recurringCharges" in body
            recurring_charges = (
                _landlord_recurring_charge_values(body["recurringCharges"])
                if has_recurring_charges
                else None
            )
            if not property_values and recurring_charges is None:
                return _problem(400, "Invalid request", "Provide property fields or recurringCharges to update.", "invalid-request")
            with transaction.atomic():
                property_obj = properties.select_for_update().get(pk=property_id)
                old_values = {
                    field: str(getattr(property_obj, field))
                    for field in property_values
                }
                for field, value in property_values.items():
                    setattr(property_obj, field, value)
                property_obj.full_clean()
                property_obj.save()
                old_charges = []
                if recurring_charges is not None:
                    current_charges = list(
                        property_obj.recurring_charges.select_for_update().order_by("id")
                    )
                    old_charges = [
                        {
                            "name": charge.name,
                            "amount": str(charge.amount),
                            "is_active": charge.is_active,
                        }
                        for charge in current_charges
                    ]
                    property_obj.recurring_charges.all().delete()
                    for charge_values in recurring_charges:
                        charge = PropertyRecurringCharge(property=property_obj, **charge_values)
                        charge.full_clean()
                        charge.save()
                record_audit_event(
                    request,
                    "landlord_property.updated",
                    "Property",
                    old_values={
                        "property_id": property_obj.pk,
                        "fields": old_values,
                        **({"recurring_charges": old_charges} if recurring_charges is not None else {}),
                    },
                    new_values={
                        "property_id": property_obj.pk,
                        "fields": {
                            field: str(getattr(property_obj, field))
                            for field in property_values
                        },
                        **(
                            {
                                "recurring_charges": [
                                    {
                                        "name": charge["name"],
                                        "amount": str(charge["amount"]),
                                        "is_active": charge["is_active"],
                                    }
                                    for charge in recurring_charges
                                ]
                            }
                            if recurring_charges is not None
                            else {}
                        ),
                    },
                )
        except Property.DoesNotExist:
            return _problem(404, "Property not found", "The property is not linked to this landlord account.", "property-not-found")
        except ValidationError as validation_error:
            return _problem(400, "Invalid property", "; ".join(validation_error.messages), "invalid-property")
        except IntegrityError:
            return _problem(400, "Invalid property", "The property or recurring charges conflict with existing records.", "invalid-property")
        return JsonResponse({"property": _landlord_property_payload(property_obj)})

    property_ids = list(properties.values_list("pk", flat=True))
    active_property_id = request.user.profile.active_property_id
    if active_property_id not in property_ids:
        active_property_id = property_ids[0] if property_ids else None
    landlord = Landlord.objects.filter(portal_user=request.user).first()
    property_records = list(
        properties.prefetch_related("recurring_charges", "caretaker_assignments")
    )
    details_by_property = {
        item["id"]: item
        for item in _landlord_property_detail_payloads(property_records, landlord)
    } if landlord is not None else {}
    payload = []
    for property_obj in property_records:
        item = _landlord_property_payload(property_obj)
        item.update(details_by_property.get(property_obj.pk, {}))
        payload.append(item)
    record_audit_event(
        request,
        "landlord_properties.viewed",
        "LandlordPropertyList",
        new_values={"property_count": len(payload)},
    )
    return JsonResponse({"properties": payload, "activePropertyId": active_property_id})


@require_http_methods(["GET", "PATCH"])
def landlord_billing_settings(request):
    error = _landlord_portal_error(request)
    if error:
        return error
    profile = UserProfile.objects.filter(user=request.user).first()
    if profile is None:
        return _problem(403, "Profile required", "A landlord profile is required to manage billing settings.", "profile-required")
    properties = _landlord_portal_properties(request.user)
    property_obj = properties.filter(pk=profile.active_property_id).first()
    if property_obj is None:
        property_obj = properties.order_by("created_at", "pk").first()
    if property_obj is None:
        return _problem(403, "Property required", "Link a property to your landlord account first.", "property-required")

    if request.method == "GET":
        settings_record = PropertyBillingSettings.objects.filter(property=property_obj).first()
        settings_payload = {
            "paybill_number": settings_record.paybill_number if settings_record else "",
            "account_number": settings_record.account_number if settings_record else "",
            "universal_security_deposit": (
                str(settings_record.universal_security_deposit) if settings_record else "8000"
            ),
            "universal_water_deposit": (
                str(settings_record.universal_water_deposit) if settings_record else "500"
            ),
            "first_online_billing_month": (
                settings_record.first_online_billing_month or None if settings_record else None
            ),
        }
        return JsonResponse({"settings": settings_payload})

    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    paybill_number = body.get("paybillNumber", "")
    account_number = body.get("accountNumber", "")
    if not isinstance(paybill_number, str) or not isinstance(account_number, str):
        return _problem(400, "Invalid settings", "Paybill and account numbers must be text.", "invalid-settings")
    paybill_number = paybill_number.strip()
    account_number = account_number.strip()
    if len(paybill_number) > 32 or len(account_number) > 64:
        return _problem(400, "Invalid settings", "Payment instructions exceed the allowed length.", "invalid-settings")

    settings_record, _ = PropertyBillingSettings.objects.get_or_create(property=property_obj)
    settings_record.paybill_number = paybill_number
    settings_record.account_number = account_number
    settings_record.updated_by = request.user
    try:
        settings_record.full_clean()
        settings_record.save()
    except ValidationError as validation_error:
        return _problem(400, "Invalid settings", str(validation_error.message_dict), "invalid-settings")
    record_audit_event(
        request,
        "property_billing_settings.updated",
        "PropertyBillingSettings",
        new_values={
            "property_id": property_obj.pk,
            "paybill_number": settings_record.paybill_number,
            "account_number": settings_record.account_number,
        },
    )
    return JsonResponse({
        "settings": {
            "paybill_number": settings_record.paybill_number,
            "account_number": settings_record.account_number,
            "universal_security_deposit": str(settings_record.universal_security_deposit),
            "universal_water_deposit": str(settings_record.universal_water_deposit),
            "first_online_billing_month": settings_record.first_online_billing_month or None,
        }
    })


@require_GET
def list_landlord_units(request):
    error = _landlord_portal_error(request)
    if error:
        return error
    units = Unit.objects.filter(
        property__in=_landlord_portal_properties(request.user)
    ).order_by("property_id", "code")
    payload = [_unit_payload(unit) for unit in units]
    record_audit_event(
        request,
        "landlord_units.viewed",
        "LandlordUnitList",
        new_values={"unit_count": len(payload)},
    )
    return JsonResponse({"units": payload})


@require_GET
def landlord_move_in_units(request):
    property_obj, error = _landlord_move_in_property(request)
    if error:
        return error
    today = timezone.localdate()
    units = (
        Unit.objects.filter(property=property_obj)
        .select_related("floor", "property")
        .prefetch_related("charges")
        .order_by("code", "id")
    )
    unit_payloads = []
    for unit in units:
        tenancy = (
            Tenancy.objects.filter(unit=unit, status=Tenancy.STATUS_ACTIVE)
            .select_related("tenant", "tenant__portal_user")
            .order_by("-start_date", "-id")
            .first()
        )
        settlement = (
            MoveInSettlement.objects.filter(
                unit=unit,
                status__in=MoveInSettlement.OPEN_STATUSES,
            )
            .order_by("-created_at", "-id")
            .first()
        )
        rent_charge = _current_rent(unit, today)
        garbage_charge = (
            unit.charges.filter(
                charge_type=UnitCharge.CHARGE_RECURRING,
                name__icontains="garbage",
                effective_from__lte=today,
            )
            .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=today))
            .order_by("-effective_from", "-id")
            .first()
        )
        tenant = tenancy.tenant if tenancy else None
        mapped_status = {
            Unit.STATUS_VACANT: "vacant",
            Unit.STATUS_BOOKED: "booked",
            Unit.STATUS_OCCUPIED: "occupied",
            Unit.STATUS_NOTICE_GIVEN: "notice_given",
            Unit.STATUS_DEPOSIT_PENDING: "deposit_pending",
            Unit.STATUS_MAINTENANCE: "maintenance",
            Unit.STATUS_ARCHIVED: "archived",
            Unit.STATUS_DRAFT: "draft",
        }.get(unit.status, "unknown")
        unit_payloads.append(
            {
                "id": unit.pk,
                "unitCode": unit.code,
                "roomName": unit.characteristics.get("room_name", unit.code)
                if isinstance(unit.characteristics, dict)
                else unit.code,
                "floor": unit.floor.name if unit.floor else "-",
                "status": mapped_status,
                "rentAmount": float(rent_charge.amount if rent_charge else property_obj.default_rent),
                "garbageAmount": float(
                    garbage_charge.amount
                    if garbage_charge
                    else property_obj.default_garbage_charge
                ),
                "depositAmount": float(
                    settlement.deposit_amount
                    if settlement
                    else property_obj.security_deposit
                ),
                "securityDeposit": float(
                    settlement.deposit_amount
                    if settlement
                    else property_obj.security_deposit
                ),
                "waterDeposit": float(
                    settlement.water_deposit_amount
                    if settlement
                    else property_obj.water_deposit
                ),
                "monthlyWaterRate": float(property_obj.default_water_rate),
                "tenantId": tenant.pk if tenant else None,
                "currentTenant": tenant.name if tenant else "-",
                "tenantEmail": (
                    tenant.email
                    if tenant and tenant.email
                    else settlement.tenant_email
                    if settlement
                    else "-"
                ),
                "tenantPhone": tenant.phone if tenant else "-",
                "tenantStatus": (
                    "active"
                    if tenant and tenant.portal_user_id and tenant.portal_user.is_active
                    else "dormant"
                    if tenant
                    else None
                ),
                "currentTenantName": (
                    tenant.name if tenant else settlement.tenant_name if settlement else "-"
                ),
                "moveInDate": (
                    tenancy.start_date.isoformat()
                    if tenancy
                    else settlement.expected_move_in_date.isoformat()
                    if settlement
                    else "-"
                ),
                "expectedMoveOutDate": tenancy.end_date.isoformat()
                if tenancy and tenancy.end_date
                else None,
            }
        )
    record_audit_event(
        request,
        "landlord_units.viewed",
        "LandlordUnitList",
        new_values={"property_id": property_obj.pk, "unit_count": len(unit_payloads)},
    )
    return JsonResponse({"units": unit_payloads})


def _landlord_move_in_property(request):
    error = _landlord_portal_error(request)
    if error:
        return None, error
    profile = UserProfile.objects.filter(user=request.user).first()
    if profile is None:
        return None, _problem(403, "Profile required", "A landlord profile is required.", "profile-required")
    property_obj = _landlord_portal_properties(request.user).filter(
        pk=profile.active_property_id
    ).first()
    if property_obj is None:
        return None, _problem(403, "Property required", "Select an active property first.", "property-required")
    return property_obj, None


def _active_unit_management_property(request):
    if _has_role(request.user, LANDLORD_PORTAL_ROLE):
        properties = _landlord_portal_properties(request.user)
    else:
        error = _property_access_error(request)
        if error:
            return None, error
        properties = _accessible_properties(request.user)
    profile = UserProfile.objects.filter(user=request.user).first()
    if profile is None:
        return None, _problem(403, "Profile required", "A profile is required to manage units.", "profile-required")
    property_obj = properties.filter(pk=profile.active_property_id).first()
    if property_obj is None:
        return None, _problem(403, "Property required", "Select an active property first.", "property-required")
    return property_obj, None


@require_POST
def update_unit_occupancy(request):
    property_obj, error = _active_unit_management_property(request)
    if error:
        return error
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    unit_id = body.get("unitId")
    status_value = body.get("status")
    if not isinstance(status_value, str) or not status_value.strip():
        return _problem(400, "Invalid status", "A unit status is required.", "invalid-status")
    try:
        unit_id = int(unit_id)
    except (TypeError, ValueError):
        return _problem(400, "Invalid request", "A valid unitId is required.", "invalid-request")
    status_map = {
        "vacant": Unit.STATUS_VACANT,
        "occupied": Unit.STATUS_OCCUPIED,
        "offboarding": Unit.STATUS_NOTICE_GIVEN,
        "notice_given": Unit.STATUS_NOTICE_GIVEN,
        "maintenance": Unit.STATUS_MAINTENANCE,
    }
    new_status = status_map.get(status_value.strip().lower())
    if new_status is None:
        return _problem(400, "Invalid status", "The requested unit status is not supported.", "invalid-status")
    unit = Unit.objects.filter(pk=unit_id, property=property_obj).first()
    if unit is None:
        return _problem(404, "Unit not found", "The unit is unavailable in the active property.", "unit-not-found")
    if new_status == Unit.STATUS_VACANT and Tenancy.objects.filter(
        unit=unit,
        status=Tenancy.STATUS_ACTIVE,
    ).exists():
        return _problem(409, "Unit occupied", "An active tenancy must be ended through the move-out workflow before the unit can be marked vacant.", "active-tenancy")
    previous = _unit_audit_values(unit)
    try:
        with transaction.atomic():
            unit = Unit.objects.select_for_update().get(pk=unit.pk)
            unit.transition_to(new_status)
            unit.full_clean()
            unit.save(update_fields=["status", "listing_enabled", "updated_at"])
    except ValidationError as exc:
        return _problem(409, "Invalid status transition", "; ".join(exc.messages), "invalid-transition")
    record_audit_event(
        request,
        "unit.status_changed",
        "Unit",
        old_values=previous,
        new_values=_unit_audit_values(unit),
    )
    mapped_status = {
        value: key for key, value in {
            "vacant": Unit.STATUS_VACANT,
            "occupied": Unit.STATUS_OCCUPIED,
            "offboarding": Unit.STATUS_NOTICE_GIVEN,
            "maintenance": Unit.STATUS_MAINTENANCE,
        }.items()
    }.get(unit.status, unit.status.lower().replace(" ", "_"))
    return JsonResponse(
        {"success": True, "unit": {"id": unit.pk, "status": mapped_status}}
    )


@require_POST
def manage_unit_action(request):
    property_obj, error = _active_unit_management_property(request)
    if error:
        return error
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    try:
        unit_id = int(body.get("unitId"))
    except (TypeError, ValueError):
        return _problem(400, "Invalid request", "Unit ID and action are required.", "invalid-request")
    action = body.get("action")
    if not isinstance(action, str) or not action.strip():
        return _problem(400, "Invalid request", "Unit ID and action are required.", "invalid-request")
    unit = Unit.objects.filter(pk=unit_id, property=property_obj).first()
    if unit is None:
        return _problem(404, "Unit not found", "The unit is unavailable in the active property.", "unit-not-found")
    if action in {
        "log_notice",
        "revoke_notice",
        "offboard_tenant",
        "immediate_evacuation",
    }:
        return JsonResponse(
            {"error": "Move-out actions now use the audited Django move-out workflow."},
            status=410,
        )
    new_status = {
        "mark_maintenance": Unit.STATUS_MAINTENANCE,
        "mark_vacant": Unit.STATUS_VACANT,
    }.get(action)
    if new_status is None:
        return _problem(400, "Invalid action", "The requested unit action is not supported.", "invalid-action")
    if new_status == Unit.STATUS_VACANT and Tenancy.objects.filter(
        unit=unit,
        status=Tenancy.STATUS_ACTIVE,
    ).exists():
        return _problem(409, "Unit occupied", "An active tenancy must be ended through the move-out workflow before the unit can be marked vacant.", "active-tenancy")
    previous = _unit_audit_values(unit)
    try:
        with transaction.atomic():
            unit = Unit.objects.select_for_update().get(pk=unit.pk)
            unit.transition_to(new_status)
            unit.full_clean()
            unit.save(update_fields=["status", "listing_enabled", "updated_at"])
    except ValidationError as exc:
        return _problem(409, "Invalid status transition", "; ".join(exc.messages), "invalid-transition")
    record_audit_event(
        request,
        "unit.status_changed",
        "Unit",
        old_values=previous,
        new_values=_unit_audit_values(unit),
    )
    mapped_status = {
        Unit.STATUS_VACANT: "vacant",
        Unit.STATUS_MAINTENANCE: "maintenance",
    }[new_status]
    return JsonResponse({"success": True, "unit": {"id": unit.pk, "status": mapped_status}})


@require_http_methods(["GET", "POST", "PATCH", "DELETE"])
def landlord_move_in_settlements(request):
    property_obj, error = _landlord_move_in_property(request)
    if error:
        return error

    if request.method == "GET":
        settlements = MoveInSettlement.objects.filter(
            unit__property=property_obj,
            status__in=MoveInSettlement.OPEN_STATUSES,
        ).select_related("unit").prefetch_related("payments")
        return JsonResponse(
            {"settlements": [_move_in_settlement_payload(item) for item in settlements]}
        )

    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")

    if request.method == "POST":
        required_text = ("tenantName", "tenantEmail", "tenantPhone", "expectedMoveInDate")
        if (
            isinstance(body.get("unitId"), bool)
            or body.get("unitId") in (None, "")
            or any(
                not isinstance(body.get(key), str) or not body[key].strip()
                for key in required_text
            )
        ):
            return _problem(400, "Invalid move-in reservation", "A unit, tenant name, email, phone, and move-in date are required.", "invalid-move-in-reservation")
        try:
            unit_id = int(body["unitId"])
            expected_move_in_date = date.fromisoformat(body["expectedMoveInDate"].strip())
            amounts = {
                field: _decimal_value(body, key)
                for field, key in (
                    ("deposit_amount", "depositAmount"),
                    ("rent_amount", "rentAmount"),
                    ("garbage_amount", "garbageAmount"),
                    ("water_deposit_amount", "waterDepositAmount"),
                )
            }
        except (ValueError, TypeError) as exc:
            return _problem(400, "Invalid move-in reservation", str(exc), "invalid-move-in-reservation")
        if expected_move_in_date < timezone.localdate():
            return _problem(400, "Invalid move-in date", "The move-in date must be today or later.", "invalid-move-in-date")
        for field, amount in amounts.items():
            if amount is None or not amount.is_finite() or amount < 0:
                return _problem(400, "Invalid move-in amount", f"{field} must be a non-negative number.", "invalid-move-in-amount")
            amounts[field] = amount.quantize(Decimal("0.01"))
        if amounts["rent_amount"] <= 0:
            return _problem(400, "Invalid move-in amount", "Rent must be greater than zero.", "invalid-move-in-amount")
        tenant_email = body["tenantEmail"].strip().lower()
        try:
            validate_email(tenant_email)
            tenant_phone = _normalize_ke_phone(body["tenantPhone"].strip())
        except (ValidationError, ValueError) as exc:
            return _problem(400, "Invalid tenant details", str(exc), "invalid-tenant-details")
        user_model = get_user_model()
        if (
            Tenant.objects.filter(email__iexact=tenant_email).exists()
            or user_model.objects.filter(email__iexact=tenant_email).exists()
        ):
            return _problem(409, "Email already registered", "This email is already linked to an account. Use a different email or the tenant transfer workflow.", "email-already-registered")

        unit = Unit.objects.filter(pk=unit_id, property=property_obj).first()
        if unit is None:
            return _problem(404, "Unit not found", "The unit is unavailable in the active property.", "unit-not-found")
        if unit.status != Unit.STATUS_VACANT:
            return _problem(409, "Unit unavailable", "Only a vacant unit can be reserved for a move-in.", "unit-unavailable")
        recurring_charges = list(
            PropertyRecurringCharge.objects.filter(property=property_obj, is_active=True)
            .order_by("name", "id")
            .values("name", "amount")
        )
        utility_charges = [
            {"name": charge["name"], "amount": str(charge["amount"])}
            for charge in recurring_charges
        ]
        first_month_rent = _move_in_first_month_amount(
            amounts["rent_amount"],
            expected_move_in_date,
        )
        total_required = (
            first_month_rent
            + amounts["deposit_amount"]
            + amounts["water_deposit_amount"]
        ).quantize(Decimal("0.01"))
        try:
            with transaction.atomic():
                locked_unit = Unit.objects.select_for_update().get(
                    pk=unit.pk,
                    property=property_obj,
                )
                if locked_unit.status != Unit.STATUS_VACANT:
                    return _problem(409, "Unit unavailable", "This unit is no longer vacant.", "unit-unavailable")
                if MoveInSettlement.objects.select_for_update().filter(
                    unit=locked_unit,
                    status__in=MoveInSettlement.OPEN_STATUSES,
                ).exists():
                    return _problem(409, "Reservation exists", "This unit already has an open move-in reservation.", "reservation-exists")
                if MoveInSettlement.objects.filter(
                    tenant_email__iexact=tenant_email,
                    status__in=MoveInSettlement.OPEN_STATUSES,
                ).exists():
                    return _problem(409, "Reservation exists", "This email already has an open move-in reservation.", "reservation-exists")
                settlement = MoveInSettlement(
                    unit=locked_unit,
                    tenant_name=body["tenantName"].strip(),
                    tenant_email=tenant_email,
                    tenant_phone=tenant_phone,
                    utility_charges=utility_charges,
                    total_required=total_required,
                    outstanding_balance=total_required,
                    expected_move_in_date=expected_move_in_date,
                    **amounts,
                )
                settlement.full_clean()
                settlement.save()
        except IntegrityError:
            return _problem(409, "Reservation exists", "The unit or tenant already has an open move-in reservation.", "reservation-exists")
        record_audit_event(
            request,
            "move_in_settlement.created",
            "MoveInSettlement",
            new_values=_move_in_settlement_payload(settlement),
        )
        return JsonResponse({"settlement": _move_in_settlement_payload(settlement)}, status=201)

    settlement_id = body.get("settlementId")
    if isinstance(settlement_id, bool):
        return _problem(400, "Invalid request", "settlementId is required.", "invalid-request")
    try:
        settlement_id = int(settlement_id)
    except (TypeError, ValueError):
        return _problem(400, "Invalid request", "settlementId is required.", "invalid-request")

    if request.method == "DELETE":
        reason = body.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return _problem(400, "Invalid request", "A cancellation reason is required.", "invalid-request")
        try:
            with transaction.atomic():
                settlement = MoveInSettlement.objects.select_for_update().filter(
                    pk=settlement_id,
                    unit__property=property_obj,
                ).select_related("unit").first()
                if settlement is None:
                    return _problem(404, "Settlement not found", "The settlement is unavailable in the active property.", "settlement-not-found")
                if settlement.tenant_id or settlement.activation_email_sent_at:
                    return _problem(409, "Cancellation unavailable", "This reservation already has a tenant account or activation email. Revoke tenant access through the move-out workflow instead.", "reservation-not-cancellable")
                if settlement.status not in MoveInSettlement.OPEN_STATUSES:
                    return _problem(409, "Cancellation unavailable", "This settlement can no longer be cancelled.", "reservation-not-cancellable")
                settlement.status = MoveInSettlement.STATUS_CANCELLED
                settlement.cancellation_reason = reason.strip()
                settlement.cancelled_at = timezone.now()
                settlement.save(update_fields=["status", "cancellation_reason", "cancelled_at", "updated_at"])
                if (
                    settlement.unit.status == Unit.STATUS_BOOKED
                    and not Tenancy.objects.filter(
                        unit=settlement.unit,
                        status=Tenancy.STATUS_ACTIVE,
                    ).exists()
                ):
                    settlement.unit.transition_to(Unit.STATUS_VACANT)
                    settlement.unit.save(update_fields=["status", "listing_enabled", "updated_at"])
        except IntegrityError:
            return _problem(409, "Cancellation unavailable", "The settlement changed while it was being cancelled.", "reservation-not-cancellable")
        record_audit_event(
            request,
            "move_in_settlement.cancelled",
            "MoveInSettlement",
            new_values=_move_in_settlement_payload(settlement),
        )
        return JsonResponse({"settlement": _move_in_settlement_payload(settlement)})

    settlement = MoveInSettlement.objects.filter(
        pk=settlement_id,
        unit__property=property_obj,
    ).select_related("unit", "tenant").prefetch_related("payments").first()
    if settlement is None:
        return _problem(404, "Settlement not found", "The settlement is unavailable in the active property.", "settlement-not-found")

    if body.get("sendActivationEmail") is True:
        return _send_move_in_activation(request, property_obj, settlement)

    raw_amount = body.get("amount")
    try:
        amount = Decimal(str(raw_amount))
    except (InvalidOperation, TypeError, ValueError):
        return _problem(400, "Invalid payment", "A positive payment amount is required.", "invalid-payment")
    if not amount.is_finite() or amount <= 0:
        return _problem(400, "Invalid payment", "A positive payment amount is required.", "invalid-payment")
    amount = amount.quantize(Decimal("0.01"))
    reference = body.get("mpesaReference", "")
    if not isinstance(reference, str):
        return _problem(400, "Invalid payment", "The payment reference must be text.", "invalid-payment")
    reference = reference.strip().upper()
    if len(reference) > 100:
        return _problem(400, "Invalid payment", "The payment reference must be 100 characters or fewer.", "invalid-payment")
    try:
        with transaction.atomic():
            settlement = MoveInSettlement.objects.select_for_update().filter(
                pk=settlement_id,
                unit__property=property_obj,
            ).select_related("unit").first()
            if settlement is None:
                return _problem(404, "Settlement not found", "The settlement is unavailable in the active property.", "settlement-not-found")
            if settlement.status in {MoveInSettlement.STATUS_CANCELLED, MoveInSettlement.STATUS_PAID, MoveInSettlement.STATUS_ACTIVE}:
                return _problem(409, "Payment unavailable", "This settlement is already cleared or cancelled.", "settlement-not-payable")
            if amount > settlement.outstanding_balance:
                return _problem(400, "Invalid payment", "Payment exceeds the move-in balance.", "payment-exceeds-balance")
            previous_values = _move_in_settlement_payload(settlement)
            settlement.total_paid += amount
            deposit_target = settlement.deposit_total
            settlement.deposit_paid_amount += min(
                amount,
                max(Decimal("0.00"), deposit_target - settlement.deposit_paid_amount),
            )
            settlement.outstanding_balance = max(
                Decimal("0.00"),
                settlement.total_required - settlement.total_paid,
            )
            if settlement.activated_at:
                settlement.status = MoveInSettlement.STATUS_ACTIVE
            elif settlement.outstanding_balance == 0:
                settlement.status = MoveInSettlement.STATUS_PAID
            elif settlement.activation_override:
                settlement.status = MoveInSettlement.STATUS_MOVE_IN_PENDING
            elif settlement.deposit_paid_amount >= deposit_target:
                settlement.status = MoveInSettlement.STATUS_BOOKED
            else:
                settlement.status = MoveInSettlement.STATUS_PARTIAL
            if settlement.deposit_paid_amount >= deposit_target:
                settlement.booked_at = settlement.booked_at or timezone.now()
                if settlement.unit.status == Unit.STATUS_VACANT:
                    settlement.unit.transition_to(Unit.STATUS_BOOKED)
                    settlement.unit.save(update_fields=["status", "listing_enabled", "updated_at"])
            payment = MoveInPayment.objects.create(
                settlement=settlement,
                amount=amount,
                method=MoveInPayment.METHOD_MANUAL,
                mpesa_reference=reference,
                recorded_by=request.user,
                status=MoveInPayment.STATUS_SUCCESS,
                processed_at=timezone.now(),
            )
            settlement.save(
                update_fields=[
                    "total_paid",
                    "deposit_paid_amount",
                    "outstanding_balance",
                    "status",
                    "booked_at",
                    "updated_at",
                ]
            )
    except IntegrityError:
        return _problem(409, "Payment conflict", "The settlement changed while recording this payment.", "payment-conflict")
    updated = MoveInSettlement.objects.select_related("unit").prefetch_related("payments").get(pk=settlement.pk)
    record_audit_event(
        request,
        "move_in_payment.recorded",
        "MoveInPayment",
        old_values=previous_values,
        new_values={
            "payment_id": payment.pk,
            "settlement_id": settlement.pk,
            "amount": str(amount),
            "reference": reference or None,
            "settlement": _move_in_settlement_payload(updated),
        },
    )
    activation_result = None
    if updated.status == MoveInSettlement.STATUS_PAID:
        activation_response = _send_move_in_activation(request, property_obj, updated)
        activation_result = json.loads(activation_response.content)
        if activation_response.status_code >= 400:
            activation_result["payment_recorded"] = True
            return JsonResponse(activation_result, status=activation_response.status_code)
    return JsonResponse(
        {
            "settlement": _move_in_settlement_payload(updated),
            "bookingConfirmed": updated.deposit_paid_amount >= updated.deposit_total,
            "activationReady": updated.outstanding_balance <= 0,
            "activationEmailSent": (
                activation_result.get("success") if activation_result else None
            ),
        }
    )


def _move_in_charge_code(unit, charge_type, name, effective_date):
    charges = UnitCharge.objects.select_for_update().filter(
        unit=unit,
        charge_type=charge_type,
    )
    if charge_type == UnitCharge.CHARGE_RENT:
        active = list(
            charges.filter(effective_from__lte=effective_date)
            .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=effective_date))
            .order_by("effective_from", "id")
        )
        future = list(charges.filter(effective_from__gt=effective_date).order_by("effective_from", "id"))
    else:
        named = charges.filter(name__iexact=name)
        active = list(
            named.filter(effective_from__lte=effective_date)
            .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=effective_date))
            .order_by("effective_from", "id")
        )
        future = list(named.filter(effective_from__gt=effective_date).order_by("effective_from", "id"))
    if len(active) > 1:
        raise ValidationError(f"Multiple {name} charges are effective on the move-in date.")
    if len({charge.charge_code for charge in future}) > 1:
        raise ValidationError(f"Multiple future {name} charge schedules conflict with the move-in date.")
    if active:
        return active[0].charge_code
    if future:
        return future[0].charge_code
    digest = hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()[:16]
    return f"move-in-{digest}"


def _materialize_move_in_unit_charges(unit, settlement):
    effective_date = settlement.expected_move_in_date
    raw_charge_specs = [
        (UnitCharge.CHARGE_RENT, "Rent", settlement.rent_amount),
        (UnitCharge.CHARGE_RECURRING, "Garbage", settlement.garbage_amount),
    ]
    for item in settlement.utility_charges:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValidationError("The move-in recurring charge snapshot is invalid.")
        try:
            amount = Decimal(str(item.get("amount")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError("The move-in recurring charge snapshot is invalid.") from exc
        if not amount.is_finite() or amount < 0:
            raise ValidationError("The move-in recurring charge snapshot is invalid.")
        raw_charge_specs.append((UnitCharge.CHARGE_RECURRING, item["name"].strip(), amount))

    charge_specs_by_name = {}
    for charge_type, name, amount in raw_charge_specs:
        if not name:
            raise ValidationError("Move-in recurring charges must have a name.")
        key = (charge_type, name.casefold())
        if key in charge_specs_by_name:
            existing_type, existing_name, existing_amount = charge_specs_by_name[key]
            charge_specs_by_name[key] = (existing_type, existing_name, existing_amount + amount)
        else:
            charge_specs_by_name[key] = (charge_type, name, amount)
    audit_events = []
    for charge_type, name, amount in charge_specs_by_name.values():
        charge_code = _move_in_charge_code(unit, charge_type, name, effective_date)
        charges = UnitCharge.objects.select_for_update().filter(
            unit=unit,
            charge_type=charge_type,
            charge_code=charge_code,
        ).order_by("effective_from", "id")
        active = list(
            charges.filter(effective_from__lte=effective_date)
            .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=effective_date))
        )
        if len(active) > 1:
            raise ValidationError(f"Multiple {name} charges overlap the move-in date.")
        if active and active[0].effective_from == effective_date:
            charge = active[0]
            old_values = _charge_payload(charge)
            if charge.name != name or charge.amount != amount:
                charge.name = name
                charge.amount = amount
                charge.full_clean()
                charge.save(update_fields=["name", "amount"])
                audit_events.append((old_values, _charge_payload(charge)))
            continue
        if active:
            previous = active[0]
            old_values = _charge_payload(previous)
            previous.effective_to = effective_date - timedelta(days=1)
            previous.full_clean()
            previous.save(update_fields=["effective_to"])
            audit_events.append((old_values, _charge_payload(previous)))
        next_charge = charges.filter(effective_from__gt=effective_date).order_by("effective_from", "id").first()
        charge = UnitCharge(
            unit=unit,
            charge_type=charge_type,
            charge_code=charge_code,
            name=name,
            amount=amount,
            effective_from=effective_date,
            effective_to=next_charge.effective_from - timedelta(days=1) if next_charge else None,
            metadata={"source": "move_in_settlement", "settlement_id": settlement.pk},
        )
        charge.full_clean()
        charge.save()
        audit_events.append((None, _charge_payload(charge)))
    return audit_events


def _queue_move_in_handover_notification(package, actor, request):
    try:
        with transaction.atomic():
            locked_package = (
                MoveInPackage.objects.select_for_update(of=("self",))
                .filter(pk=package.pk)
                .select_related(
                    "assigned_caretaker__profile",
                    "tenancy__tenant",
                    "tenancy__unit__property",
                )
                .first()
            )
            if locked_package is None:
                raise ValueError("The move-in inspection task is unavailable.")
            if locked_package.caretaker_notification_queued_at:
                return True, None
            caretaker = locked_package.assigned_caretaker
            if caretaker is None:
                raise ValueError("A caretaker must be assigned to the move-in inspection.")
            phone = getattr(getattr(caretaker, "profile", None), "phone", "")
            if not phone:
                raise ValueError("The assigned caretaker has no phone number for SMS.")
            queue_template_sms(
                phone,
                "move_in_handover_assigned",
                {
                    "tenant_name": locked_package.tenancy.tenant.name,
                    "property_name": locked_package.tenancy.unit.property.name,
                    "unit_code": locked_package.tenancy.unit.code,
                    "move_in_date": locked_package.tenancy.start_date.isoformat(),
                },
                actor=actor,
                request=request,
            )
            locked_package.caretaker_notification_queued_at = timezone.now()
            locked_package.caretaker_notification_error = ""
            locked_package.save(
                update_fields=[
                    "caretaker_notification_queued_at",
                    "caretaker_notification_error",
                    "updated_at",
                ]
            )
            return True, None
    except (ImproperlyConfigured, ValueError, RuntimeError, OSError) as exc:
        MoveInPackage.objects.filter(pk=package.pk).update(
            caretaker_notification_error=str(exc)[:500]
        )
        logger.exception("Could not queue move-in caretaker assignment SMS.")
        return False, str(exc)


def _send_move_in_activation(
    request,
    property_obj,
    settlement,
    *,
    assigned_caretaker=None,
    force=False,
    actor=None,
    identity_document=None,
):
    if (
        settlement.status != MoveInSettlement.STATUS_PAID
        or settlement.outstanding_balance > 0
        or settlement.total_paid < settlement.total_required
        or settlement.deposit_paid_amount < settlement.deposit_total
    ):
        return _problem(409, "Activation unavailable", "The full move-in balance and deposits must be paid before sending an activation link.", "activation-not-ready")
    if not settlement.tenant_email or not settlement.expected_move_in_date:
        return _problem(409, "Activation unavailable", "A tenant email and expected move-in date are required.", "activation-details-required")
    if not settings.PUBLIC_SITE_URL:
        return _problem(503, "Activation unavailable", "PUBLIC_SITE_URL must be configured before issuing tenant activation links.", "activation-url-not-configured")

    user_model = get_user_model()
    actor = actor or request.user
    token = secrets.token_urlsafe(32)
    activation_code = _new_tenant_activation_code()
    now = timezone.now()
    created_tenant = False
    created_tenancy = False
    created_deposit_account = False
    charge_audit_events = []
    deposit_entry = None
    move_in_accounting = None
    move_in_document_keys = []
    try:
        with transaction.atomic():
            locked = MoveInSettlement.objects.select_for_update(of=("self",)).filter(
                pk=settlement.pk,
                unit__property=property_obj,
            ).select_related("unit", "tenant", "unit__property").first()
            if locked is None:
                return _problem(404, "Settlement not found", "The settlement is unavailable in the active property.", "settlement-not-found")
            if (
                locked.status != MoveInSettlement.STATUS_PAID
                or locked.outstanding_balance > 0
                or locked.deposit_paid_amount < locked.deposit_total
            ):
                return _problem(409, "Activation unavailable", "The full move-in balance and deposits must be paid before sending an activation link.", "activation-not-ready")
            if (
                locked.activation_email_sent_at
                and locked.activation_sms_queued_at
                and not force
            ):
                tenancy = Tenancy.objects.filter(
                    tenant=locked.tenant,
                    unit=locked.unit,
                    status=Tenancy.STATUS_ACTIVE,
                ).first()
                if tenancy is None:
                    return _problem(409, "Activation unavailable", "The activated move-in tenancy could not be found.", "tenancy-not-found")
                package = MoveInPackage.objects.filter(tenancy=tenancy).first()
                if package is None or (
                    assigned_caretaker
                    and package.assigned_caretaker_id != assigned_caretaker.pk
                ):
                    return _problem(
                        409,
                        "Inspection assignment changed",
                        "The assigned caretaker does not match the saved move-in inspection task.",
                        "move-in-caretaker-assignment-conflict",
                    )
                if assigned_caretaker:
                    caretaker_sms_queued, caretaker_sms_error = (
                        _queue_move_in_handover_notification(package, actor, request)
                    )
                else:
                    caretaker_sms_queued, caretaker_sms_error = None, None
                return JsonResponse(
                    {
                        "success": caretaker_sms_queued is not False,
                        "message": f"Activation email and SMS were already sent to {locked.tenant_email} and {locked.tenant_phone}.",
                        "activation_email_sent": True,
                        "activation_sms_queued": True,
                        "activation_email_error": None,
                        "activation_sms_error": None,
                        "move_in_caretaker_sms_queued": caretaker_sms_queued,
                        "move_in_caretaker_sms_error": caretaker_sms_error,
                        "move_in_caretaker_id": package.assigned_caretaker_id,
                        "settlement": {
                            "id": locked.pk,
                            "activation_email_sent_at": locked.activation_email_sent_at.isoformat(),
                            "activation_email_error": None,
                            "activation_sms_queued_at": locked.activation_sms_queued_at.isoformat(),
                            "activation_sms_error": None,
                        },
                        "tenant": _tenant_payload(locked.tenant),
                        "tenancy": _tenancy_payload(tenancy),
                    },
                    status=502 if caretaker_sms_queued is False else 200,
                )

            tenant = locked.tenant
            if tenant is None:
                if user_model.objects.filter(
                    Q(email__iexact=locked.tenant_email)
                    | Q(username__iexact=locked.tenant_email)
                ).exists():
                    return _problem(409, "Email already registered", "This email is already linked to another account.", "email-already-registered")
                if Tenant.objects.filter(email__iexact=locked.tenant_email).exists():
                    return _problem(409, "Email already registered", "This email is already linked to a tenant record.", "email-already-registered")
                tenant = Tenant(
                    branch=locked.unit.property.branch,
                    tenant_type=locked.tenant_type,
                    name=locked.tenant_name,
                    company_registration_number=locked.company_registration_number,
                    contact_person=locked.contact_person,
                    national_id_or_passport=locked.national_id_or_passport,
                    kra_pin=locked.kra_pin,
                    occupation=locked.occupation,
                    employer=locked.employer,
                    next_of_kin_name=locked.next_of_kin_name,
                    next_of_kin_phone=locked.next_of_kin_phone,
                    next_of_kin_relationship=locked.next_of_kin_relationship,
                    whatsapp_opted_in_at=timezone.now() if locked.whatsapp_opt_in else None,
                    phone=locked.tenant_phone,
                    email=locked.tenant_email,
                    is_provisional=True,
                )
                tenant.full_clean()
                tenant.save()
                created_tenant = True
                locked.tenant = tenant
            elif tenant.portal_user_id or not tenant.is_provisional:
                return _problem(409, "Activation unavailable", "This reservation is already linked to an activated tenant account.", "tenant-already-activated")

            tenancy = Tenancy.objects.filter(
                unit=locked.unit,
                tenant=tenant,
                status=Tenancy.STATUS_ACTIVE,
            ).first()
            if tenancy is None:
                if Tenancy.objects.select_for_update().filter(
                    unit=locked.unit,
                    status=Tenancy.STATUS_ACTIVE,
                ).exists():
                    return _problem(409, "Unit unavailable", "This unit already has an active tenancy.", "unit-already-occupied")
                tenancy = Tenancy(
                    unit=locked.unit,
                    tenant=tenant,
                    tenancy_type=locked.tenancy_type,
                    start_date=locked.expected_move_in_date,
                    end_date=locked.tenancy_end_date,
                    due_day=locked.tenancy_due_day or locked.unit.property.rent_due_day,
                    grace_period_days=0,
                    notice_period_days=30,
                )
                tenancy.full_clean()
                tenancy.save()
                created_tenancy = True
                unit = Unit.objects.select_for_update().get(pk=locked.unit_id)
                target_status = (
                    Unit.STATUS_OCCUPIED
                    if locked.expected_move_in_date <= timezone.localdate()
                    else Unit.STATUS_BOOKED
                )
                if unit.status != target_status:
                    unit.transition_to(target_status)
                    unit.save(update_fields=["status", "listing_enabled", "updated_at"])

                deposit_target = locked.deposit_total
                account = DepositAccount.objects.create(
                    tenancy=tenancy,
                    target_amount=deposit_target,
                    balance=locked.deposit_paid_amount,
                )
                created_deposit_account = True
                if locked.deposit_paid_amount > 0:
                    deposit_entry = DepositEntry.objects.create(
                        account=account,
                        entry_type=DepositEntry.TYPE_RECEIPT,
                        amount=locked.deposit_paid_amount,
                        reference=f"MOVEIN-{locked.pk}",
                        description="Move-in security and utility deposit receipt",
                        created_by=actor,
                    )

            locked.save(update_fields=["tenant", "updated_at"])
            charge_audit_events = _materialize_move_in_unit_charges(locked.unit, locked)
            move_in_accounting = _materialize_move_in_payment(
                tenancy,
                locked,
                actor,
                move_in_document_keys,
                identity_document,
                assigned_caretaker,
            )
            TenantActivationChallenge.objects.filter(
                tenant=tenant,
                unit=locked.unit,
                used_at__isnull=True,
            ).update(used_at=now)
            challenge = TenantActivationChallenge.objects.create(
                tenant=tenant,
                unit=locked.unit,
                token_hash=_activation_hash(token),
                code_hash=_activation_hash(activation_code),
                phone_verification_required=True,
                expires_at=now + timedelta(hours=48),
            )
    except (IntegrityError, ValidationError, ValueError) as exc:
        _cleanup_tenant_evidence(move_in_document_keys)
        detail = (
            "; ".join(exc.messages)
            if isinstance(exc, ValidationError)
            else str(exc)
            if isinstance(exc, ValueError)
            else "The activation could not be prepared because the tenant or unit conflicts with existing records."
        )
        return _problem(409, "Activation unavailable", detail, "activation-conflict")
    except (BotoCoreError, ClientError, ImproperlyConfigured, OSError) as exc:
        _cleanup_tenant_evidence(move_in_document_keys)
        logger.exception("Could not store move-in documents (%s).", type(exc).__name__)
        return _problem(
            503,
            "Move-in documents unavailable",
            "The agreement and payment receipt could not be stored securely.",
            "move-in-document-storage-unavailable",
        )
    except DatabaseError:
        _cleanup_tenant_evidence(move_in_document_keys)
        logger.exception("Could not save move-in accounting or document records.")
        return _problem(
            503,
            "Move-in records unavailable",
            "The move-in accounting records could not be saved.",
            "move-in-records-unavailable",
        )

    if created_tenant:
        record_audit_event(
            request,
            "tenant.created",
            "Tenant",
            new_values={
                "id": tenant.pk,
                "branch_id": tenant.branch_id,
                "name": tenant.name,
                "email": tenant.email,
                "phone": tenant.phone,
                "is_provisional": True,
            },
        )
    if created_tenancy:
        record_audit_event(
            request,
            "tenancy.created",
            "Tenancy",
            new_values=_tenancy_payload(tenancy),
        )
    for old_values, new_values in charge_audit_events:
        record_audit_event(
            request,
            "unit_charge.created" if old_values is None else "unit_charge.updated",
            "UnitCharge",
            old_values=old_values,
            new_values=new_values,
        )
    if created_deposit_account:
        record_audit_event(
            request,
            "deposit.account_created",
            "DepositAccount",
            new_values={
                "id": account.pk,
                "tenancy_id": tenancy.pk,
                "target_amount": str(account.target_amount),
                "balance": str(account.balance),
            },
        )
    if deposit_entry:
        record_audit_event(
            request,
            "deposit.entry_created",
            "DepositEntry",
            new_values={
                "id": deposit_entry.pk,
                "tenancy_id": tenancy.pk,
                "entry_type": deposit_entry.entry_type,
                "amount": str(deposit_entry.amount),
                "balance": str(account.balance),
            },
        )
    if move_in_accounting:
        if move_in_accounting["package_created"]:
            record_audit_event(
                request,
                "move_in_package.created",
                "MoveInPackage",
                new_values=_move_in_package_payload(move_in_accounting["package"]),
            )
        if move_in_accounting["invoice_created"]:
            record_audit_event(
                request,
                "invoice.move_in_issued",
                "Invoice",
                new_values=_invoice_payload(move_in_accounting["invoice"]),
            )
        if move_in_accounting["payment_created"]:
            record_audit_event(
                request,
                "payment.recorded",
                "Payment",
                new_values=_payment_payload(move_in_accounting["payment"]),
            )
        if move_in_accounting["allocation_created"]:
            record_audit_event(
                request,
                "payment.manually_allocated",
                "Payment",
                new_values={
                    "id": move_in_accounting["payment"].pk,
                    "invoice_id": move_in_accounting["invoice"].pk,
                    "amount": str(move_in_accounting["payment"].amount),
                },
            )
    record_audit_event(
        request,
        "tenant_activation.issued",
        "TenantActivationChallenge",
        new_values={
            "id": challenge.pk,
            "tenant_id": tenant.pk,
            "tenancy_id": tenancy.pk,
            "settlement_id": locked.pk,
            "expires_at": challenge.expires_at.isoformat(),
        },
    )
    activation_url = f"{settings.PUBLIC_SITE_URL.rstrip('/')}/activate?token={token}"
    email_error = None
    try:
        _send_tenant_activation_email(
            locked.tenant_name,
            locked.tenant_email,
            locked.unit.code,
            property_obj.name,
            activation_url,
            activation_code,
            phone_verification_required=True,
        )
    except (ImproperlyConfigured, RuntimeError, smtplib.SMTPException, OSError) as exc:
        email_error = str(exc)
        locked.activation_email_error = str(exc)
        challenge.email_delivery_error = str(exc)[:500]
        challenge.save(update_fields=["email_delivery_error"])
        locked.save(update_fields=["activation_email_error", "updated_at"])
        logger.error("Could not deliver a move-in activation email (%s).", type(exc).__name__)
    else:
        locked.activation_email_sent_at = timezone.now()
        locked.activation_email_error = ""
        locked.save(update_fields=["activation_email_sent_at", "activation_email_error", "updated_at"])
        challenge.email_sent_at = locked.activation_email_sent_at
        challenge.email_delivery_error = ""
        challenge.save(update_fields=["email_sent_at", "email_delivery_error"])

    sms_error = None
    try:
        queue_template_sms(
            locked.tenant_phone,
            "tenant_activation",
            {
                "tenant_name": locked.tenant_name,
                "activation_url": activation_url,
                "code": activation_code,
            },
            actor=actor,
            request=request,
            expires_at=challenge.expires_at,
        )
    except (ImproperlyConfigured, RuntimeError, ValueError, OSError) as exc:
        sms_error = str(exc)
        locked.activation_sms_error = sms_error
        locked.save(update_fields=["activation_sms_error", "updated_at"])
        logger.error("Could not queue a move-in activation SMS (%s).", type(exc).__name__)
    else:
        locked.activation_sms_queued_at = timezone.now()
        locked.activation_sms_error = ""
        locked.save(update_fields=["activation_sms_queued_at", "activation_sms_error", "updated_at"])

    if move_in_accounting["package"].assigned_caretaker_id:
        caretaker_sms_queued, caretaker_sms_error = _queue_move_in_handover_notification(
            move_in_accounting["package"],
            actor,
            request,
        )
    else:
        caretaker_sms_queued, caretaker_sms_error = None, None
    activation_email_sent = email_error is None
    activation_sms_queued = sms_error is None
    all_messages_queued = (
        activation_email_sent
        and activation_sms_queued
        and caretaker_sms_queued is not False
    )
    message = (
        "Tenant activation and move-in inspection SMS messages are queued."
        if all_messages_queued
        else "Tenant account is ready, but one or more activation or inspection messages could not be queued."
    )
    activation_result = {
        "success": all_messages_queued,
        "message": message,
        "activation_email_sent": activation_email_sent,
        "activation_email_error": email_error,
        "activation_sms_queued": activation_sms_queued,
        "activation_sms_error": sms_error,
        "move_in_caretaker_sms_queued": caretaker_sms_queued,
        "move_in_caretaker_sms_error": caretaker_sms_error,
        "move_in_caretaker_id": move_in_accounting["package"].assigned_caretaker_id,
        "tenant": _tenant_payload(tenant),
        "tenancy": _tenancy_payload(tenancy),
        "settlement": {
            "id": locked.pk,
            "activation_email_sent_at": (
                locked.activation_email_sent_at.isoformat()
                if locked.activation_email_sent_at
                else None
            ),
            "activation_email_error": email_error,
            "activation_sms_queued_at": (
                locked.activation_sms_queued_at.isoformat()
                if locked.activation_sms_queued_at
                else None
            ),
            "activation_sms_error": sms_error,
        },
    }
    if move_in_accounting:
        activation_result["invoice"] = _invoice_payload(
            move_in_accounting["invoice"]
        )
        activation_result["receipt"] = _receipt_payload(
            move_in_accounting["receipt"]
        )
    return JsonResponse(
        activation_result,
        status=200 if all_messages_queued else 502,
    )


def _move_in_first_month_amount(monthly_amount, move_in_date):
    month_days = calendar.monthrange(move_in_date.year, move_in_date.month)[1]
    billable_days = month_days - move_in_date.day + 1
    return (
        Decimal(str(monthly_amount))
        / Decimal(month_days)
        * Decimal(billable_days)
    ).quantize(Decimal("0.01"))


def _move_in_onboarding_quote(unit, expected_move_in_date):
    rent_charge = _current_rent(unit, expected_move_in_date)
    if rent_charge is not None:
        rent_amount = rent_charge.amount
    elif unit.unit_type_definition_id:
        rent_amount = unit.unit_type_definition.monthly_rent
    else:
        rent_amount = unit.property.default_rent
    rent_amount = Decimal(str(rent_amount)).quantize(Decimal("0.01"))
    month_days = calendar.monthrange(
        expected_move_in_date.year,
        expected_move_in_date.month,
    )[1]
    billable_days = month_days - expected_move_in_date.day + 1

    security_deposit = (
        unit.unit_type_definition.security_deposit
        if unit.unit_type_definition_id
        else unit.property.security_deposit
    )
    security_deposit = Decimal(str(security_deposit or 0)).quantize(Decimal("0.01"))

    utility_deposits = []
    configured_water_deposit = False
    if unit.unit_type_definition_id:
        for utility in unit.unit_type_definition.utilities.all():
            amount = utility.deposit_amount
            if amount is None or amount <= 0:
                continue
            utility_deposits.append(
                {
                    "name": f"{utility.name} deposit",
                    "amount": amount.quantize(Decimal("0.01")),
                    "utility_type": utility.utility_type,
                }
            )
            configured_water_deposit = configured_water_deposit or (
                utility.utility_type == PropertyUnitTypeUtility.UTILITY_WATER
            )
    if not configured_water_deposit and unit.property.water_deposit > 0:
        utility_deposits.append(
            {
                "name": "Water deposit",
                "amount": unit.property.water_deposit.quantize(Decimal("0.01")),
                "utility_type": PropertyUnitTypeUtility.UTILITY_WATER,
            }
        )

    monthly_charges_by_name = {}
    for charge in PropertyRecurringCharge.objects.filter(
        property=unit.property,
        is_active=True,
    ).order_by("name", "id"):
        monthly_charges_by_name.setdefault(
            charge.name.casefold(),
            {"name": charge.name, "amount": charge.amount.quantize(Decimal("0.01"))},
        )
    if unit.unit_type_definition_id:
        for utility in unit.unit_type_definition.utilities.all():
            if (
                utility.billing_method == PropertyUnitTypeUtility.METHOD_FIXED
                and utility.fixed_amount is not None
                and utility.fixed_amount > 0
            ):
                monthly_charges_by_name.setdefault(
                    utility.name.casefold(),
                    {
                        "name": utility.name,
                        "amount": utility.fixed_amount.quantize(Decimal("0.01")),
                    },
                )

    utility_deposit_amount = sum(
        (deposit["amount"] for deposit in utility_deposits),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    garbage_amount = Decimal(str(unit.property.default_garbage_charge or 0)).quantize(
        Decimal("0.01")
    )
    line_items = [
        {
            "name": "First month rent",
            "amount": _move_in_first_month_amount(rent_amount, expected_move_in_date),
            "category": "rent",
        },
        {"name": "Security deposit", "amount": security_deposit, "category": "deposit"},
        *[
            {
                "name": deposit["name"],
                "amount": deposit["amount"],
                "category": "utility_deposit",
            }
            for deposit in utility_deposits
        ],
    ]
    total_required = sum(
        (item["amount"] for item in line_items),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    quote = {
        "unit_id": unit.pk,
        "property_id": unit.property_id,
        "start_date": expected_move_in_date.isoformat(),
        "proration": {
            "method": "actual_calendar_days",
            "month_days": month_days,
            "billable_days": billable_days,
            "move_in_day_included": True,
        },
        "items": [
            {
                "name": item["name"],
                "amount": str(item["amount"]),
                "category": item["category"],
            }
            for item in line_items
        ],
        "total": str(total_required),
    }
    quote["fingerprint"] = hashlib.sha256(
        json.dumps(quote, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "quote": quote,
        "rent_amount": rent_amount,
        "security_deposit": security_deposit,
        "garbage_amount": garbage_amount,
        "letting_fee_amount": Decimal("0.00"),
        "utility_deposits": utility_deposits,
        "utility_deposit_amount": utility_deposit_amount,
        "total_required": total_required,
        "utility_charges": [
            {"name": charge["name"], "amount": str(charge["amount"])}
            for charge in monthly_charges_by_name.values()
        ],
    }


def _property_officer_onboarding_error(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    _, error = _property_officer_membership(request.user)
    return error


def _tenant_document_file_details(upload):
    if upload is None:
        return None, "A document file is required."
    if upload.size <= 0 or upload.size > 10 * 1024 * 1024:
        return None, "Documents must be from 1 byte to 10 MB."
    try:
        header = upload.read(12)
        upload.seek(0)
    except OSError:
        logger.exception("Could not read uploaded tenant document.")
        return None, "The uploaded document could not be read."
    signatures = (
        (b"%PDF-", "application/pdf", "pdf"),
        (b"\xff\xd8\xff", "image/jpeg", "jpg"),
        (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    )
    match = next(
        (
            (content_type, extension)
            for signature, content_type, extension in signatures
            if header.startswith(signature)
        ),
        None,
    )
    if match is None and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        match = ("image/webp", "webp")
    if match is None:
        return None, "Documents must be PDF, JPEG, PNG, or WebP files."
    return match, None


@require_GET
def property_officer_move_in_quote(request):
    error = _property_officer_onboarding_error(request)
    if error:
        return error
    try:
        unit_id = int(request.GET.get("unit_id", ""))
        expected_move_in_date = date.fromisoformat(
            request.GET.get("start_date") or timezone.localdate().isoformat()
        )
    except (TypeError, ValueError):
        return _problem(400, "Invalid quote request", "A valid unit_id and start_date are required.", "invalid-quote-request")
    if expected_move_in_date < timezone.localdate():
        return _problem(400, "Invalid move-in date", "The move-in date must be today or later.", "invalid-move-in-date")

    unit = (
        Unit.objects.filter(
            pk=unit_id,
            property__in=_accessible_properties(request.user),
            status=Unit.STATUS_VACANT,
        )
        .select_related("property", "unit_type_definition")
        .prefetch_related("unit_type_definition__utilities")
        .first()
    )
    if unit is None:
        return _problem(404, "Unit not found", "The vacant unit is unavailable or outside your access scope.", "unit-not-found")
    quote = _move_in_onboarding_quote(unit, expected_move_in_date)
    if quote["rent_amount"] <= 0:
        return _problem(409, "Rent not configured", "Configure a positive rent amount for this unit before onboarding a tenant.", "rent-not-configured")
    return JsonResponse({"quote": quote["quote"]})


@require_POST
def property_officer_tenant_onboarding(request):
    error = _property_officer_onboarding_error(request)
    if error:
        return error
    if request.content_type == "multipart/form-data":
        body = request.POST.dict()
        body["confirm_paid"] = body.get("confirm_paid") == "true"
        body["whatsapp_opt_in"] = body.get("whatsapp_opt_in", "false") == "true"
    else:
        body = _json_body(request)
    if body is None:
        return _problem(
            400,
            "Invalid request",
            "Submit tenant details and the national ID/passport in a multipart form.",
            "invalid-request",
        )
    if body.get("confirm_paid") is not True:
        return _problem(400, "Payment confirmation required", "Confirm that the full move-in amount has been paid before onboarding.", "payment-confirmation-required")
    if isinstance(body.get("unit_id"), bool) or isinstance(body.get("due_day"), bool):
        return _problem(400, "Invalid onboarding request", "unit_id and due_day must be numeric values.", "invalid-onboarding-request")

    try:
        unit_id = int(body.get("unit_id"))
        caretaker_id = int(body.get("caretaker_id"))
        expected_move_in_date = date.fromisoformat(str(body.get("start_date") or ""))
        due_day = int(body.get("due_day"))
        tenancy_end_date = (
            date.fromisoformat(str(body["end_date"]))
            if body.get("end_date")
            else None
        )
        submitted_total = Decimal(str(body.get("quote_total")))
        payment_amount = Decimal(str(body.get("payment_amount")))
        payment_date = date.fromisoformat(str(body.get("payment_date") or ""))
    except (TypeError, ValueError, InvalidOperation):
        return _problem(400, "Invalid onboarding request",         "A valid unit, caretaker, move-in date, rent due day, quote, payment amount, and payment date are required.", "invalid-onboarding-request")
    if expected_move_in_date < timezone.localdate():
        return _problem(400, "Invalid move-in date", "The move-in date must be today or later.", "invalid-move-in-date")
    if not 1 <= due_day <= 31:
        return _problem(400, "Invalid rent due day", "The rent due day must be between 1 and 31.", "invalid-rent-due-day")
    tenancy_type = body.get("tenancy_type")
    if tenancy_type not in dict(Tenancy.TENANCY_TYPE_CHOICES):
        return _problem(400, "Invalid tenancy type", "Choose a supported tenancy type.", "invalid-tenancy-type")
    if tenancy_type == Tenancy.TYPE_FIXED_TERM and tenancy_end_date is None:
        return _problem(400, "Invalid tenancy dates", "Fixed-term tenancies require an end date.", "invalid-tenancy-dates")
    if tenancy_end_date and tenancy_end_date < expected_move_in_date:
        return _problem(400, "Invalid tenancy dates", "The tenancy end date cannot be before its start date.", "invalid-tenancy-dates")

    tenant_type = body.get("tenant_type")
    if tenant_type not in dict(Tenant.TENANT_TYPE_CHOICES):
        return _problem(400, "Invalid tenant type", "Choose individual or company.", "invalid-tenant-type")
    required_fields = ("name", "email", "phone")
    if any(not isinstance(body.get(field), str) or not body[field].strip() for field in required_fields):
        return _problem(400, "Invalid tenant details", "Name, email, and phone are required.", "invalid-tenant-details")
    name = body["name"].strip()
    email = body["email"].strip().lower()
    try:
        validate_email(email)
        phone = _normalize_ke_phone(body["phone"].strip())
    except (ValidationError, ValueError) as exc:
        return _problem(400, "Invalid tenant details", str(exc), "invalid-tenant-details")
    national_id_or_passport = str(body.get("national_id_or_passport") or "").strip()
    company_registration_number = str(body.get("company_registration_number") or "").strip()
    contact_person = str(body.get("contact_person") or "").strip()
    kra_pin = str(body.get("kra_pin") or "").strip()
    occupation = str(body.get("occupation") or "").strip()
    employer = str(body.get("employer") or "").strip()
    next_of_kin_name = str(body.get("next_of_kin_name") or "").strip()
    next_of_kin_relationship = str(body.get("next_of_kin_relationship") or "").strip()
    next_of_kin_phone = str(body.get("next_of_kin_phone") or "").strip()
    identity_upload = request.FILES.get("identity_document")
    if identity_upload is None:
        return _problem(
            400,
            "Identity document required",
            "Upload the tenant's national ID/passport or the company contact person's document before completing onboarding.",
            "identity-document-required",
        )
    identity_file_details, identity_file_error = _tenant_document_file_details(
        identity_upload
    )
    if identity_file_error:
        return _problem(
            400,
            "Invalid identity document",
            identity_file_error,
            "invalid-identity-document",
        )
    if identity_file_details is None:
        return _problem(
            400,
            "Invalid identity document",
            "The identity document file type could not be determined.",
            "invalid-identity-document",
        )
    if not identity_upload.name or len(identity_upload.name) > 255:
        return _problem(
            400,
            "Invalid identity document",
            "The identity document filename must be at most 255 characters.",
            "invalid-identity-document",
        )
    identity_document = (
        identity_upload,
        identity_file_details[0],
        identity_file_details[1],
    )
    transaction_reference = str(body.get("transaction_reference") or "").strip()
    payment_channel = body.get("payment_channel")
    whatsapp_opt_in = body.get("whatsapp_opt_in", False)
    if whatsapp_opt_in not in (True, False):
        return _problem(400, "Invalid tenant details", "whatsapp_opt_in must be true or false.", "invalid-tenant-details")
    if next_of_kin_phone:
        try:
            next_of_kin_phone = _normalize_ke_phone(next_of_kin_phone)
        except (ValidationError, ValueError) as exc:
            return _problem(400, "Invalid next-of-kin phone", str(exc), "invalid-next-of-kin")
    if any(len(value) > limit for value, limit in (
        (occupation, 150),
        (employer, 255),
        (next_of_kin_name, 255),
        (next_of_kin_relationship, 100),
        (transaction_reference, 100),
    )):
        return _problem(400, "Invalid onboarding details", "Occupation, employer, next-of-kin, and payment reference exceed their allowed lengths.", "invalid-onboarding-details")
    if payment_channel not in dict(MoveInPayment.CHANNEL_CHOICES):
        return _problem(400, "Invalid payment channel", "Choose M-Pesa, bank transfer, or cash.", "invalid-payment-channel")
    if not transaction_reference:
        return _problem(400, "Invalid payment reference", "A unique transaction or office receipt reference is required.", "invalid-payment-reference")
    if payment_date > timezone.localdate():
        return _problem(400, "Invalid payment date", "The payment date cannot be in the future.", "invalid-payment-date")
    if not national_id_or_passport:
        return _problem(
            400,
            "Invalid tenant details",
            "A national ID or passport number is required for the tenant or company contact person.",
            "invalid-tenant-details",
        )
    if tenant_type == Tenant.COMPANY and not company_registration_number:
        return _problem(400, "Invalid tenant details", "A company registration number is required for a company tenant.", "invalid-tenant-details")
    if tenant_type == Tenant.COMPANY and not contact_person:
        return _problem(400, "Invalid tenant details", "A contact person is required for a company tenant.", "invalid-tenant-details")

    unit = (
        Unit.objects.filter(
            pk=unit_id,
            property__in=_accessible_properties(request.user),
        )
        .select_related("property", "unit_type_definition")
        .prefetch_related("unit_type_definition__utilities")
        .first()
    )
    if unit is None:
        return _problem(404, "Unit not found", "The unit is unavailable or outside your access scope.", "unit-not-found")
    assigned_caretaker = (
        get_user_model()
        .objects.filter(
            pk=caretaker_id,
            is_active=True,
            groups__name=CARETAKER_ROLE,
            property_caretaker_assignments__property=unit.property,
            property_caretaker_assignments__ended_at__isnull=True,
        )
        .select_related("profile")
        .distinct()
        .first()
    )
    if assigned_caretaker is None:
        return _problem(
            400,
            "Invalid caretaker",
            "Choose an active caretaker assigned to this property.",
            "invalid-move-in-caretaker",
        )
    caretaker_phone = getattr(
        getattr(assigned_caretaker, "profile", None),
        "phone",
        "",
    )
    if not caretaker_phone:
        return _problem(
            400,
            "Caretaker phone required",
            "The assigned caretaker must have a phone number to receive the move-in SMS.",
            "move-in-caretaker-phone-required",
        )
    existing = MoveInSettlement.objects.filter(
        unit=unit,
        status__in=MoveInSettlement.OPEN_STATUSES,
    ).select_related("tenant").first()
    if unit.status != Unit.STATUS_VACANT and not (
        existing and existing.tenant_email.casefold() == email.casefold()
    ):
        return _problem(409, "Unit unavailable", "Only a vacant unit can be selected for onboarding.", "unit-unavailable")

    quote_values = _move_in_onboarding_quote(unit, expected_move_in_date)
    activation_retry = bool(existing and existing.tenant_id)
    if activation_retry:
        existing_package = MoveInPackage.objects.filter(
            tenancy__tenant=existing.tenant,
            tenancy__unit=unit,
        ).only("assigned_caretaker_id").first()
        if (
            existing_package
            and existing_package.assigned_caretaker_id != assigned_caretaker.pk
        ):
            return _problem(
                409,
                "Inspection assignment changed",
                "The move-in inspection is already assigned to another caretaker.",
                "move-in-caretaker-assignment-conflict",
            )
    if not activation_retry and quote_values["rent_amount"] <= 0:
        return _problem(409, "Rent not configured", "Configure a positive rent amount for this unit before onboarding a tenant.", "rent-not-configured")
    if not submitted_total.is_finite() or submitted_total < 0:
        return _problem(400, "Invalid move-in amount", "quote_total must be a non-negative number.", "invalid-move-in-amount")
    if not payment_amount.is_finite() or payment_amount <= 0:
        return _problem(400, "Invalid payment amount", "payment_amount must be a positive amount.", "invalid-payment-amount")
    try:
        submitted_total = submitted_total.quantize(Decimal("0.01"))
        payment_amount = payment_amount.quantize(Decimal("0.01"))
    except InvalidOperation:
        return _problem(400, "Invalid move-in amount", "The quote and payment must be valid currency amounts.", "invalid-move-in-amount")
    if not activation_retry:
        if submitted_total != quote_values["total_required"]:
            return _problem(409, "Move-in quote changed", "The move-in amount changed. Refresh the quote and confirm payment again.", "move-in-quote-changed")
        if payment_amount != quote_values["total_required"]:
            return _problem(400, "Payment amount mismatch", "Record the full move-in total before onboarding.", "payment-amount-mismatch")
        if body.get("quote_fingerprint") != quote_values["quote"]["fingerprint"]:
            return _problem(409, "Move-in quote changed", "The move-in charges changed. Refresh the quote and confirm payment again.", "move-in-quote-changed")

    if existing:
        expected_details = (
            existing.tenant_name == name
            and existing.tenant_email.casefold() == email.casefold()
            and existing.tenant_phone == phone
            and existing.tenant_type == tenant_type
            and existing.national_id_or_passport == national_id_or_passport
            and existing.company_registration_number == company_registration_number
            and existing.contact_person == contact_person
            and existing.kra_pin == kra_pin
            and existing.occupation == occupation
            and existing.employer == employer
            and existing.next_of_kin_name == next_of_kin_name
            and existing.next_of_kin_phone == next_of_kin_phone
            and existing.next_of_kin_relationship == next_of_kin_relationship
            and existing.whatsapp_opt_in == whatsapp_opt_in
            and existing.expected_move_in_date == expected_move_in_date
            and existing.tenancy_type == tenancy_type
            and existing.tenancy_end_date == tenancy_end_date
            and existing.tenancy_due_day == due_day
            and existing.total_required
            == (
                submitted_total
                if activation_retry
                else quote_values["total_required"]
            )
        )
        if not expected_details:
            return _problem(409, "Onboarding already started", "This unit already has an open move-in record with different tenant or tenancy details.", "onboarding-already-started")
        settlement = existing
    else:
        user_model = get_user_model()
        if (
            Tenant.objects.filter(email__iexact=email).exists()
            or user_model.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exists()
        ):
            return _problem(409, "Email already registered", "This email is already linked to an account. Use a different email or the tenant transfer workflow.", "email-already-registered")
        if MoveInSettlement.objects.filter(
            tenant_email__iexact=email,
            status__in=MoveInSettlement.OPEN_STATUSES,
        ).exists():
            return _problem(409, "Onboarding already started", "This email already has an open move-in record.", "onboarding-already-started")
        total_required = quote_values["total_required"]
        deposit_paid_amount = (
            quote_values["security_deposit"] + quote_values["utility_deposit_amount"]
        ).quantize(Decimal("0.01"))
        settlement = MoveInSettlement(
            unit=unit,
            tenant_name=name,
            tenant_email=email,
            tenant_phone=phone,
            tenant_type=tenant_type,
            national_id_or_passport=national_id_or_passport,
            company_registration_number=company_registration_number,
            contact_person=contact_person,
            kra_pin=kra_pin,
            occupation=occupation,
            employer=employer,
            next_of_kin_name=next_of_kin_name,
            next_of_kin_phone=next_of_kin_phone,
            next_of_kin_relationship=next_of_kin_relationship,
            whatsapp_opt_in=whatsapp_opt_in,
            tenancy_type=tenancy_type,
            tenancy_end_date=tenancy_end_date,
            tenancy_due_day=due_day,
            rent_amount=quote_values["rent_amount"],
            deposit_amount=quote_values["security_deposit"],
            garbage_amount=quote_values["garbage_amount"],
            move_in_letting_fee=quote_values["letting_fee_amount"],
            utility_deposit_amount=quote_values["utility_deposit_amount"],
            utility_deposits=[
                {
                    "name": deposit["name"],
                    "amount": str(deposit["amount"]),
                }
                for deposit in quote_values["utility_deposits"]
            ],
            utility_charges=quote_values["utility_charges"],
            total_required=total_required,
            total_paid=total_required,
            deposit_paid_amount=deposit_paid_amount,
            outstanding_balance=Decimal("0.00"),
            expected_move_in_date=expected_move_in_date,
            status=MoveInSettlement.STATUS_PAID,
            booked_at=timezone.now(),
        )
        try:
            with transaction.atomic():
                locked_unit = Unit.objects.select_for_update().get(pk=unit.pk)
                if locked_unit.status != Unit.STATUS_VACANT:
                    return _problem(409, "Unit unavailable", "This unit is no longer vacant.", "unit-unavailable")
                if MoveInSettlement.objects.select_for_update().filter(
                    unit=locked_unit,
                    status__in=MoveInSettlement.OPEN_STATUSES,
                ).exists():
                    return _problem(409, "Onboarding already started", "This unit already has an open move-in record.", "onboarding-already-started")
                settlement.full_clean()
                settlement.save()
                confirmation_payment = MoveInPayment.objects.create(
                    settlement=settlement,
                    amount=total_required,
                    method=MoveInPayment.METHOD_OFFICER_CONFIRMATION,
                    payment_channel=payment_channel,
                    transaction_reference=transaction_reference,
                    payment_date=payment_date,
                    mpesa_reference=(
                        transaction_reference
                        if payment_channel == MoveInPayment.CHANNEL_MPESA
                        else ""
                    ),
                    recorded_by=request.user,
                    status=MoveInPayment.STATUS_SUCCESS,
                    result_description=(
                        "Property officer recorded the full move-in payment during tenant onboarding."
                    ),
                    processed_at=timezone.now(),
                )
        except IntegrityError:
            return _problem(409, "Onboarding already started", "The unit or tenant already has an open move-in record.", "onboarding-already-started")

        record_audit_event(
            request,
            "move_in_settlement.created",
            "MoveInSettlement",
            new_values=_move_in_settlement_payload(settlement),
        )
        record_audit_event(
            request,
            "move_in.payment_confirmed",
            "MoveInSettlement",
            new_values={
                "settlement_id": settlement.pk,
                "unit_id": unit.pk,
                "confirmed_total": str(total_required),
                "confirmed_by": request.user.pk,
                "payment_channel": payment_channel,
                "transaction_reference": transaction_reference,
                "payment_date": payment_date.isoformat(),
                "move_in_payment_id": confirmation_payment.pk,
                "confirmed_at": timezone.now().isoformat(),
            },
        )

    activation_response = _send_move_in_activation(
        request,
        unit.property,
        settlement,
        assigned_caretaker=assigned_caretaker,
        actor=request.user,
        identity_document=identity_document,
    )
    activation_payload = json.loads(activation_response.content)
    if activation_response.status_code >= 400 and not (
        activation_payload.get("tenant") and activation_payload.get("tenancy")
    ):
        return activation_response
    return JsonResponse(
        {
            **activation_payload,
            "settlement": _move_in_settlement_payload(settlement),
        },
        status=201,
    )


@require_POST
def move_in_stk_payment(request):
    error = _tenant_portal_error(request) if request.user.is_authenticated and _has_role(request.user, TENANT_PORTAL_ROLE) else None
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if error:
        return error
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    action = body.get("action")
    if action not in {"create", "attach", "fail"}:
        return _problem(400, "Invalid request", "A supported payment action is required.", "invalid-request")
    payment_id = None
    if action != "create":
        try:
            payment_id = int(body.get("paymentId"))
        except (TypeError, ValueError):
            return _problem(400, "Invalid request", "A valid paymentId is required.", "invalid-request")

    settlement = None
    property_obj = None
    is_landlord = _has_role(request.user, LANDLORD_PORTAL_ROLE)
    if is_landlord:
        property_obj, error = _landlord_move_in_property(request)
        if error:
            return error
        if action == "create":
            try:
                settlement_id = int(body.get("settlementId"))
            except (TypeError, ValueError):
                return _problem(400, "Invalid request", "A settlement ID is required.", "invalid-request")
            settlement = MoveInSettlement.objects.filter(
                pk=settlement_id,
                unit__property=property_obj,
            ).select_related("unit").first()
        else:
            payment = MoveInPayment.objects.filter(
                pk=payment_id,
                settlement__unit__property=property_obj,
                recorded_by=request.user,
            ).select_related("settlement").first()
            settlement = payment.settlement if payment else None
    else:
        tenant = Tenant.objects.filter(portal_user=request.user).first()
        if tenant is None:
            return _problem(403, "Permission denied", "This payment is unavailable.", "payment-access-denied")
        if action == "create":
            try:
                settlement_id = int(body.get("settlementId"))
            except (TypeError, ValueError):
                return _problem(400, "Invalid request", "A settlement ID is required.", "invalid-request")
            settlement = MoveInSettlement.objects.filter(
                pk=settlement_id,
                tenant=tenant,
            ).select_related("unit").first()
        else:
            payment = MoveInPayment.objects.filter(
                pk=payment_id,
                settlement__tenant=tenant,
                recorded_by=request.user,
            ).select_related("settlement").first()
            settlement = payment.settlement if payment else None
    if settlement is None:
        return _problem(404, "Settlement not found", "The settlement is unavailable to this account.", "settlement-not-found")

    if action == "create":
        try:
            amount = Decimal(str(body.get("amount")))
        except (InvalidOperation, TypeError, ValueError):
            return _problem(400, "Invalid payment", "A positive payment amount is required.", "invalid-payment")
        if not amount.is_finite() or amount <= 0:
            return _problem(400, "Invalid payment", "A positive payment amount is required.", "invalid-payment")
        amount = amount.quantize(Decimal("0.01"))
        with transaction.atomic():
            settlement = MoveInSettlement.objects.select_for_update().filter(
                pk=settlement.pk
            ).first()
            if settlement is None or settlement.status == MoveInSettlement.STATUS_CANCELLED:
                return _problem(404, "Settlement not found", "The settlement is unavailable.", "settlement-not-found")
            if amount > settlement.outstanding_balance:
                return _problem(400, "Invalid payment", "Payment exceeds the move-in balance.", "payment-exceeds-balance")
            payment = MoveInPayment.objects.create(
                settlement=settlement,
                amount=amount,
                method=MoveInPayment.METHOD_STK_PUSH,
                recorded_by=request.user,
                status=MoveInPayment.STATUS_PENDING,
            )
        return JsonResponse(
            {
                "paymentId": payment.pk,
                "tenantPhone": settlement.tenant_phone,
                "settlementId": settlement.pk,
            },
            status=201,
        )

    if action == "fail":
        description = body.get("description")
        if not isinstance(description, str):
            description = "STK push initiation failed."
        updated = MoveInPayment.objects.filter(
            pk=payment_id,
            settlement=settlement,
            recorded_by=request.user,
            status=MoveInPayment.STATUS_PENDING,
        ).update(
            status=MoveInPayment.STATUS_FAILED,
            result_description=description[:1000],
            processed_at=timezone.now(),
        )
        return JsonResponse({"updated": bool(updated)})

    checkout_request_id = body.get("checkoutRequestId")
    if not isinstance(checkout_request_id, str) or not checkout_request_id.strip():
        return _problem(400, "Invalid payment attempt", "A checkout request ID is required.", "invalid-payment-attempt")
    payment = MoveInPayment.objects.filter(
        pk=payment_id,
        settlement=settlement,
        recorded_by=request.user,
        status=MoveInPayment.STATUS_PENDING,
    ).first()
    if payment is None:
        return _problem(404, "Payment attempt not found", "The pending payment attempt is unavailable.", "payment-attempt-not-found")
    payment.checkout_request_id = checkout_request_id.strip()
    try:
        payment.save(update_fields=["checkout_request_id"])
    except IntegrityError:
        return _problem(409, "Duplicate payment attempt", "This checkout request is already linked to a payment.", "duplicate-payment-attempt")
    return JsonResponse({"paymentId": payment.pk, "settlementId": settlement.pk})


@csrf_exempt
@require_POST
def receive_move_in_mpesa_callback(request):
    body = _json_body(request)
    if body is None:
        return JsonResponse({"matched": False, "error": "Invalid callback payload."}, status=400)
    checkout_request_id = body.get("checkout_request_id")
    if not isinstance(checkout_request_id, str) or not checkout_request_id:
        return JsonResponse({"matched": False, "error": "Checkout request ID is required."}, status=400)
    try:
        result_code = int(body.get("result_code"))
    except (TypeError, ValueError):
        return JsonResponse({"matched": False, "error": "Result code is required."}, status=400)

    try:
        with transaction.atomic():
            payment = MoveInPayment.objects.select_for_update().filter(
                checkout_request_id=checkout_request_id,
            ).select_related("settlement__unit").first()
            if payment is None:
                return JsonResponse({"matched": False})
            if payment.status == MoveInPayment.STATUS_SUCCESS:
                return JsonResponse({"matched": True, "applied": True, "duplicate": True})
            if payment.status != MoveInPayment.STATUS_PENDING:
                return JsonResponse({"matched": True, "applied": False, "reason": "attempt_not_pending"})
            if result_code != 0:
                payment.status = MoveInPayment.STATUS_FAILED
                payment.result_description = str(body.get("result_description") or "M-Pesa payment failed.")[:1000]
                payment.processed_at = timezone.now()
                payment.save(update_fields=["status", "result_description", "processed_at"])
                return JsonResponse({"matched": True, "applied": False, "reason": "payment_failed"})

            try:
                amount = Decimal(str(body.get("amount")))
            except (InvalidOperation, TypeError, ValueError):
                amount = None
            reference = body.get("reference")
            if (
                amount is None
                or not amount.is_finite()
                or amount != payment.amount
                or not isinstance(reference, str)
                or not reference.strip()
            ):
                payment.status = MoveInPayment.STATUS_NEEDS_REVIEW
                payment.result_description = "M-Pesa callback did not match the pending payment amount or reference."
                payment.processed_at = timezone.now()
                payment.save(update_fields=["status", "result_description", "processed_at"])
                return JsonResponse({"matched": True, "applied": False, "reason": "callback_mismatch"})

            settlement = MoveInSettlement.objects.select_for_update().get(pk=payment.settlement_id)
            if (
                settlement.status == MoveInSettlement.STATUS_CANCELLED
                or payment.amount > settlement.outstanding_balance
            ):
                payment.status = MoveInPayment.STATUS_NEEDS_REVIEW
                payment.result_description = "The settlement balance changed before callback processing."
                payment.processed_at = timezone.now()
                payment.save(update_fields=["status", "result_description", "processed_at"])
                return JsonResponse({"matched": True, "applied": False, "reason": "balance_changed"})

            settlement.total_paid += payment.amount
            deposit_target = settlement.deposit_total
            settlement.deposit_paid_amount += min(
                payment.amount,
                max(Decimal("0.00"), deposit_target - settlement.deposit_paid_amount),
            )
            settlement.outstanding_balance = max(
                Decimal("0.00"),
                settlement.total_required - settlement.total_paid,
            )
            if settlement.activated_at:
                settlement.status = MoveInSettlement.STATUS_ACTIVE
            elif settlement.outstanding_balance == 0:
                settlement.status = MoveInSettlement.STATUS_PAID
            elif settlement.activation_override:
                settlement.status = MoveInSettlement.STATUS_MOVE_IN_PENDING
            elif settlement.deposit_paid_amount >= deposit_target:
                settlement.status = MoveInSettlement.STATUS_BOOKED
            else:
                settlement.status = MoveInSettlement.STATUS_PARTIAL
            if settlement.deposit_paid_amount >= deposit_target:
                settlement.booked_at = settlement.booked_at or timezone.now()
                if settlement.unit.status == Unit.STATUS_VACANT:
                    settlement.unit.transition_to(Unit.STATUS_BOOKED)
                    settlement.unit.save(update_fields=["status", "listing_enabled", "updated_at"])
            settlement.save(
                update_fields=[
                    "total_paid",
                    "deposit_paid_amount",
                    "outstanding_balance",
                    "status",
                    "booked_at",
                    "updated_at",
                ]
            )
            payment.status = MoveInPayment.STATUS_SUCCESS
            payment.mpesa_reference = reference.strip()[:100]
            payment.result_description = ""
            payment.processed_at = timezone.now()
            payment.save(
                update_fields=["status", "mpesa_reference", "result_description", "processed_at"]
            )
    except (IntegrityError, ValidationError):
        logger.exception("Could not apply a move-in M-Pesa callback.")
        return JsonResponse({"matched": True, "applied": False, "reason": "processing_error"}, status=500)
    result = {"matched": True, "applied": True}
    if settlement.status == MoveInSettlement.STATUS_PAID:
        property_obj = Property.objects.filter(pk=settlement.unit.property_id).first()
        if property_obj is not None:
            activation_response = _send_move_in_activation(
                request,
                property_obj,
                settlement,
                actor=payment.recorded_by,
            )
            activation_result = json.loads(activation_response.content)
            result["activation_email_sent"] = activation_result.get("success", False)
            if activation_response.status_code >= 400:
                result["activation_email_error"] = activation_result.get("error")
    return JsonResponse(result)


def _landlord_statement_tax_config(agreement):
    if not isinstance(agreement.terms, dict):
        return None
    config = agreement.terms.get("landlord_statement_tax")
    if not isinstance(config, dict) or config.get("approved") is not True:
        return None
    name = config.get("name")
    basis = config.get("basis")
    try:
        rate = Decimal(str(config.get("rate")))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if (
        not isinstance(name, str)
        or not name.strip()
        or basis != "collected_invoice_allocations"
        or not rate.is_finite()
        or rate < 0
        or rate > 100
    ):
        return None
    return {"name": name.strip(), "rate": rate, "basis": basis}


def _build_landlord_statement(landlord, period_start, period_end, property_id=None):
    property_query = Property.objects.filter(landlord=landlord)
    if property_id is not None:
        property_query = property_query.filter(pk=property_id)
    properties = list(property_query.order_by("name", "id"))
    property_ids = [item.pk for item in properties]
    zero = Decimal("0.00")
    totals = {
        "billed_rent": zero,
        "collected_invoice_allocations": zero,
        "arrears": zero,
        "commission": zero,
        "expenses": zero,
        "maintenance_costs": zero,
        "tax": zero,
    }
    property_totals = {
        item.pk: {
            "property_id": item.pk,
            "property": item.name,
            "billed_rent": zero,
            "collected_invoice_allocations": zero,
            "arrears": zero,
            "expenses": zero,
            "maintenance_costs": zero,
        }
        for item in properties
    }
    maintenance_cost_allocations = []

    if property_ids:
        billed_by_property = (
            InvoiceLine.objects.filter(
                invoice__status=Invoice.STATUS_ISSUED,
                invoice__tenancy__unit__property_id__in=property_ids,
                invoice__period_start__range=(period_start, period_end),
                charge_type=UnitCharge.CHARGE_RENT,
            )
            .values("invoice__tenancy__unit__property_id")
            .annotate(total=Sum("amount"))
        )
        for row in billed_by_property:
            property_totals[row["invoice__tenancy__unit__property_id"]]["billed_rent"] = (
                row["total"] or zero
            )

        allocations = list(
            PaymentAllocation.objects.filter(
                invoice__status=Invoice.STATUS_ISSUED,
                invoice__tenancy__unit__property_id__in=property_ids,
                payment__received_at__date__range=(period_start, period_end),
            )
            .exclude(
                payment__instrument_status__in=(
                    Payment.INSTRUMENT_PENDING,
                    Payment.INSTRUMENT_BOUNCED,
                )
            )
            .select_related("payment", "invoice__tenancy__unit__property")
            .order_by("payment__received_at", "id")
        )
        agreement_cache = {}
        commission_by_agreement = {}
        for allocation in allocations:
            payment_date = timezone.localdate(allocation.payment.received_at)
            agreements = agreement_cache.get(payment_date)
            if agreements is None:
                agreements = list(
                    ManagementAgreement.objects.filter(
                        landlord=landlord,
                        effective_from__lte=payment_date,
                    )
                    .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=payment_date))
                    .order_by("-effective_from", "-id")
                )
                agreement_cache[payment_date] = agreements
            agreement = agreements[0] if agreements else None
            property_total = property_totals[
                allocation.invoice.tenancy.unit.property_id
            ]
            property_total["collected_invoice_allocations"] += allocation.amount
            if agreement is None:
                commission_by_agreement[None] = commission_by_agreement.get(None, zero) + allocation.amount
            else:
                commission_by_agreement[agreement.pk] = (
                    commission_by_agreement.get(agreement.pk, zero) + allocation.amount
                )

        overdue_invoices = (
            Invoice.objects.filter(
                status=Invoice.STATUS_ISSUED,
                tenancy__unit__property_id__in=property_ids,
                due_date__lte=period_end,
            )
            .select_related("branch", "tenancy__tenant", "tenancy__unit__property")
            .order_by("id")
        )
        for invoice in overdue_invoices:
            arrears = invoice_arrears(invoice, period_end)["total_due"]
            property_totals[invoice.tenancy.unit.property_id]["arrears"] += arrears

        expenses_by_property = (
            PropertyExpense.objects.filter(
                property_id__in=property_ids,
                status=PropertyExpense.STATUS_APPROVED,
                effective_from__range=(period_start, period_end),
                approved_at__date__lte=period_end,
            )
            .values("property_id")
            .annotate(total=Sum("amount"))
        )
        for row in expenses_by_property:
            property_totals[row["property_id"]]["expenses"] = row["total"] or zero

        landlord_cost_allocations = (
            MaintenanceCostAllocation.objects.filter(
                landlord=landlord,
                target=MaintenanceCostAllocation.TARGET_LANDLORD,
                quotation__status=MaintenanceQuotation.STATUS_APPROVED,
                quotation__ticket__property_id__in=property_ids,
                created_at__date__range=(period_start, period_end),
                quotation__approved_at__date__lte=period_end,
            )
            .select_related(
                "quotation__ticket__property",
                "quotation__ticket__unit",
                "quotation__vendor",
            )
            .order_by("created_at", "id")
        )
        for allocation in landlord_cost_allocations:
            ticket = allocation.quotation.ticket
            property_total = property_totals[ticket.property_id]
            property_total["maintenance_costs"] += allocation.amount
            maintenance_cost_allocations.append(
                {
                    "id": allocation.pk,
                    "quotation_id": allocation.quotation_id,
                    "ticket_id": ticket.pk,
                    "ticket_title": ticket.title,
                    "property_id": ticket.property_id,
                    "property": ticket.property.name,
                    "unit_code": ticket.unit.code if ticket.unit_id else None,
                    "vendor": allocation.quotation.vendor.name,
                    "description": allocation.quotation.description,
                    "reason": allocation.reason,
                    "amount": str(allocation.amount),
                    "allocated_at": allocation.created_at.isoformat(),
                }
            )

        agreements_in_period = list(
            ManagementAgreement.objects.filter(
                landlord=landlord,
                effective_from__lte=period_end,
            )
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=period_start))
            .order_by("effective_from", "id")
        )
        commission_configured = bool(agreements_in_period)
        agreements_by_id = {agreement.pk: agreement for agreement in agreements_in_period}
        if None in commission_by_agreement:
            commission_configured = False
        for agreement_id, collected in commission_by_agreement.items():
            if agreement_id is None:
                continue
            agreement = agreements_by_id.get(agreement_id)
            if agreement is None:
                commission_configured = False
                continue
            if agreement.commission_model == "percentage" and agreement.commission_rate is not None:
                totals["commission"] += (
                    collected * agreement.commission_rate / Decimal("100")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            elif agreement.commission_model != "fixed":
                commission_configured = False
        fixed_fee_unallocated = False
        for agreement in agreements_in_period:
            if agreement.commission_model == "fixed" and agreement.fixed_fee is not None:
                if property_id is None:
                    totals["commission"] += agreement.fixed_fee
                else:
                    fixed_fee_unallocated = True
            elif agreement.commission_model == "fixed":
                commission_configured = False
            elif agreement.commission_model == "percentage" and agreement.commission_rate is None:
                commission_configured = False
            elif agreement.commission_model != "percentage":
                commission_configured = False

        tax_configured = True
        tax_applicable = False
        for agreement in agreements_in_period:
            if not agreement.mri_enabled:
                continue
            tax_applicable = True
            tax_config = _landlord_statement_tax_config(agreement)
            if tax_config is None:
                tax_configured = False
                continue
            collected = commission_by_agreement.get(agreement.pk, zero)
            totals["tax"] += (
                collected * tax_config["rate"] / Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if not tax_applicable:
            tax_status = "not_applicable"
        elif tax_configured:
            tax_status = "configured"
        else:
            tax_status = "not_configured"
        commission_status = (
            "not_allocated"
            if fixed_fee_unallocated
            else "configured"
            if commission_configured
            else "not_configured"
        )

        for property_total in property_totals.values():
            totals["billed_rent"] += property_total["billed_rent"]
            totals["collected_invoice_allocations"] += property_total["collected_invoice_allocations"]
            totals["arrears"] += property_total["arrears"]
            totals["expenses"] += property_total["expenses"]
            totals["maintenance_costs"] += property_total["maintenance_costs"]
    else:
        commission_status = "not_allocated" if property_id is not None else "not_configured"
        tax_status = "not_applicable"

    net_payout = None
    if commission_status == "configured" and tax_status != "not_configured":
        net_payout = (
            totals["collected_invoice_allocations"]
            - totals["commission"]
            - totals["expenses"]
            - totals["maintenance_costs"]
            - totals["tax"]
        )

    statement = {
        "landlord": {"id": landlord.pk, "name": landlord.name},
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "commission_basis": "collected_invoice_allocations",
        "commission_status": commission_status,
        "tax_status": tax_status,
        "summary": {
            "billed_rent": str(totals["billed_rent"]),
            "collected_invoice_allocations": str(totals["collected_invoice_allocations"]),
            "arrears": str(totals["arrears"]),
            "commission": (
                None
                if commission_status == "not_allocated"
                else str(totals["commission"])
            ),
            "expenses": str(totals["expenses"]),
            "maintenance_costs": str(totals["maintenance_costs"]),
            "tax": str(totals["tax"]) if tax_status != "not_configured" else None,
            "net_payout": str(net_payout) if net_payout is not None else None,
        },
        "properties": [
            {
                **row,
                "billed_rent": str(row["billed_rent"]),
                "collected_invoice_allocations": str(row["collected_invoice_allocations"]),
                "arrears": str(row["arrears"]),
                "expenses": str(row["expenses"]),
                "maintenance_costs": str(row["maintenance_costs"]),
            }
            for row in property_totals.values()
        ],
        "maintenance_cost_allocations": maintenance_cost_allocations,
    }
    return statement, commission_status, tax_status


@require_GET
def landlord_statement(request):
    error = _landlord_portal_error(request)
    if error:
        return error

    report_type = request.GET.get("report", "statement")
    if report_type == "annual_mri":
        raw_year = request.GET.get("year", "")
        try:
            year = int(raw_year)
            if len(raw_year) != 4 or year < 1900 or year > 9998:
                raise ValueError
        except (TypeError, ValueError):
            return _problem(400, "Invalid year", "year must be a four-digit year.", "invalid-year")
        period_start = date(year, 1, 1)
        period_end = date(year, 12, 31)
    elif report_type == "statement":
        raw_start = request.GET.get("from")
        raw_end = request.GET.get("to")
        try:
            period_start = date.fromisoformat(raw_start) if raw_start else None
            period_end = date.fromisoformat(raw_end) if raw_end else None
        except ValueError:
            return _problem(400, "Invalid period", "from and to must be valid ISO dates.", "invalid-period")
        if period_start is None or period_end is None:
            return _problem(400, "Invalid period", "Both from and to dates are required.", "invalid-period")
        if period_start > period_end:
            return _problem(400, "Invalid period", "from cannot be after to.", "invalid-period")
        year = None
    else:
        return _problem(400, "Invalid report", "report must be statement or annual_mri.", "invalid-report")

    output_format = request.GET.get("format", "json").lower()
    if output_format not in {"json", "csv"}:
        return _problem(400, "Invalid format", "format must be json or csv.", "invalid-format")

    property_id = None
    raw_property_id = request.GET.get("property_id")
    if raw_property_id:
        try:
            property_id = int(raw_property_id)
            if property_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return _problem(400, "Invalid property", "property_id must be a positive integer.", "invalid-property")

    landlord = Landlord.objects.filter(portal_user=request.user).first()
    if landlord is None:
        return _problem(404, "Landlord record not found", "No landlord record is linked to this account.", "landlord-not-found")
    if property_id is not None and not Property.objects.filter(
        pk=property_id, landlord=landlord
    ).exists():
        return _problem(404, "Property not found", "The property is not linked to this landlord account.", "property-not-found")

    statement, commission_status, tax_status = _build_landlord_statement(
        landlord, period_start, period_end, property_id=property_id
    )
    portfolio_properties = list(
        Property.objects.filter(landlord=landlord).order_by("name", "id")
    )
    property_ids = [property_obj.pk for property_obj in portfolio_properties]
    deposit_property_ids = [property_id] if property_id is not None else property_ids
    trust_deposits = [
        {
            "id": account.pk,
            "property_id": account.tenancy.unit.property_id,
            "property": account.tenancy.unit.property.name,
            "unit_code": account.tenancy.unit.code,
            "tenant_name": account.tenancy.tenant.name,
            "balance": str(account.balance),
            "target_amount": str(account.target_amount),
        }
        for account in DepositAccount.objects.filter(
            tenancy__unit__property_id__in=deposit_property_ids
        )
        .select_related("tenancy__unit__property", "tenancy__tenant")
        .order_by("tenancy__unit__property__name", "tenancy__unit__code", "id")
    ]
    trust_deposits_total = sum(
        (Decimal(item["balance"]) for item in trust_deposits), Decimal("0.00")
    )
    latest_remittances = {}
    remittances = LandlordRemittance.objects.filter(
        landlord=landlord,
        period_start__lte=period_end,
        period_end__gte=period_start,
    ).order_by("-period_end", "-period_start", "-revision", "-id")
    for remittance in remittances:
        period_key = (remittance.period_start, remittance.period_end)
        latest_remittances.setdefault(
            period_key, _landlord_remittance_payload(remittance)
        )

    statement.update(
        {
            "report": report_type,
            "property_id": property_id,
            "property_options": [
                {"id": item.pk, "name": item.name}
                for item in portfolio_properties
            ],
            "trust_deposits": trust_deposits,
            "trust_deposits_total": str(trust_deposits_total),
            "remittances": list(latest_remittances.values()),
        }
    )
    if year is not None:
        statement["year"] = year

    record_audit_event(
        request,
        "landlord_statement.exported" if output_format == "csv" else "landlord_statement.viewed",
        "LandlordStatement",
        new_values={
            "landlord_id": landlord.pk,
            "report": report_type,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "property_id": property_id,
            "commission_status": commission_status,
            "tax_status": tax_status,
        },
    )
    if output_format == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        report_name = (
            f"landlord-mri-summary-{year}.csv"
            if year is not None
            else f"landlord-statement-{period_start.isoformat()}-{period_end.isoformat()}.csv"
        )
        response["Content-Disposition"] = f'attachment; filename="{report_name}"'
        writer = csv.writer(response)
        writer.writerow(["record_type", "property_id", "property", "metric", "amount", "status"])
        if year is not None:
            writer.writerow(["annual_mri", "", landlord.name, "year", year, ""])
        for metric, amount in statement["summary"].items():
            writer.writerow(
                [
                    "summary",
                    "",
                    "",
                    metric,
                    amount if amount is not None else "",
                    tax_status if metric == "tax" else commission_status if metric == "commission" else "",
                ]
            )
        for row in statement["properties"]:
            for metric in (
                "billed_rent",
                "collected_invoice_allocations",
                "arrears",
                "expenses",
                "maintenance_costs",
            ):
                writer.writerow(["property", row["property_id"], row["property"], metric, row[metric], ""])
        for allocation in statement["maintenance_cost_allocations"]:
            writer.writerow(
                [
                    "maintenance_cost_allocation",
                    allocation["property_id"],
                    allocation["property"],
                    f'{allocation["ticket_title"]} · {allocation["vendor"]}',
                    allocation["amount"],
                    allocation["allocated_at"],
                ]
            )
        writer.writerow(["trust_deposits", "", "", "total_held", trust_deposits_total, ""])
        for deposit in trust_deposits:
            writer.writerow(
                [
                    "trust_deposit",
                    deposit["property_id"],
                    deposit["property"],
                    f'{deposit["unit_code"]} · {deposit["tenant_name"]}',
                    deposit["balance"],
                    "held",
                ]
            )
        for remittance in latest_remittances.values():
            writer.writerow(
                [
                    "remittance",
                    "",
                    f'{remittance["period_start"]} to {remittance["period_end"]}',
                    f'net_payout; reference={remittance["payout_reference"] or ""}',
                    remittance["summary"]["net_payout"],
                    remittance["status"],
                ]
            )
        return response
    return JsonResponse(statement)


def _landlord_remittance_payload(remittance):
    return {
        "id": remittance.pk,
        "landlord": {
            "id": remittance.landlord_id,
            "name": remittance.landlord.name,
        },
        "period_start": remittance.period_start.isoformat(),
        "period_end": remittance.period_end.isoformat(),
        "revision": remittance.revision,
        "summary": remittance.statement_snapshot["summary"],
        "commission_status": remittance.statement_snapshot["commission_status"],
        "tax_status": remittance.statement_snapshot["tax_status"],
        "status": remittance.status,
        "prepared_by": remittance.prepared_by_id,
        "reviewed_by": remittance.reviewed_by_id,
        "rejection_reason": remittance.rejection_reason,
        "payout_reference": remittance.payout_reference,
        "payout_date": remittance.payout_date.isoformat() if remittance.payout_date else None,
        "payout_recorded_by": remittance.payout_recorded_by_id,
        "created_at": remittance.created_at.isoformat(),
    }


@require_GET
def list_landlord_remittances(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")

    if _has_landlord_management_access(request.user):
        error = _move_out_staff_error(request)
        if error:
            return error
        landlords = _accessible_landlords(request.user)
        remittances = LandlordRemittance.objects.filter(
            landlord__in=landlords
        ).select_related("landlord", "prepared_by", "reviewed_by", "payout_recorded_by")
        landlord_options = list(landlords.order_by("name", "id").values("id", "name"))
    else:
        landlord = Landlord.objects.filter(portal_user=request.user).first()
        if landlord is None:
            return _problem(
                403,
                "Permission denied",
                "Only authorized staff or a linked landlord can view remittances.",
                "landlord-remittance-access-denied",
            )
        remittances = LandlordRemittance.objects.filter(landlord=landlord).select_related(
            "landlord", "prepared_by", "reviewed_by", "payout_recorded_by"
        )
        landlord_options = [{"id": landlord.pk, "name": landlord.name}]

    return JsonResponse({
        "landlords": landlord_options,
        "remittances": [
            _landlord_remittance_payload(item)
            for item in remittances.order_by("-period_end", "-revision", "-id")[:200]
        ],
    })


@require_POST
def create_landlord_remittance(request):
    error = _move_out_staff_error(request)
    if error:
        return error
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "A JSON object is required.", "invalid-request")
    try:
        landlord_id = int(body.get("landlord_id"))
        period_start = date.fromisoformat(str(body.get("period_start", "")))
        period_end = date.fromisoformat(str(body.get("period_end", "")))
    except (TypeError, ValueError):
        return _problem(
            400,
            "Invalid remittance",
            "landlord_id, period_start, and period_end must be valid.",
            "invalid-landlord-remittance",
        )
    if period_start > period_end or period_end > timezone.localdate():
        return _problem(
            400,
            "Invalid remittance period",
            "The period must be valid and cannot end in the future.",
            "invalid-landlord-remittance-period",
        )

    try:
        with transaction.atomic():
            landlord = Landlord.objects.select_for_update().filter(
                pk=landlord_id,
                pk__in=_accessible_landlords(request.user).values("pk"),
            ).first()
            if landlord is None:
                return _problem(
                    404,
                    "Landlord not found",
                    "The landlord is unavailable.",
                    "landlord-not-found",
                )
            latest = LandlordRemittance.objects.filter(
                landlord=landlord,
                period_start=period_start,
                period_end=period_end,
            ).order_by("-revision").first()
            if latest and latest.status != LandlordRemittance.STATUS_REJECTED:
                return _problem(
                    409,
                    "Remittance already exists",
                    "This period already has an active remittance.",
                    "landlord-remittance-conflict",
                )

            statement, commission_status, tax_status = _build_landlord_statement(
                landlord, period_start, period_end
            )
            if commission_status != "configured" or tax_status == "not_configured":
                return _problem(
                    409,
                    "Remittance configuration incomplete",
                    "Configure the management commission and any applicable approved tax before preparing a remittance.",
                    "landlord-remittance-configuration-incomplete",
                )
            summary = statement["summary"]
            net_payout = Decimal(summary["net_payout"])
            if net_payout < 0:
                return _problem(
                    409,
                    "Negative remittance",
                    "Approved expenses and deductions exceed collections; resolve the balance before preparing a remittance.",
                    "negative-landlord-remittance",
                )
            remittance = LandlordRemittance(
                landlord=landlord,
                period_start=period_start,
                period_end=period_end,
                revision=(latest.revision + 1) if latest else 1,
                statement_snapshot=statement,
                collected_amount=Decimal(summary["collected_invoice_allocations"]),
                commission_amount=Decimal(summary["commission"]),
                expenses_amount=(
                    Decimal(summary["expenses"])
                    + Decimal(summary["maintenance_costs"])
                ),
                tax_amount=Decimal(summary["tax"]) if summary["tax"] is not None else None,
                net_payout=net_payout,
                prepared_by=request.user,
            )
            remittance.full_clean()
            remittance.save()
            record_audit_event(
                request,
                "landlord_remittance.prepared",
                "LandlordRemittance",
                new_values=_landlord_remittance_payload(remittance),
            )
    except IntegrityError:
        return _problem(
            409,
            "Remittance conflict",
            "A remittance for this period was created concurrently.",
            "landlord-remittance-conflict",
        )
    return JsonResponse({"remittance": _landlord_remittance_payload(remittance)}, status=201)


@require_POST
def decide_landlord_remittance(request, remittance_id):
    error = _move_out_staff_error(request)
    if error:
        return error
    if not (
        _has_manager_access(request.user)
    ):
        return _problem(
            403,
            "Permission denied",
            "Only a Branch Manager or Super Admin can approve a landlord remittance.",
            "landlord-remittance-approval-denied",
        )
    body = _json_body(request)
    action = body.get("action") if body else None
    if action not in {"approve", "reject"}:
        return _problem(
            400,
            "Invalid decision",
            "action must be approve or reject.",
            "invalid-landlord-remittance-decision",
        )
    review_note = str(body.get("review_note") or "").strip()
    if action == "reject" and not review_note:
        return _problem(
            400,
            "Invalid decision",
            "A review_note is required when rejecting a remittance.",
            "invalid-landlord-remittance-decision",
        )
    with transaction.atomic():
        remittance = LandlordRemittance.objects.select_for_update().filter(
            pk=remittance_id,
            landlord__in=_accessible_landlords(
                request.user,
                role_names=MANAGER_ROLE_NAMES,
            ),
        ).select_related("landlord", "prepared_by").first()
        if remittance is None:
            return _problem(
                404,
                "Remittance not found",
                "The remittance is unavailable.",
                "landlord-remittance-not-found",
            )
        if remittance.status != LandlordRemittance.STATUS_AWAITING_APPROVAL:
            return _problem(
                409,
                "Remittance unavailable",
                "Only a remittance awaiting approval can be reviewed.",
                "landlord-remittance-conflict",
            )
        if remittance.prepared_by_id == request.user.pk:
            return _problem(
                403,
                "Permission denied",
                "The remittance preparer cannot review their own remittance.",
                "landlord-remittance-self-approval-denied",
            )
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
            remittance.save(update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "approval_hash",
                "rejection_reason",
                "updated_at",
            ])
        except ValidationError as exc:
            return _problem(
                400,
                "Invalid decision",
                str(exc.message_dict),
                "invalid-landlord-remittance-decision",
            )
        record_audit_event(
            request,
            f"landlord_remittance.{remittance.status}",
            "LandlordRemittance",
            new_values=_landlord_remittance_payload(remittance),
        )
    return JsonResponse({"remittance": _landlord_remittance_payload(remittance)})


@require_POST
def record_landlord_remittance_payout(request, remittance_id):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not requires_finance_mfa(request.user):
        return _problem(
            403,
            "Permission denied",
            "Finance authorization is required to record a remittance payout.",
            "landlord-remittance-payout-denied",
        )
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "A JSON object is required.", "invalid-request")
    reference = body.get("payout_reference")
    if not isinstance(reference, str) or not reference.strip() or len(reference.strip()) > 100:
        return _problem(
            400,
            "Invalid payout",
            "A payout reference of at most 100 characters is required.",
            "invalid-landlord-remittance-payout",
        )
    try:
        payout_date = date.fromisoformat(str(body.get("payout_date", "")))
    except (TypeError, ValueError):
        return _problem(
            400,
            "Invalid payout",
            "payout_date must be a valid ISO date.",
            "invalid-landlord-remittance-payout",
        )
    with transaction.atomic():
        remittance = LandlordRemittance.objects.select_for_update().filter(
            pk=remittance_id,
            landlord__in=_accessible_landlords(request.user),
        ).select_related("landlord").first()
        if remittance is None:
            return _problem(
                404,
                "Remittance not found",
                "The remittance is unavailable.",
                "landlord-remittance-not-found",
            )
        if remittance.status != LandlordRemittance.STATUS_APPROVED:
            return _problem(
                409,
                "Remittance unavailable",
                "Only an approved remittance with a positive payout can be recorded.",
                "landlord-remittance-conflict",
            )
        if payout_date < remittance.period_end or payout_date > timezone.localdate():
            return _problem(
                400,
                "Invalid payout date",
                "The payout date must be on or after the remittance period and cannot be in the future.",
                "invalid-landlord-remittance-payout",
            )
        reference = reference.strip()
        if LandlordRemittance.objects.filter(payout_reference=reference).exists():
            return _problem(
                409,
                "Duplicate payout reference",
                "This payout reference has already been recorded.",
                "duplicate-landlord-remittance-payout",
            )
        remittance.status = LandlordRemittance.STATUS_PAID
        remittance.payout_reference = reference
        remittance.payout_date = payout_date
        remittance.payout_recorded_by = request.user
        try:
            remittance.full_clean(validate_unique=False)
        except ValidationError as exc:
            return _problem(
                400,
                "Invalid payout",
                str(exc.message_dict),
                "invalid-landlord-remittance-payout",
            )
        try:
            with transaction.atomic():
                remittance.save(update_fields=[
                    "status",
                    "payout_reference",
                    "payout_date",
                    "payout_recorded_by",
                    "updated_at",
                ])
        except IntegrityError:
            return _problem(
                409,
                "Duplicate payout reference",
                "This payout reference has already been recorded.",
                "duplicate-landlord-remittance-payout",
            )
        record_audit_event(
            request,
            "landlord_remittance.paid",
            "LandlordRemittance",
            new_values=_landlord_remittance_payload(remittance),
        )
    return JsonResponse({"remittance": _landlord_remittance_payload(remittance)})


def _invitation_assigned_properties(invitation):
    properties = list(invitation.properties.all())
    return properties or [invitation.property]


def _invitation_is_within_property_scope(invitation, property_ids):
    allowed_ids = set(property_ids)
    return all(
        property_obj.pk in allowed_ids
        for property_obj in _invitation_assigned_properties(invitation)
    )


def _caretaker_invitation_payload(invitation):
    properties = _invitation_assigned_properties(invitation)
    return {
        "id": invitation.pk,
        "email": invitation.email,
        "full_name": invitation.full_name,
        "property_id": invitation.property_id,
        "property_ids": [property_obj.pk for property_obj in properties],
        "property_name": ", ".join(property_obj.name for property_obj in properties),
        "property_names": [property_obj.name for property_obj in properties],
        "expires_at": invitation.expires_at.isoformat(),
        "status": (
            "active"
            if invitation.used_at
            else "pending_activation"
            if invitation.status == "active"
            else invitation.status
        ),
        "used_at": invitation.used_at.isoformat() if invitation.used_at else None,
    }


@require_GET
def list_caretaker_invitations(request):
    error = _caretaker_invitation_access_error(request)
    if error:
        return error
    properties = _caretaker_invitation_properties(request)
    raw_property_id = request.GET.get("property_id")
    if raw_property_id:
        try:
            property_id = int(raw_property_id)
        except ValueError:
            return _problem(400, "Invalid property", "property_id must be an integer.", "invalid-property")
        properties = properties.filter(pk=property_id)
        if not properties.exists():
            return _problem(404, "Property not found", "The property is outside your access scope.", "property-not-found")
    property_ids = list(properties.values_list("id", flat=True))
    invitation_records = CaretakerInvitation.objects.filter(
        Q(property_id__in=property_ids) | Q(properties__in=property_ids),
    ).select_related("property").prefetch_related("properties").distinct()[:100]
    invitations = [
        invitation
        for invitation in invitation_records
        if _invitation_is_within_property_scope(invitation, property_ids)
    ]
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
    error = _caretaker_invitation_access_error(request)
    if error:
        return error
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    email = body.get("email")
    full_name = body.get("full_name")
    phone = body.get("phone")
    identity = body.get("id_or_registration_number")
    kra_pin = body.get("kra_pin", "")
    if not isinstance(email, str) or not isinstance(full_name, str):
        return _problem(400, "Invalid caretaker", "A caretaker name and email address are required.", "invalid-caretaker")
    if not isinstance(phone, str) or not isinstance(identity, str) or not isinstance(kra_pin, str):
        return _problem(
            400,
            "Invalid caretaker",
            "A caretaker phone and ID or registration number are required; KRA PIN must be text.",
            "invalid-caretaker",
        )
    email = email.strip().lower()
    full_name = full_name.strip()
    identity = identity.strip()
    kra_pin = kra_pin.strip()
    try:
        validate_email(email)
        phone = _normalize_ke_phone(phone)
    except (ValidationError, ValueError) as validation_error:
        return _problem(400, "Invalid caretaker", str(validation_error), "invalid-caretaker")
    if not full_name or not identity:
        return _problem(
            400,
            "Invalid caretaker",
            "A caretaker name and ID or registration number are required.",
            "invalid-caretaker",
        )
    raw_property_ids = body.get("property_ids")
    if raw_property_ids is None:
        raw_property_ids = [body.get("property_id")]
    if not isinstance(raw_property_ids, list) or not raw_property_ids:
        return _problem(400, "Invalid property", "Select one or more properties.", "invalid-property")
    property_ids = []
    for raw_property_id in raw_property_ids:
        if isinstance(raw_property_id, bool) or not isinstance(raw_property_id, (int, str)):
            return _problem(400, "Invalid property", "Property IDs must be integers.", "invalid-property")
        try:
            property_id = int(raw_property_id)
        except (TypeError, ValueError):
            return _problem(400, "Invalid property", "Property IDs must be integers.", "invalid-property")
        if property_id <= 0:
            return _problem(400, "Invalid property", "Property IDs must be positive.", "invalid-property")
        if property_id not in property_ids:
            property_ids.append(property_id)
    user_model = get_user_model()
    if user_model.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exists():
        return _problem(409, "Account exists", "This email already belongs to an account.", "caretaker-exists")
    if UserProfile.objects.filter(phone__iexact=phone).exists():
        return _problem(409, "Phone already in use", "An account already uses this phone number.", "phone-in-use")
    token = secrets.token_urlsafe(32)
    activation_code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = timezone.now() + timedelta(
        seconds=settings.AUTH_DIRECTOR_ACTIVATION_TTL_SECONDS
    )
    with transaction.atomic():
        accessible_property_ids = _caretaker_invitation_properties(request).values_list("pk", flat=True)
        assigned_properties = list(
            Property.objects.select_for_update()
            .filter(pk__in=property_ids)
            .filter(pk__in=accessible_property_ids)
            .select_related("branch")
            .order_by("pk")
        )
        if len(assigned_properties) != len(property_ids):
            return _problem(404, "Property not found", "The property is outside your access scope.", "property-not-found")
        if CaretakerInvitation.objects.filter(
            Q(property_id__in=property_ids) | Q(properties__in=property_ids),
            email__iexact=email,
            used_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).exists():
            return _problem(409, "Invitation exists", "An active invitation already exists for this email on a selected property.", "invitation-exists")
        invitation = CaretakerInvitation.objects.create(
            property=assigned_properties[0],
            email=email,
            full_name=full_name,
            phone=phone,
            id_or_registration_number=identity,
            kra_pin=kra_pin,
            token_hash=_activation_hash(token),
            code_hash=make_password(activation_code),
            created_by=request.user,
            expires_at=expires_at,
        )
        invitation.properties.set(assigned_properties)
    record_audit_event(
        request,
        "caretaker_invitation.created",
        "CaretakerInvitation",
        new_values={
            "id": invitation.pk,
            "property_ids": [property_obj.pk for property_obj in assigned_properties],
            "email": email,
        },
    )
    property_names = ", ".join(property_obj.name for property_obj in assigned_properties)
    activation_url = f"{settings.PUBLIC_SITE_URL.rstrip('/')}/activate-caretaker?token={token}"
    try:
        _send_caretaker_activation_email(
            invitation.email,
            property_names,
            activation_url,
            activation_code,
        )
    except (ImproperlyConfigured, RuntimeError) as exc:
        invitation.email_delivery_error = str(exc)[:500]
        invitation.save(update_fields=["email_delivery_error"])
        logger.error("Could not deliver a caretaker activation email (%s).", type(exc).__name__)
        return JsonResponse(
            {
                "invitation": _caretaker_invitation_payload(invitation),
                "error": f"The invitation was created, but its activation email was not sent: {exc}",
            },
            status=502,
        )
    invitation.email_sent_at = timezone.now()
    invitation.save(update_fields=["email_sent_at"])
    return JsonResponse({"invitation": _caretaker_invitation_payload(invitation)}, status=201)


@require_POST
def resend_caretaker_invitation(request, invitation_id):
    error = _caretaker_invitation_access_error(request)
    if error:
        return error
    token = secrets.token_urlsafe(32)
    activation_code = f"{secrets.randbelow(1_000_000):06d}"
    with transaction.atomic():
        invitation = CaretakerInvitation.objects.select_for_update(of=("self",)).filter(
            pk=invitation_id,
        ).filter(
            Q(property__in=_caretaker_invitation_properties(request))
            | Q(properties__in=_caretaker_invitation_properties(request))
        ).select_related("property").prefetch_related("properties").first()
        if (
            invitation is None
            or not _invitation_is_within_property_scope(
                invitation,
                _caretaker_invitation_properties(request).values_list("pk", flat=True),
            )
            or invitation.used_at
            or invitation.revoked_at
        ):
            return _problem(404, "Invitation not found", "The invitation is unavailable or no longer active.", "invitation-not-found")
        invitation.token_hash = _activation_hash(token)
        invitation.code_hash = make_password(activation_code)
        invitation.failed_attempts = 0
        invitation.email_sent_at = None
        invitation.email_delivery_error = ""
        invitation.expires_at = timezone.now() + timedelta(
            seconds=settings.AUTH_DIRECTOR_ACTIVATION_TTL_SECONDS
        )
        invitation.save(
            update_fields=[
                "token_hash",
                "code_hash",
                "failed_attempts",
                "email_sent_at",
                "email_delivery_error",
                "expires_at",
            ]
        )
    record_audit_event(
        request,
        "caretaker_invitation.resent",
        "CaretakerInvitation",
        new_values={"id": invitation.pk, "property_id": invitation.property_id},
    )
    activation_url = (
        f"{settings.PUBLIC_SITE_URL.rstrip('/')}/activate-caretaker"
        f"?token={token}"
    )
    property_names = ", ".join(
        property_obj.name for property_obj in _invitation_assigned_properties(invitation)
    )
    try:
        _send_caretaker_activation_email(
            invitation.email,
            property_names,
            activation_url,
            activation_code,
        )
    except (ImproperlyConfigured, RuntimeError) as exc:
        invitation.email_delivery_error = str(exc)[:500]
        invitation.save(update_fields=["email_delivery_error"])
        logger.error("Could not resend a caretaker activation email (%s).", type(exc).__name__)
        return JsonResponse(
            {
                "invitation": _caretaker_invitation_payload(invitation),
                "error": f"The invitation was renewed, but its activation email was not sent: {exc}",
            },
            status=502,
        )
    invitation.email_sent_at = timezone.now()
    invitation.save(update_fields=["email_sent_at"])
    return JsonResponse({"invitation": _caretaker_invitation_payload(invitation)})


@require_POST
def revoke_caretaker_access(request):
    error = _caretaker_invitation_access_error(request)
    if error:
        return error
    body = _json_body(request)
    try:
        property_id = int(body["property_id"]) if body else None
        caretaker_id = int(body["caretaker_id"]) if body else None
    except (KeyError, TypeError, ValueError):
        property_id = caretaker_id = None
    property_obj = _caretaker_invitation_properties(request).filter(pk=property_id).first()
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


@require_POST
def revoke_caretaker_invitation(request, invitation_id):
    error = _caretaker_invitation_access_error(request)
    if error:
        return error
    with transaction.atomic():
        invitation = CaretakerInvitation.objects.select_for_update(of=("self",)).filter(
            pk=invitation_id,
        ).filter(
            Q(property__in=_caretaker_invitation_properties(request))
            | Q(properties__in=_caretaker_invitation_properties(request))
        ).select_related("property").prefetch_related("properties").first()
        if (
            invitation is None
            or not _invitation_is_within_property_scope(
                invitation,
                _caretaker_invitation_properties(request).values_list("pk", flat=True),
            )
            or invitation.used_at
            or invitation.revoked_at
        ):
            return _problem(
                404,
                "Invitation not found",
                "The invitation is unavailable or no longer active.",
                "invitation-not-found",
            )
        old_status = invitation.status
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at"])
    record_audit_event(
        request,
        "caretaker_invitation.revoked",
        "CaretakerInvitation",
        old_values={"id": invitation.pk, "status": old_status},
        new_values=_caretaker_invitation_payload(invitation),
    )
    return JsonResponse({"invitation": _caretaker_invitation_payload(invitation)})


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
            "full_name": invitation.full_name,
            "phone": invitation.phone,
            "property_name": ", ".join(
                property_obj.name for property_obj in _invitation_assigned_properties(invitation)
            ),
            "property_names": [
                property_obj.name for property_obj in _invitation_assigned_properties(invitation)
            ],
            "expires_at": invitation.expires_at.isoformat(),
        }
    )


@require_POST
def activate_caretaker(request):
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")
    token = body.get("token")
    code = body.get("code")
    password = body.get("password")
    if not all(isinstance(value, str) for value in (token, code, password)):
        return _problem(400, "Invalid request", "token, code, and password are required.", "invalid-request")
    code = code.strip()
    if len(code) != 6 or not code.isdigit():
        return _problem(400, "Invalid code", "Enter the six-digit activation code from the email.", "invalid-code")
    invitation = CaretakerInvitation.objects.filter(
        token_hash=_activation_hash(token),
    ).select_related("property").first()
    if invitation is None or not invitation.is_usable:
        return _problem(400, "Invalid invitation", "The caretaker invitation is invalid, expired, or already used.", "invalid-invitation")
    fallback_name = body.get("name", "")
    if not isinstance(fallback_name, str):
        return _problem(400, "Invalid name", "A valid caretaker name is required.", "invalid-name")
    name = invitation.full_name.strip() or fallback_name.strip()
    raw_phone = invitation.phone or body.get("phone", "")
    if not isinstance(raw_phone, str):
        return _problem(400, "Invalid phone", "A valid caretaker phone number is required.", "invalid-phone")
    try:
        phone = _normalize_ke_phone(raw_phone)
    except ValueError as exc:
        return _problem(400, "Invalid phone", str(exc), "invalid-phone")
    if not name or len(password) < 12:
        return _problem(400, "Invalid registration", "Caretaker details and a password of at least 12 characters are required.", "invalid-registration")
    try:
        password_validation.validate_password(password)
    except ValidationError as exc:
        return _problem(400, "Invalid registration", "; ".join(exc.messages), "invalid-registration")
    try:
        with transaction.atomic():
            invitation = CaretakerInvitation.objects.select_for_update().select_related(
                "property"
            ).prefetch_related("properties").get(pk=invitation.pk)
            if not invitation.is_usable:
                return _problem(400, "Invalid invitation", "The caretaker invitation is invalid, expired, or already used.", "invalid-invitation")
            if not invitation.code_hash or not check_password(code, invitation.code_hash):
                invitation.failed_attempts += 1
                update_fields = ["failed_attempts"]
                if invitation.failed_attempts >= 5:
                    invitation.expires_at = timezone.now()
                    update_fields.append("expires_at")
                invitation.save(update_fields=update_fields)
                return _problem(400, "Invalid code", "The activation code is incorrect or expired.", "invalid-code")
            user_model = get_user_model()
            if user_model.objects.filter(
                Q(email__iexact=invitation.email) | Q(username__iexact=invitation.email)
            ).exists():
                return _problem(409, "Account exists", "This email already belongs to an account.", "caretaker-exists")
            user = user_model.objects.create_user(
                username=invitation.email,
                email=invitation.email,
                password=password,
            )
            group, _ = Group.objects.get_or_create(name=CARETAKER_ROLE)
            user.groups.add(group)
            profile = UserProfile(
                user=user,
                name=name,
                phone=phone,
                id_or_registration_number=invitation.id_or_registration_number,
                kra_pin=invitation.kra_pin,
            )
            profile.full_clean()
            profile.save()
            assigned_properties = _invitation_assigned_properties(invitation)
            profile.branches.add(*(property_obj.branch for property_obj in assigned_properties))
            PropertyCaretakerAssignment.objects.bulk_create(
                [
                    PropertyCaretakerAssignment(
                        property=property_obj,
                        caretaker=user,
                        assigned_by=invitation.created_by,
                    )
                    for property_obj in assigned_properties
                ]
            )
            for property_obj in assigned_properties:
                property_obj.caretakers.add(user)
            invitation.used_by = user
            invitation.used_at = timezone.now()
            invitation.save(update_fields=["used_by", "used_at"])
    except ValidationError as exc:
        return _problem(400, "Invalid registration", "; ".join(exc.messages), "invalid-registration")
    except IntegrityError:
        return _problem(409, "Account exists", "The account or caretaker assignment already exists.", "account-exists")
    record_audit_event(
        request,
        "caretaker_invitation.accepted",
        "CaretakerInvitation",
        new_values={
            "id": invitation.pk,
            "user_id": user.pk,
            "property_ids": [property_obj.pk for property_obj in assigned_properties],
        },
        actor=user,
    )
    return JsonResponse({"activated": True}, status=201)


@require_GET
def caretaker_dashboard(request):
    error = _caretaker_dashboard_error(request)
    if error:
        return error
    properties = _accessible_properties(request.user).prefetch_related("caretaker_assignments")
    units = Unit.objects.filter(property__in=properties).select_related(
        "property",
        "floor__block",
        "unit_type_definition",
    )
    readings = MeterReading.objects.filter(unit__in=units).select_related("unit", "submitted_by")[:20]
    period = _caretaker_water_period()
    readings_due = _caretaker_water_readings_due()
    active_tenancies = Tenancy.objects.filter(
        unit__in=units,
        status=Tenancy.STATUS_ACTIVE,
    ).select_related("tenant")
    tenancy_by_unit = {tenancy.unit_id: tenancy for tenancy in active_tenancies}
    metered_unit_ids = set(
        UtilityConfiguration.objects.filter(
            unit__in=units,
            utility_type=UtilityConfiguration.UTILITY_WATER,
            billing_method=UtilityConfiguration.METHOD_METERED,
            effective_from__lte=period,
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=period))
        .values_list("unit_id", flat=True)
    )
    eligible_unit_ids = metered_unit_ids.intersection(tenancy_by_unit)
    period_reading_units = set(
        MeterReading.objects.filter(
            unit_id__in=eligible_unit_ids,
            period=period,
        ).values_list("unit_id", flat=True)
    )
    active_readings_by_unit = {}
    for property_obj in properties:
        active_readings_by_unit.update(
            {
                row["unit_id"]: row
                for row in _caretaker_water_units(property_obj, period)
            }
        )
    checklist_items = MoveInChecklistItem.objects.filter(
        package__tenancy__unit__in=units,
        package__handover_status=MoveInPackage.HANDOVER_STATUS_PENDING,
        status=MoveInChecklistItem.STATUS_PENDING,
    ).exclude(
        title__in=(
            "Confirm tenant identity and contact details",
            "Confirm agreement terms and signatures",
        )
    ).select_related(
        "package__tenancy",
        "package__tenancy__tenant",
        "package__tenancy__unit__property",
    )
    maintenance_tickets = _accessible_maintenance_tickets(request.user)
    today_viewings = (
        PropertyViewing.objects.filter(
            lead__property__in=properties,
            scheduled_for__date=timezone.localdate(),
            status=PropertyViewing.STATUS_SCHEDULED,
        )
        .filter(Q(assigned_to__isnull=True) | Q(assigned_to=request.user))
        .select_related("lead__property", "lead__unit")
        .order_by("scheduled_for")
    )
    unit_payloads = []
    for unit in units:
        tenancy = tenancy_by_unit.get(unit.pk)
        unit_payloads.append(
            {
                **_unit_payload(unit),
                "status": unit.status.lower().replace(" ", "_"),
                "property_id": unit.property_id,
                "property_name": unit.property.name,
                "floor_name": unit.floor.name if unit.floor else None,
                "block_name": unit.floor.block.name if unit.floor_id else None,
                "unit_type": unit.unit_type,
                "bedrooms": unit.bedrooms,
                "characteristics": (
                    unit.characteristics
                    if isinstance(unit.characteristics, dict)
                    else {}
                ),
                "tenant": (
                    {"id": tenancy.tenant_id, "name": tenancy.tenant.name}
                    if tenancy
                    else None
                ),
                "has_metered_water": unit.pk in metered_unit_ids,
                "reading_submitted_for_period": unit.pk in period_reading_units,
                "water_meter": active_readings_by_unit.get(unit.pk, {}).get("meter"),
                "water_meter_reading": (
                    active_readings_by_unit[unit.pk]["current_value"]
                    if active_readings_by_unit.get(unit.pk, {}).get("status") == "published"
                    else active_readings_by_unit.get(unit.pk, {}).get("previous_value")
                ),
            }
        )
    return JsonResponse(
        {
            "period": period.isoformat(),
            "readings_due": readings_due,
            "summary": {
                "properties": properties.count(),
                "units": len(unit_payloads),
                "occupied": sum(unit.status == Unit.STATUS_OCCUPIED for unit in units),
                "notice_given": sum(unit.status == Unit.STATUS_NOTICE_GIVEN for unit in units),
                "vacant": sum(unit.status == Unit.STATUS_VACANT for unit in units),
                "maintenance": sum(unit.status == Unit.STATUS_MAINTENANCE for unit in units),
                "readings_pending": (
                    len(eligible_unit_ids) - len(period_reading_units)
                    if readings_due
                    else 0
                ),
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
                    "has_paid_water": UtilityConfiguration.objects.filter(
                        unit__property=property_obj,
                        utility_type=UtilityConfiguration.UTILITY_WATER,
                        effective_from__lte=period,
                    ).filter(
                        Q(effective_to__isnull=True) | Q(effective_to__gte=period)
                    ).filter(
                        Q(fixed_amount__gt=0)
                        | Q(rate_per_unit__gt=0)
                        | Q(minimum_charge__gt=0)
                    ).exists(),
                    "has_metered_water": UtilityConfiguration.objects.filter(
                        unit__property=property_obj,
                        utility_type=UtilityConfiguration.UTILITY_WATER,
                        billing_method=UtilityConfiguration.METHOD_METERED,
                        effective_from__lte=period,
                    ).filter(
                        Q(effective_to__isnull=True) | Q(effective_to__gte=period)
                    ).exists(),
                }
                for property_obj in properties
            ],
            "units": unit_payloads,
            "viewings": [
                {
                    "id": viewing.pk,
                    "prospect_name": viewing.lead.full_name,
                    "phone": viewing.lead.phone,
                    "property_name": (
                        viewing.lead.property.name
                        if viewing.lead.property_id
                        else None
                    ),
                    "unit_code": (
                        viewing.lead.unit.code
                        if viewing.lead.unit_id
                        else None
                    ),
                    "scheduled_for": viewing.scheduled_for.isoformat(),
                    "notes": viewing.notes,
                }
                for viewing in today_viewings
            ],
            "readings": [_meter_reading_payload(reading) for reading in readings],
            "checklists": [
                {
                    "id": item.pk,
                    "tenancy_id": item.package.tenancy_id,
                    "unit_id": item.package.tenancy.unit_id,
                    "unit_code": item.package.tenancy.unit.code,
                    "property_name": item.package.tenancy.unit.property.name,
                    "tenant_name": item.package.tenancy.tenant.name,
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


@require_http_methods(["PATCH"])
def complete_caretaker_move_in_checklist_item(request, item_id):
    error = _caretaker_dashboard_error(request)
    if error:
        return error
    body = _json_body(request)
    if not isinstance(body, dict) or body.get("status") != MoveInChecklistItem.STATUS_COMPLETED:
        return _problem(
            400,
            "Invalid checklist update",
            "The checklist item status must be completed.",
            "invalid-checklist-update",
        )
    notes = body.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 5000:
        return _problem(
            400,
            "Invalid checklist update",
            "Checklist notes must be text no longer than 5,000 characters.",
            "invalid-checklist-update",
        )
    item = (
        MoveInChecklistItem.objects.select_related("package__tenancy__unit__property")
        .filter(pk=item_id, package__tenancy__unit__property__in=_accessible_properties(request.user))
        .filter(
            models.Q(package__assigned_caretaker=request.user)
            | models.Q(package__assigned_caretaker__isnull=True)
        )
        .first()
    )
    if item is None:
        return _problem(404, "Checklist item not found", "The checklist item is unavailable.", "checklist-item-not-found")
    with transaction.atomic():
        locked_item = (
            MoveInChecklistItem.objects.select_for_update()
            .filter(
                pk=item.pk,
                package__tenancy__unit__property__in=_accessible_properties(request.user),
            )
            .filter(
                models.Q(package__assigned_caretaker=request.user)
                | models.Q(package__assigned_caretaker__isnull=True)
            )
            .first()
        )
        if locked_item is None:
            return _problem(404, "Checklist item not found", "The checklist item is unavailable.", "checklist-item-not-found")
        if locked_item.title in MoveInChecklistItem.CARETAKER_HANDOVER_ITEM_TITLES:
            return _problem(
                409,
                "Structured handover required",
                "Submit the move-in handover form to record readings, condition details, keys, and photos together.",
                "structured-handover-required",
            )
        if locked_item.status != MoveInChecklistItem.STATUS_PENDING:
            return _problem(409, "Checklist item already completed", "Only pending checklist items can be completed.", "checklist-item-conflict")
        locked_item.status = MoveInChecklistItem.STATUS_COMPLETED
        locked_item.notes = notes.strip()
        locked_item.completed_at = timezone.now()
        locked_item.save(update_fields=["status", "notes", "completed_at"])
        record_audit_event(
            request,
            "move_in_checklist.completed",
            "MoveInChecklistItem",
            new_values={
                "id": locked_item.pk,
                "package_id": locked_item.package_id,
                "status": locked_item.status,
                "notes": locked_item.notes,
            },
        )
    return JsonResponse(
        {
            "checklist_item": {
                "id": locked_item.pk,
                "status": locked_item.status,
                "notes": locked_item.notes,
                "completed_at": locked_item.completed_at.isoformat(),
            }
        }
    )


MOVE_IN_CONDITION_AREAS = (
    "Walls",
    "Doors",
    "Locks",
    "Windows",
    "Plumbing fixtures",
    "Electrical sockets",
)
MOVE_IN_CONDITION_STATUSES = {"good", "needs_attention", "not_applicable"}
MOVE_IN_METER_TYPES = {
    UtilityConfiguration.UTILITY_WATER,
    UtilityConfiguration.UTILITY_ELECTRICITY,
}


def _move_in_meter_requirements(package):
    tenancy = package.tenancy
    utilities = UtilityConfiguration.objects.filter(
        unit_id=tenancy.unit_id,
        utility_type__in=MOVE_IN_METER_TYPES,
        billing_method=UtilityConfiguration.METHOD_METERED,
        effective_from__lte=tenancy.start_date,
    ).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=tenancy.start_date)
    ).order_by("utility_type", "-effective_from", "-id")
    requirements = []
    seen_types = set()
    for utility in utilities:
        if utility.utility_type in seen_types:
            continue
        seen_types.add(utility.utility_type)
        metadata = utility.metadata if isinstance(utility.metadata, dict) else {}
        meter = str(metadata.get("meter") or tenancy.unit.meters.get(utility.utility_type) or "").strip()
        requirements.append(
            {
                "utility_type": utility.utility_type,
                "name": utility.name,
                "meter": meter or None,
                "configured": bool(meter),
            }
        )
    return requirements


def _move_in_condition_requirements(unit):
    rooms = ["Living room", "Kitchen", "Bathroom"]
    if "studio" in unit.unit_type.lower():
        rooms[0] = "Studio"
    else:
        rooms.extend(
            f"Bedroom {index}"
            for index in range(1, unit.bedrooms + 1)
        )
    return [
        {"room": room, "area": area}
        for room in rooms
        for area in MOVE_IN_CONDITION_AREAS
    ]


def _validated_move_in_handover_inventory(package, raw_items):
    expected_items = package.handover_inventory
    if not isinstance(expected_items, list):
        raise ValueError("The configured handover inventory is invalid. Contact a Property Officer.")
    if not isinstance(raw_items, list) or len(raw_items) != len(expected_items):
        raise ValueError("Record an issued quantity for every configured handover item.")

    normalized_items = []
    seen_names = set()
    for expected, submitted in zip(expected_items, raw_items):
        if not isinstance(expected, dict) or not isinstance(submitted, dict):
            raise ValueError("Each handover inventory entry must be an object.")
        name = expected.get("name")
        property_quantity = expected.get("property_quantity")
        issued_quantity = submitted.get("issued_quantity")
        if (
            not isinstance(name, str)
            or not name.strip()
            or isinstance(property_quantity, bool)
            or not isinstance(property_quantity, int)
            or not 0 <= property_quantity <= 32767
            or submitted.get("name") != name
            or name.casefold() in seen_names
        ):
            raise ValueError("The handover inventory changed. Reload the checklist and try again.")
        if (
            isinstance(issued_quantity, bool)
            or not isinstance(issued_quantity, int)
            or not 0 <= issued_quantity <= 32767
        ):
            raise ValueError(
                f"The issued quantity for {name} must be a whole number from 0 to 32,767."
            )
        seen_names.add(name.casefold())
        normalized_items.append(
            {
                "name": name,
                "property_quantity": property_quantity,
                "issued_quantity": issued_quantity,
            }
        )
    return normalized_items


def _move_in_handover_photo_error(package):
    photos = package.handover_photos
    if not isinstance(photos, list) or not photos:
        return "Upload at least one move-in condition or meter photo before submitting."
    has_water_meter = any(
        requirement["utility_type"] == UtilityConfiguration.UTILITY_WATER
        for requirement in _move_in_meter_requirements(package)
    )
    has_water_meter_photo = any(
        isinstance(photo, dict) and photo.get("evidence_type") == "water_meter"
        for photo in photos
    )
    if has_water_meter and not has_water_meter_photo:
        return "Upload a clear photo of the water meter and its reading before submitting."
    return None


def _move_in_handover_payload(package, photo_path):
    condition_checklist = package.condition_checklist or [
        {**entry, "status": "", "notes": ""}
        for entry in _move_in_condition_requirements(package.tenancy.unit)
    ]
    return {
        "id": package.pk,
        "tenancy_id": package.tenancy_id,
        "property_name": package.tenancy.unit.property.name,
        "unit_id": package.tenancy.unit_id,
        "unit_code": package.tenancy.unit.code,
        "tenant_name": package.tenancy.tenant.name,
        "move_in_date": package.tenancy.start_date.isoformat(),
        "assigned_caretaker_id": package.assigned_caretaker_id,
        "status": package.handover_status,
        "keys_issued": package.keys_issued,
        "handover_inventory": package.handover_inventory,
        "condition_checklist": condition_checklist,
        "meter_requirements": _move_in_meter_requirements(package),
        "initial_meter_readings": package.initial_meter_readings,
        "photos": [
            {
                "name": photo.get("name", f"Handover photo {index + 1}"),
                "evidence_type": photo.get("evidence_type"),
                "content_type": photo.get("content_type"),
                "size_bytes": photo.get("size_bytes"),
                "url": f"{photo_path}/{package.pk}/photos/{index}",
            }
            for index, photo in enumerate(package.handover_photos)
            if isinstance(photo, dict)
        ],
        "submitted_at": (
            package.handover_submitted_at.isoformat()
            if package.handover_submitted_at
            else None
        ),
        "confirmed_at": (
            package.handover_confirmed_at.isoformat()
            if package.handover_confirmed_at
            else None
        ),
    }


@require_GET
def caretaker_move_in_handovers(request):
    error = _caretaker_dashboard_error(request)
    if error:
        return error
    packages = (
        MoveInPackage.objects.filter(
            handover_status=MoveInPackage.HANDOVER_STATUS_PENDING,
            tenancy__status=Tenancy.STATUS_ACTIVE,
            tenancy__unit__property__in=_accessible_properties(request.user),
        )
        .filter(
            models.Q(assigned_caretaker=request.user)
            | models.Q(assigned_caretaker__isnull=True)
        )
        .select_related("tenancy__tenant", "tenancy__unit__property")
        .order_by("tenancy__start_date", "tenancy__unit__code", "id")
    )
    return JsonResponse(
        {
            "handovers": [
                _move_in_handover_payload(
                    package,
                    "/api/caretaker/checklists",
                )
                for package in packages
            ]
        }
    )


@require_POST
def upload_caretaker_move_in_handover_photos(request, package_id):
    error = _caretaker_dashboard_error(request)
    if error:
        return error
    package = (
        MoveInPackage.objects.filter(
            pk=package_id,
            handover_status=MoveInPackage.HANDOVER_STATUS_PENDING,
            tenancy__status=Tenancy.STATUS_ACTIVE,
            tenancy__unit__property__in=_accessible_properties(request.user),
        )
        .filter(
            models.Q(assigned_caretaker=request.user)
            | models.Q(assigned_caretaker__isnull=True)
        )
        .select_related("tenancy__tenant", "tenancy__unit__property")
        .first()
    )
    if package is None:
        return _problem(
            404,
            "Handover not found",
            "The move-in handover is unavailable or outside your assigned properties.",
            "move-in-handover-not-found",
        )
    if timezone.localdate() < package.tenancy.start_date:
        return _problem(
            409,
            "Move-in inspection not due",
            f"The move-in inspection can be submitted on {package.tenancy.start_date.isoformat()}.",
            "move-in-inspection-not-due",
        )
    files = request.FILES.getlist("photos")
    try:
        evidence = _prepare_tenant_evidence(files, max_files=10)
    except ValueError as exc:
        return _problem(400, "Invalid evidence", str(exc), "invalid-evidence")
    if not evidence:
        return _problem(
            400,
            "Photos required",
            "Select at least one photo to upload.",
            "handover-photos-required",
        )
    photo_types = request.POST.getlist("photo_types")
    if photo_types and (
        len(photo_types) != len(evidence)
        or any(photo_type not in {"condition", "water_meter"} for photo_type in photo_types)
    ):
        return _problem(
            400,
            "Invalid photo evidence type",
            "Choose a valid evidence type for each uploaded photo.",
            "invalid-handover-photo-type",
        )
    if not photo_types:
        photo_types = ["condition"] * len(evidence)
    if len(package.handover_photos) + len(evidence) > 10:
        return _problem(
            400,
            "Too many photos",
            "A move-in handover can include no more than 10 photos.",
            "too-many-handover-photos",
        )

    uploaded_keys = []
    photos = []
    try:
        for item, evidence_type in zip(evidence, photo_types):
            key = f"move-in/{package.pk}/{uuid.uuid4().hex}.{item['extension']}"
            uploaded_keys.append(key)
            upload_private_object(key, item["file"], item["content_type"])
            photos.append(
                {
                    "storage_key": key,
                    "evidence_type": evidence_type,
                    "content_type": item["content_type"],
                    "size_bytes": item["size_bytes"],
                    "name": f"Handover photo {len(package.handover_photos) + len(photos) + 1}.{item['extension']}",
                }
            )
        with transaction.atomic():
            locked_package = (
                MoveInPackage.objects.select_for_update()
                .filter(
                    pk=package.pk,
                    handover_status=MoveInPackage.HANDOVER_STATUS_PENDING,
                    tenancy__unit__property__in=_accessible_properties(request.user),
                )
                .select_related("tenancy__tenant", "tenancy__unit__property")
                .first()
            )
            if locked_package is None:
                _cleanup_tenant_evidence(uploaded_keys)
                return _problem(
                    409,
                    "Handover is no longer editable",
                    "Photos cannot be added after handover submission.",
                    "move-in-handover-conflict",
                )
            if len(locked_package.handover_photos) + len(photos) > 10:
                _cleanup_tenant_evidence(uploaded_keys)
                return _problem(
                    409,
                    "Too many photos",
                    "A move-in handover can include no more than 10 photos.",
                    "too-many-handover-photos",
                )
            locked_package.handover_photos = locked_package.handover_photos + photos
            locked_package.save(update_fields=["handover_photos", "updated_at"])
    except (BotoCoreError, ClientError, ImproperlyConfigured, OSError):
        _cleanup_tenant_evidence(uploaded_keys)
        logger.exception("Move-in handover photo upload failed.")
        return _problem(
            503,
            "Evidence storage unavailable",
            "Photos could not be stored. Please try again later.",
            "evidence-storage-unavailable",
        )
    except DatabaseError:
        _cleanup_tenant_evidence(uploaded_keys)
        logger.exception("Could not save move-in handover photos.")
        return _problem(
            500,
            "Handover photos could not be saved",
            "The request could not be saved. Please try again.",
            "handover-photo-save-failed",
        )
    record_audit_event(
        request,
        "move_in_handover.photos_uploaded",
        "MoveInPackage",
        new_values={"id": locked_package.pk, "photo_count": len(locked_package.handover_photos)},
    )
    return JsonResponse(
        {
            "handover": _move_in_handover_payload(
                locked_package,
                "/api/caretaker/checklists",
            )
        }
    )


@require_POST
def submit_caretaker_move_in_handover(request, package_id):
    error = _caretaker_dashboard_error(request)
    if error:
        return error
    body = _json_body(request)
    if not isinstance(body, dict):
        return _problem(
            400,
            "Invalid handover",
            "Submit keys, condition checklist, and initial meter readings.",
            "invalid-move-in-handover",
        )
    keys_issued = body.get("keys_issued")
    if (
        isinstance(keys_issued, bool)
        or not isinstance(keys_issued, int)
        or not 0 <= keys_issued <= 32767
    ):
        return _problem(
            400,
            "Invalid key count",
            "The number of keys issued must be a whole number from 0 to 32,767.",
            "invalid-key-count",
        )
    package = (
        MoveInPackage.objects.filter(
            pk=package_id,
            handover_status=MoveInPackage.HANDOVER_STATUS_PENDING,
            tenancy__status=Tenancy.STATUS_ACTIVE,
            tenancy__unit__property__in=_accessible_properties(request.user),
        )
        .filter(
            models.Q(assigned_caretaker=request.user)
            | models.Q(assigned_caretaker__isnull=True)
        )
        .select_related("tenancy__tenant", "tenancy__unit__property")
        .first()
    )
    if package is None:
        return _problem(
            404,
            "Handover not found",
            "The move-in handover is unavailable or outside your assigned properties.",
            "move-in-handover-not-found",
        )
    if timezone.localdate() < package.tenancy.start_date:
        return _problem(
            409,
            "Move-in inspection not due",
            f"The move-in inspection can be submitted on {package.tenancy.start_date.isoformat()}.",
            "move-in-inspection-not-due",
        )
    try:
        normalized_inventory = _validated_move_in_handover_inventory(
            package,
            body.get("handover_inventory"),
        )
    except ValueError as exc:
        return _problem(
            400,
            "Invalid handover inventory",
            str(exc),
            "invalid-handover-inventory",
        )
    conditions = body.get("condition_checklist")
    required_conditions = _move_in_condition_requirements(package.tenancy.unit)
    if not isinstance(conditions, list) or len(conditions) != len(required_conditions):
        return _problem(
            400,
            "Incomplete condition checklist",
            "Record the condition of every required area in each room.",
            "invalid-condition-checklist",
        )
    normalized_conditions = []
    seen_conditions = set()
    required_condition_keys = {
        (required["room"], required["area"])
        for required in required_conditions
    }
    for condition in conditions:
        if not isinstance(condition, dict):
            return _problem(400, "Invalid condition checklist", "Each condition entry must be an object.", "invalid-condition-checklist")
        room = condition.get("room")
        area = condition.get("area")
        status = condition.get("status")
        notes = condition.get("notes", "")
        if not isinstance(room, str) or not isinstance(area, str) or not isinstance(status, str):
            return _problem(400, "Invalid condition checklist", "Room, area, and status must be text values.", "invalid-condition-checklist")
        condition_key = (room, area)
        if (
            condition_key not in required_condition_keys
            or condition_key in seen_conditions
            or status not in MOVE_IN_CONDITION_STATUSES
            or not isinstance(notes, str)
            or len(notes) > 2000
        ):
            return _problem(
                400,
                "Invalid condition checklist",
                "Condition entries must use each required area once, a supported status, and notes no longer than 2,000 characters.",
                "invalid-condition-checklist",
            )
        if status == "needs_attention" and not notes.strip():
            return _problem(
                400,
                "Condition notes required",
                f"Add notes describing the issue with {area.lower()}.",
                "condition-notes-required",
            )
        seen_conditions.add(condition_key)
        normalized_conditions.append(
            {"room": room, "area": area, "status": status, "notes": notes.strip()}
        )
    if seen_conditions != required_condition_keys:
        return _problem(
            400,
            "Incomplete condition checklist",
            "Record the condition of every required area in each room.",
            "invalid-condition-checklist",
        )

    raw_readings = body.get("initial_meter_readings")
    if not isinstance(raw_readings, list):
        return _problem(
            400,
            "Invalid meter readings",
            "Initial meter readings must be submitted as a list.",
            "invalid-initial-meter-readings",
        )
    normalized_readings = []
    seen_utility_types = set()
    for reading in raw_readings:
        if not isinstance(reading, dict):
            return _problem(400, "Invalid meter reading", "Each meter reading must be an object.", "invalid-initial-meter-readings")
        utility_type = reading.get("utility_type")
        value = reading.get("current_value")
        if (
            not isinstance(utility_type, str)
            or utility_type not in MOVE_IN_METER_TYPES
            or utility_type in seen_utility_types
            or isinstance(value, bool)
            or not isinstance(value, (str, int, float, Decimal))
        ):
            return _problem(
                400,
                "Invalid meter reading",
                "Provide one numeric opening reading per metered water or electricity utility.",
                "invalid-initial-meter-readings",
            )
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError):
            decimal_value = None
        if (
            decimal_value is None
            or not decimal_value.is_finite()
            or decimal_value < 0
            or decimal_value.as_tuple().exponent < -4
            or decimal_value.adjusted() >= 10
        ):
            return _problem(
                400,
                "Invalid meter reading",
                "Meter readings must be finite, non-negative numbers with at most four decimal places.",
                "invalid-initial-meter-readings",
            )
        seen_utility_types.add(utility_type)
        normalized_readings.append(
            {"utility_type": utility_type, "current_value": format(decimal_value, "f")}
        )

    requirements = _move_in_meter_requirements(package)
    required_types = {item["utility_type"] for item in requirements}
    if any(not item["configured"] for item in requirements):
        return _problem(
            409,
            "Meter configuration required",
            "Configure a meter identifier for each metered utility before submitting the handover.",
            "meter-configuration-required",
        )
    if seen_utility_types != required_types:
        return _problem(
            400,
            "Opening readings required",
            "Submit exactly one opening reading for each metered water or electricity utility.",
            "incomplete-initial-meter-readings",
        )
    requirement_by_type = {item["utility_type"]: item for item in requirements}
    normalized_readings = [
        {
            **reading,
            "name": requirement_by_type[reading["utility_type"]]["name"],
            "meter": requirement_by_type[reading["utility_type"]]["meter"],
        }
        for reading in normalized_readings
    ]
    photo_error = _move_in_handover_photo_error(package)
    if photo_error:
        return _problem(
            400,
            "Handover photos required",
            photo_error,
            "handover-photos-required",
        )

    try:
        with transaction.atomic():
            locked_package = (
                MoveInPackage.objects.select_for_update()
                .filter(
                    pk=package.pk,
                    handover_status=MoveInPackage.HANDOVER_STATUS_PENDING,
                    tenancy__status=Tenancy.STATUS_ACTIVE,
                    tenancy__unit__property__in=_accessible_properties(request.user),
                )
                .select_related("tenancy__tenant", "tenancy__unit__property")
                .first()
            )
            if locked_package is None:
                return _problem(
                    409,
                    "Handover is no longer editable",
                    "This handover has already been submitted or is no longer active.",
                    "move-in-handover-conflict",
                )
            photo_error = _move_in_handover_photo_error(locked_package)
            if photo_error:
                return _problem(
                    400,
                    "Handover photos required",
                    photo_error,
                    "handover-photos-required",
                )
            tenancy = Tenancy.objects.select_for_update().select_related("unit").get(
                pk=locked_package.tenancy_id
            )
            locked_package.keys_issued = keys_issued
            locked_package.handover_inventory = normalized_inventory
            locked_package.condition_checklist = normalized_conditions
            locked_package.initial_meter_readings = normalized_readings
            locked_package.handover_status = MoveInPackage.HANDOVER_STATUS_SUBMITTED
            locked_package.handover_submitted_at = timezone.now()
            locked_package.handover_submitted_by = request.user
            locked_package.save(
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
                    package=locked_package,
                    title=title,
                    defaults={
                        "status": MoveInChecklistItem.STATUS_COMPLETED,
                        "notes": notes,
                        "completed_at": locked_package.handover_submitted_at,
                    },
                )
                if item.status != MoveInChecklistItem.STATUS_COMPLETED:
                    item.status = MoveInChecklistItem.STATUS_COMPLETED
                    item.notes = notes
                    item.completed_at = locked_package.handover_submitted_at
                    item.save(update_fields=["status", "notes", "completed_at"])
            record_audit_event(
                request,
                "move_in_handover.submitted",
                "MoveInPackage",
                new_values={
                    "id": locked_package.pk,
                    "tenancy_id": locked_package.tenancy_id,
                    "keys_issued": keys_issued,
                    "handover_inventory": normalized_inventory,
                    "condition_checklist": normalized_conditions,
                    "initial_meter_readings": normalized_readings,
                    "photo_count": len(locked_package.handover_photos),
                },
            )
            queued = _queue_property_staff_sms(
                tenancy.unit.property,
                (BRANCH_MANAGER_ROLE, SUPER_ADMIN_ROLE, "Property Officer / Agent"),
                "move_in_handover_submitted",
                {
                    "tenant_name": tenancy.tenant.name,
                    "property_name": tenancy.unit.property.name,
                    "unit_code": tenancy.unit.code,
                },
                request.user,
                request,
            )
            if not queued:
                raise ValueError(
                    "No scoped Property Officer or branch manager is available for SMS notification."
                )
    except (ImproperlyConfigured, ValueError) as exc:
        logger.exception("Could not queue move-in handover submission SMS.")
        return _problem(
            503,
            "Handover notification unavailable",
            str(exc),
            "move-in-handover-notification-unavailable",
        )
    except DatabaseError:
        logger.exception("Could not submit move-in handover.")
        return _problem(
            500,
            "Handover could not be submitted",
            "The request could not be saved. Please try again.",
            "move-in-handover-save-failed",
        )
    return JsonResponse(
        {
            "handover": _move_in_handover_payload(
                locked_package,
                "/api/caretaker/checklists",
            )
        }
    )


@require_GET
def property_officer_move_in_handovers(request):
    error = _caretaker_management_error(request)
    if error:
        return error
    packages = (
        MoveInPackage.objects.filter(
            handover_status=MoveInPackage.HANDOVER_STATUS_SUBMITTED,
            tenancy__unit__property__in=_accessible_properties(request.user),
        )
        .select_related(
            "tenancy__tenant",
            "tenancy__unit__property",
            "handover_submitted_by",
        )
        .order_by("handover_submitted_at", "id")
    )
    return JsonResponse(
        {
            "handovers": [
                _move_in_handover_payload(
                    package,
                    "/api/property-officer/move-in-handovers",
                )
                for package in packages
            ]
        }
    )


@require_POST
def confirm_property_officer_move_in_handover(request, package_id):
    error = _caretaker_management_error(request)
    if error:
        return error
    try:
        with transaction.atomic():
            package = (
                MoveInPackage.objects.select_for_update(of=("self",))
                .filter(
                    pk=package_id,
                    handover_status=MoveInPackage.HANDOVER_STATUS_SUBMITTED,
                    tenancy__unit__property__in=_accessible_properties(request.user),
                )
                .select_related(
                    "tenancy__tenant",
                    "tenancy__unit__property",
                    "handover_submitted_by__profile",
                )
                .first()
            )
            if package is None:
                return _problem(
                    404,
                    "Handover not found",
                    "The submitted handover is unavailable or outside your property access scope.",
                    "move-in-handover-not-found",
                )
            package.handover_status = MoveInPackage.HANDOVER_STATUS_CONFIRMED
            package.handover_confirmed_at = timezone.now()
            package.handover_confirmed_by = request.user
            package.save(
                update_fields=[
                    "handover_status",
                    "handover_confirmed_at",
                    "handover_confirmed_by",
                    "updated_at",
                ]
            )
            record_audit_event(
                request,
                "move_in_handover.confirmed",
                "MoveInPackage",
                new_values={
                    "id": package.pk,
                    "tenancy_id": package.tenancy_id,
                    "confirmed_by": request.user.pk,
                },
            )
            tenancy = Tenancy.objects.select_for_update().select_related("unit").get(
                pk=package.tenancy_id
            )
            meters = dict(tenancy.unit.meters or {})
            has_meter_updates = False
            for reading in package.initial_meter_readings:
                utility_type = reading["utility_type"]
                value = Decimal(reading["current_value"])
                meters[f"{utility_type}_reading"] = format(value, "f")
                has_meter_updates = True
                if utility_type == UtilityConfiguration.UTILITY_WATER:
                    tenancy.base_water_meter_reading = value
            if has_meter_updates:
                tenancy.unit.meters = meters
                tenancy.unit.save(update_fields=["meters", "updated_at"])
            if tenancy.base_water_meter_reading is not None:
                tenancy.save(update_fields=["base_water_meter_reading"])
            context = {
                "tenant_name": package.tenancy.tenant.name,
                "property_name": package.tenancy.unit.property.name,
                "unit_code": package.tenancy.unit.code,
            }
            if not package.tenancy.tenant.phone:
                raise ValueError("The tenant has no phone number for move-in confirmation SMS.")
            queue_template_sms(
                package.tenancy.tenant.phone,
                "move_in_handover_confirmed",
                context,
                actor=request.user,
                request=request,
            )
            caretaker_phone = getattr(
                getattr(package.handover_submitted_by, "profile", None),
                "phone",
                "",
            )
            if not caretaker_phone:
                raise ValueError("The caretaker has no phone number for move-in confirmation SMS.")
            queue_template_sms(
                caretaker_phone,
                "move_in_handover_confirmed",
                context,
                actor=request.user,
                request=request,
            )
    except (ImproperlyConfigured, ValueError) as exc:
        logger.exception("Could not queue move-in handover confirmation SMS.")
        return _problem(
            503,
            "Handover notification unavailable",
            str(exc),
            "move-in-handover-notification-unavailable",
        )
    except DatabaseError:
        logger.exception("Could not confirm move-in handover.")
        return _problem(
            500,
            "Handover could not be confirmed",
            "The request could not be saved. Please try again.",
            "move-in-handover-confirmation-failed",
        )
    return JsonResponse(
        {
            "handover": _move_in_handover_payload(
                package,
                "/api/property-officer/move-in-handovers",
            )
        }
    )


@require_GET
def get_move_in_handover_photo(request, package_id, photo_index):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if _is_caretaker(request.user):
        access_error = _caretaker_dashboard_error(request)
    else:
        access_error = _caretaker_management_error(request)
    if access_error:
        return access_error
    package = (
        MoveInPackage.objects.filter(
            pk=package_id,
            tenancy__unit__property__in=_accessible_properties(request.user),
        )
        .only("id", "handover_photos")
        .first()
    )
    if package is None or photo_index >= len(package.handover_photos):
        return _problem(
            404,
            "Photo not found",
            "The handover photo is unavailable.",
            "handover-photo-not-found",
        )
    photo = package.handover_photos[photo_index]
    storage_key = photo.get("storage_key") if isinstance(photo, dict) else None
    content_type = photo.get("content_type") if isinstance(photo, dict) else None
    if (
        not isinstance(storage_key, str)
        or not storage_key.startswith(f"move-in/{package.pk}/")
        or content_type not in {"image/jpeg", "image/png", "image/webp"}
    ):
        return _problem(404, "Photo not found", "The handover photo is unavailable.", "handover-photo-not-found")
    try:
        content = read_private_object(storage_key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return _problem(404, "Photo not found", "The handover photo is unavailable.", "handover-photo-not-found")
        logger.exception("Could not retrieve move-in handover photo.")
        return _problem(
            503,
            "Evidence storage unavailable",
            "The photo could not be loaded. Please try again later.",
            "evidence-storage-unavailable",
        )
    except (BotoCoreError, ImproperlyConfigured, OSError):
        logger.exception("Could not retrieve move-in handover photo.")
        return _problem(
            503,
            "Evidence storage unavailable",
            "The photo could not be loaded. Please try again later.",
            "evidence-storage-unavailable",
        )
    record_audit_event(
        request,
        "move_in_handover.photo_viewed",
        "MoveInPackage",
        new_values={"id": package.pk, "photo_index": photo_index},
    )
    response = HttpResponse(content, content_type=content_type)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _caretaker_water_period():
    today = timezone.localdate()
    return (today.replace(day=28) + timedelta(days=4)).replace(day=1)


def _caretaker_water_readings_due():
    today = timezone.localdate()
    due_day = min(30, calendar.monthrange(today.year, today.month)[1])
    return today.day >= due_day


def _caretaker_water_properties(user, period_start=None):
    period_start = period_start or _caretaker_water_period()
    return _accessible_properties(user).filter(
        units__utilities__utility_type=UtilityConfiguration.UTILITY_WATER,
        units__utilities__billing_method=UtilityConfiguration.METHOD_METERED,
        units__utilities__effective_from__lte=period_start,
    ).filter(
        Q(units__utilities__effective_to__isnull=True)
        | Q(units__utilities__effective_to__gte=period_start)
    ).distinct()


def _caretaker_water_units(property_obj, period_start):
    tenancies = (
        Tenancy.objects.filter(
            unit__property=property_obj,
            status=Tenancy.STATUS_ACTIVE,
            start_date__lte=month_end(period_start),
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=period_start))
        .select_related("unit", "tenant")
        .order_by("unit__code", "id")
    )
    rows = []
    for tenancy in tenancies:
        unit = tenancy.unit
        utility = (
            unit.utilities.filter(
                utility_type=UtilityConfiguration.UTILITY_WATER,
                billing_method=UtilityConfiguration.METHOD_METERED,
                effective_from__lte=period_start,
            )
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=period_start))
            .order_by("-effective_from", "-id")
            .first()
        )
        if utility is None:
            continue
        meter = str(utility.metadata.get("meter") or unit.meters.get("water") or "").strip()
        if not meter:
            continue
        published = MeterReading.objects.filter(
            unit=unit,
            meter=meter,
            period=period_start,
        ).first()
        previous = MeterReading.objects.filter(
            unit=unit,
            meter=meter,
            period__lt=period_start,
        ).order_by("-period", "-submitted_at", "-id").first()
        draft = MeterReadingDraft.objects.filter(
            unit=unit,
            meter=meter,
            period=period_start,
        ).first()
        previous_value = (
            published.previous_value
            if published
            else draft.previous_value
            if draft
            else previous.current_value
            if previous
            else tenancy.base_water_meter_reading
            if tenancy.base_water_meter_reading is not None
            else unit.meters.get("water_reading")
        )
        baseline_value = (
            tenancy.base_water_meter_reading
            if tenancy.base_water_meter_reading is not None
            else unit.meters.get("water_reading")
        )
        rows.append(
            {
                "unit_id": unit.pk,
                "unit_code": unit.code,
                "meter": meter,
                "previous_value": str(previous_value) if previous_value is not None else None,
                "current_value": (
                    str(published.current_value)
                    if published
                    else str(draft.current_value)
                    if draft
                    else None
                ),
                "status": "published" if published else "draft" if draft else "pending",
                "photo_uploaded": bool(
                    published.photo_key if published else draft.photo_key if draft else ""
                ),
                "baseline_value": (
                    str(baseline_value)
                    if baseline_value is not None
                    else None
                ),
            }
        )
    return rows


@require_GET
def caretaker_water_readings(request):
    error = _caretaker_dashboard_error(request)
    if error:
        return error
    period_start = _caretaker_water_period()
    readings_due = _caretaker_water_readings_due()
    properties = _caretaker_water_properties(request.user).prefetch_related("units")
    property_payloads = []
    for property_obj in properties:
        rows = _caretaker_water_units(property_obj, period_start)
        property_payloads.append(
            {
                "id": property_obj.pk,
                "name": property_obj.name,
                "period": period_start.isoformat(),
                "units": rows,
                "readings_due": readings_due,
                "can_publish": bool(rows) and all(
                    row["status"] == "published"
                    or (
                        row["previous_value"] is not None
                        and row["current_value"] is not None
                        and row["photo_uploaded"]
                    )
                    for row in rows
                ),
                "pending_count": (
                    sum(row["status"] != "published" for row in rows)
                    if readings_due
                    else 0
                ),
            }
        )
    return JsonResponse(
        {
            "period": period_start.isoformat(),
            "readings_due": readings_due,
            "properties": property_payloads,
        }
    )


@require_POST
def save_caretaker_water_reading_drafts(request):
    error = _caretaker_dashboard_error(request)
    if error:
        return error
    body = _json_body(request)
    if not isinstance(body, dict):
        return _problem(400, "Invalid request", "Expected property, period, and readings.", "invalid-readings")
    try:
        property_id = int(body["property_id"])
        period_start = date.fromisoformat(str(body["period"]))
    except (KeyError, TypeError, ValueError):
        return _problem(400, "Invalid request", "A valid property_id and period are required.", "invalid-period")
    if period_start.day != 1:
        return _problem(400, "Invalid period", "The billing period must start on the first of a month.", "invalid-period")
    if period_start != _caretaker_water_period():
        return _problem(400, "Invalid period", "Readings can only be recorded for the upcoming billing month.", "invalid-period")
    property_obj = _caretaker_water_properties(request.user, period_start).filter(pk=property_id).first()
    if property_obj is None:
        return _problem(404, "Property not found", "The property is unavailable or has no metered water.", "property-not-found")
    readings_data = body.get("readings")
    if not isinstance(readings_data, list) or not readings_data:
        return _problem(400, "Invalid request", "readings must be a non-empty array.", "invalid-readings")
    rows = {row["unit_id"]: row for row in _caretaker_water_units(property_obj, period_start)}
    prepared = []
    seen = set()
    for item in readings_data:
        if not isinstance(item, dict):
            return _problem(400, "Invalid reading", "Each reading must be an object.", "invalid-reading")
        try:
            unit_id = int(item["unit_id"])
            current_value = Decimal(str(item["current_value"]))
            supplied_previous = (
                Decimal(str(item["previous_value"]))
                if item.get("previous_value") not in (None, "")
                else None
            )
        except (KeyError, InvalidOperation, TypeError, ValueError):
            return _problem(400, "Invalid reading", "Each reading needs a valid unit_id and current_value.", "invalid-reading")
        if unit_id not in rows or unit_id in seen:
            return _problem(400, "Invalid reading", "Each unit must be eligible and appear only once.", "invalid-reading")
        seen.add(unit_id)
        row = rows[unit_id]
        if row["status"] == "published":
            return _problem(409, "Reading already published", f"Unit {row['unit_code']} is already published for this period.", "reading-already-published")
        known_previous = (
            Decimal(row["previous_value"])
            if row["previous_value"] is not None
            else None
        )
        if known_previous is not None and supplied_previous is not None and supplied_previous != known_previous:
            return _problem(400, "Invalid previous reading", f"Unit {row['unit_code']} previous reading must be {known_previous}.", "invalid-previous-reading")
        previous_value = known_previous if known_previous is not None else supplied_previous
        if previous_value is None:
            return _problem(400, "Baseline required", f"Enter the previous meter value for unit {row['unit_code']}.", "baseline-required")
        draft = MeterReadingDraft(
            unit_id=unit_id,
            meter=row["meter"],
            period=period_start,
            previous_value=previous_value,
            current_value=current_value,
            submitted_by=request.user,
        )
        try:
            draft.clean()
        except ValidationError as validation_error:
            return _problem(400, "Invalid meter reading", str(validation_error.message_dict), "invalid-meter-reading")
        prepared.append(draft)

    with transaction.atomic():
        Property.objects.select_for_update().get(pk=property_obj.pk)
        for draft in prepared:
            saved_draft = MeterReadingDraft.objects.select_for_update().filter(
                unit_id=draft.unit_id,
                meter=draft.meter,
                period=period_start,
            ).first()
            if saved_draft is None:
                draft.save()
                continue
            old_photo_key = saved_draft.photo_key
            values_changed = (
                saved_draft.previous_value != draft.previous_value
                or saved_draft.current_value != draft.current_value
            )
            saved_draft.previous_value = draft.previous_value
            saved_draft.current_value = draft.current_value
            saved_draft.submitted_by = request.user
            if values_changed:
                saved_draft.photo_key = ""
                saved_draft.photo_metadata = {}
            saved_draft.save()
            if values_changed and old_photo_key:
                transaction.on_commit(
                    lambda key=old_photo_key: _cleanup_tenant_evidence([key])
                )
    current_rows = _caretaker_water_units(property_obj, period_start)
    return JsonResponse(
        {
            "saved": len(prepared),
            "pending_count": sum(row["status"] != "published" for row in current_rows),
            "can_publish": bool(current_rows) and all(
                row["status"] == "published"
                or (
                    row["previous_value"] is not None
                    and row["current_value"] is not None
                    and row["photo_uploaded"]
                )
                for row in current_rows
            ),
        }
    )


@require_POST
def upload_caretaker_water_reading_photo(request):
    error = _caretaker_dashboard_error(request)
    if error:
        return error
    try:
        property_id = int(request.POST["property_id"])
        unit_id = int(request.POST["unit_id"])
        period_start = date.fromisoformat(request.POST["period"])
    except (KeyError, TypeError, ValueError):
        return _problem(
            400,
            "Invalid request",
            "A valid property_id, unit_id, and period are required.",
            "invalid-meter-evidence",
        )
    if period_start != _caretaker_water_period():
        return _problem(
            400,
            "Invalid period",
            "Evidence can only be uploaded for the upcoming billing month.",
            "invalid-period",
        )
    property_obj = _caretaker_water_properties(request.user, period_start).filter(
        pk=property_id
    ).first()
    if property_obj is None:
        return _problem(
            404,
            "Property not found",
            "The property is unavailable or has no metered water.",
            "property-not-found",
        )
    row = next(
        (
            item
            for item in _caretaker_water_units(property_obj, period_start)
            if item["unit_id"] == unit_id
        ),
        None,
    )
    if row is None:
        return _problem(
            404,
            "Unit not found",
            "The unit is unavailable or does not require a meter reading.",
            "unit-not-found",
        )
    upload = request.FILES.get("photo")
    if upload is None:
        return _problem(400, "Invalid evidence", "A meter photo is required.", "invalid-evidence")
    try:
        evidence = _prepare_tenant_evidence([upload])[0]
    except ValueError as exc:
        return _problem(400, "Invalid evidence", str(exc), "invalid-evidence")

    draft = MeterReadingDraft.objects.filter(
        unit_id=unit_id,
        meter=row["meter"],
        period=period_start,
    ).first()
    if draft is None:
        return _problem(
            409,
            "Reading not saved",
            "Save the current meter reading before uploading its photo.",
            "reading-not-saved",
        )

    storage_key = (
        f"water-meter-readings/{property_obj.pk}/{period_start.isoformat()}/"
        f"{unit_id}/{uuid.uuid4().hex}.{evidence['extension']}"
    )
    old_storage_key = ""
    try:
        upload_private_object(storage_key, evidence["file"], evidence["content_type"])
        with transaction.atomic():
            Property.objects.select_for_update().get(pk=property_obj.pk)
            locked_draft = MeterReadingDraft.objects.select_for_update().filter(
                pk=draft.pk,
            ).first()
            if (
                locked_draft is None
                or locked_draft.previous_value != draft.previous_value
                or locked_draft.current_value != draft.current_value
            ):
                _cleanup_tenant_evidence([storage_key])
                return _problem(
                    409,
                    "Reading changed",
                    "The saved reading changed before its photo could be attached. Save it again and retry.",
                    "reading-changed",
                )
            old_storage_key = locked_draft.photo_key
            locked_draft.photo_key = storage_key
            locked_draft.photo_metadata = {
                "content_type": evidence["content_type"],
                "size_bytes": evidence["size_bytes"],
                "name": f"Meter evidence.{evidence['extension']}",
            }
            locked_draft.full_clean()
            locked_draft.save(update_fields=["photo_key", "photo_metadata", "updated_at"])
            record_audit_event(
                request,
                "meter_reading.evidence_uploaded",
                "MeterReadingDraft",
                new_values={
                    "unit_id": unit_id,
                    "property_id": property_obj.pk,
                    "period": period_start.isoformat(),
                    "size_bytes": evidence["size_bytes"],
                    "content_type": evidence["content_type"],
                },
            )
    except (BotoCoreError, ClientError, ImproperlyConfigured, OSError):
        _cleanup_tenant_evidence([storage_key])
        logger.exception("Caretaker meter evidence upload failed.")
        return _problem(
            503,
            "Evidence storage unavailable",
            "The meter photo could not be stored. Please try again.",
            "meter-evidence-unavailable",
        )
    except ValidationError as validation_error:
        _cleanup_tenant_evidence([storage_key])
        return _problem(
            400,
            "Invalid evidence",
            str(validation_error.message_dict),
            "invalid-evidence",
        )
    except DatabaseError:
        _cleanup_tenant_evidence([storage_key])
        logger.exception("Could not save caretaker meter evidence metadata.")
        return _problem(
            503,
            "Evidence could not be saved",
            "The meter photo could not be attached to the saved reading. Please try again.",
            "meter-evidence-unavailable",
        )
    if old_storage_key:
        transaction.on_commit(
            lambda key=old_storage_key: _cleanup_tenant_evidence([key])
        )
    return JsonResponse(
        {
            "unit_id": unit_id,
            "photo_uploaded": True,
            "photo_metadata": {
                "content_type": evidence["content_type"],
                "size_bytes": evidence["size_bytes"],
            },
        },
        status=201,
    )


@require_http_methods(["PATCH"])
def publish_caretaker_water_readings(request):
    error = _caretaker_dashboard_error(request)
    if error:
        return error
    body = _json_body(request)
    if not isinstance(body, dict):
        return _problem(400, "Invalid request", "Expected property_id and period.", "invalid-readings")
    try:
        property_id = int(body["property_id"])
        period_start = date.fromisoformat(str(body["period"]))
    except (KeyError, TypeError, ValueError):
        return _problem(400, "Invalid request", "A valid property_id and period are required.", "invalid-period")
    if period_start.day != 1:
        return _problem(400, "Invalid period", "The billing period must start on the first of a month.", "invalid-period")
    if period_start != _caretaker_water_period():
        return _problem(400, "Invalid period", "Only the upcoming billing month can be published.", "invalid-period")
    property_obj = _caretaker_water_properties(request.user, period_start).filter(pk=property_id).first()
    if property_obj is None:
        return _problem(404, "Property not found", "The property is unavailable or has no metered water.", "property-not-found")
    rows = _caretaker_water_units(property_obj, period_start)
    if not rows:
        return _problem(409, "No readings required", "There are no occupied units requiring water readings.", "no-readings-required")

    missing = [
        row["unit_code"]
        for row in rows
        if row["status"] != "published"
        and (
            row["previous_value"] is None
            or row["current_value"] is None
            or not row["photo_uploaded"]
        )
    ]
    if missing:
        return _problem(
            409,
            "Readings incomplete",
            f"Save a valid reading and meter photo for every occupied unit before publishing: {', '.join(missing)}.",
            "readings-incomplete",
        )
    prepared_readings = []
    drafts_to_delete = []
    with transaction.atomic():
        Property.objects.select_for_update().get(pk=property_obj.pk)
        for row in rows:
            if row["status"] == "published":
                continue
            draft = MeterReadingDraft.objects.select_for_update().filter(
                unit_id=row["unit_id"],
                meter=row["meter"],
                period=period_start,
            ).first()
            if draft is None:
                return _problem(409, "Readings incomplete", f"Unit {row['unit_code']} has no saved draft.", "readings-incomplete")
            reading = MeterReading(
                unit_id=row["unit_id"],
                meter=row["meter"],
                period=period_start,
                previous_value=draft.previous_value,
                current_value=draft.current_value,
                submitted_by=draft.submitted_by,
                photo_key=draft.photo_key,
                photo_metadata=draft.photo_metadata,
            )
            try:
                reading.full_clean()
            except ValidationError as validation_error:
                return _problem(400, "Invalid meter reading", str(validation_error.message_dict), "invalid-meter-reading")
            prepared_readings.append(reading)
            drafts_to_delete.append(draft)
        MeterReading.objects.bulk_create(prepared_readings)
        MeterReadingDraft.objects.filter(
            pk__in=[draft.pk for draft in drafts_to_delete]
        ).delete()
        for reading in prepared_readings:
            record_audit_event(
                request,
                "meter_reading.published",
                "MeterReading",
                new_values=_meter_reading_payload(reading),
            )
    from users.tasks import draft_property_invoices

    try:
        draft_property_invoices.delay(property_obj.pk, period_start.isoformat())
    except BrokerOperationalError:
        logger.exception(
            "Published caretaker readings but could not queue invoice drafting for property %s, period %s.",
            property_obj.pk,
            period_start,
        )
        return _problem(
            503,
            "Invoice drafting delayed",
            "Readings are published. Invoice drafting could not be queued; the scheduled billing task will retry.",
            "invoice-drafting-unavailable",
        )
    return JsonResponse(
        {
            "published": len(rows),
            "period": period_start.isoformat(),
            "message": "All water readings have been published; invoice drafts are being generated.",
        },
        status=201,
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
    error = _property_read_access_error(request)
    if error:
        return error
    properties = _properties_visible_to_user(request.user).prefetch_related(
        "caretakers", "blocks__floors", "documents", "unit_types__utilities"
    )
    payload = [_property_payload(item) for item in properties]
    if _has_role(request.user, LANDLORD_PORTAL_ROLE):
        record_audit_event(
            request,
            "landlord_properties.viewed",
            "LandlordPropertyList",
            new_values={"property_count": len(payload)},
        )
    return JsonResponse({"properties": payload})


@require_GET
def get_property(request, property_id):
    error = _property_read_access_error(request)
    if error:
        return error
    property_obj = (
        _properties_visible_to_user(request.user)
        .prefetch_related(
            "caretakers",
            "blocks__floors",
            "documents",
            "unit_types__utilities",
        )
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


@require_http_methods(["PATCH"])
def update_property_move_in_settings(request, property_id):
    error = _property_officer_onboarding_error(request)
    if error:
        return error
    body = _json_body(request)
    if body is None or not any(
        field in body for field in ("move_in_letting_fee", "default_handover_items")
    ):
        return _problem(
            400,
            "Invalid property settings",
            "Provide a letting fee or default handover inventory.",
            "invalid-property-settings",
        )
    property_obj = _accessible_properties(request.user).filter(pk=property_id).first()
    if property_obj is None:
        return _problem(404, "Property not found", "The property is unavailable or outside your access scope.", "property-not-found")
    old_values = {
        "move_in_letting_fee": str(property_obj.move_in_letting_fee),
        "default_handover_items": property_obj.default_handover_items,
    }
    update_fields = []
    if "move_in_letting_fee" in body:
        try:
            letting_fee = Decimal(str(body["move_in_letting_fee"]))
        except (InvalidOperation, TypeError, ValueError):
            return _problem(400, "Invalid letting fee", "move_in_letting_fee must be a valid amount.", "invalid-letting-fee")
        if not letting_fee.is_finite() or letting_fee < 0:
            return _problem(400, "Invalid letting fee", "move_in_letting_fee must be a finite, non-negative amount.", "invalid-letting-fee")
        property_obj.move_in_letting_fee = letting_fee.quantize(Decimal("0.01"))
        update_fields.append("move_in_letting_fee")
    if "default_handover_items" in body:
        try:
            property_obj.default_handover_items = _validated_handover_inventory(
                body["default_handover_items"]
            )
        except ValueError as exc:
            return _problem(400, "Invalid handover inventory", str(exc), "invalid-handover-inventory")
        update_fields.append("default_handover_items")
    try:
        property_obj.full_clean()
        property_obj.save(update_fields=[*update_fields, "updated_at"])
    except ValidationError as exc:
        return _problem(400, "Invalid property settings", "; ".join(exc.messages), "invalid-property-settings")
    record_audit_event(
        request,
        "property.move_in_settings_updated",
        "Property",
        old_values=old_values,
        new_values={
            "move_in_letting_fee": str(property_obj.move_in_letting_fee),
            "default_handover_items": property_obj.default_handover_items,
        },
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
        try:
            if isinstance(body["landlord_id"], bool):
                raise TypeError
            landlord_id = int(body["landlord_id"])
        except (TypeError, ValueError):
            return _problem(400, "Invalid landlord", "landlord_id must be an integer.", "invalid-landlord")
        landlord = _accessible_landlords(request.user).filter(
            pk=landlord_id,
            branch=branch,
        ).first()
        if landlord is None:
            return _problem(
                400,
                "Invalid landlord",
                "The landlord must belong to the selected property branch and be visible to you.",
                "invalid-landlord",
            )
    if (
        not isinstance(body.get("amenities") or [], list)
        or not isinstance(body.get("gps_coordinates") or {}, dict)
        or not isinstance(body.get("house_rules") or {}, dict)
        or not isinstance(body.get("photos") or [], list)
    ):
        return _problem(
            400,
            "Invalid property details",
            "gps_coordinates and house_rules must be objects; amenities and photos must be arrays.",
            "invalid-property",
        )
    house_rules = body.get("house_rules") or {}
    if "statements" in house_rules and (
        not isinstance(house_rules["statements"], list)
        or len(house_rules["statements"]) > 50
        or any(
            not isinstance(statement, str)
            or not statement.strip()
            or len(statement.strip()) > 500
            for statement in house_rules["statements"]
        )
    ):
        return _problem(
            400,
            "Invalid house rules",
            "House rule statements must be non-empty text of no more than 500 characters, with at most 50 statements.",
            "invalid-property-rules",
        )
    expected_unit_count = body.get("expected_unit_count", 0)
    if isinstance(expected_unit_count, bool):
        return _problem(400, "Invalid unit count", "expected_unit_count must be a whole number from 0 to 500.", "invalid-property")
    try:
        expected_unit_count = int(expected_unit_count)
    except (TypeError, ValueError):
        return _problem(400, "Invalid unit count", "expected_unit_count must be a whole number from 0 to 500.", "invalid-property")
    if expected_unit_count < 0 or expected_unit_count > 500:
        return _problem(400, "Invalid unit count", "expected_unit_count must be a whole number from 0 to 500.", "invalid-property")
    default_unit_type = body.get("default_unit_type", "")
    if not isinstance(default_unit_type, str):
        return _problem(400, "Invalid unit type", "default_unit_type must be text.", "invalid-property")
    raw_unit_types = body.get("unit_types", [])
    if not isinstance(raw_unit_types, list) or len(raw_unit_types) > 50:
        return _problem(
            400,
            "Invalid unit types",
            "unit_types must be an array containing no more than 50 definitions.",
            "invalid-property",
        )

    def amount(value, field, *, optional=False):
        if value in (None, "") and optional:
            return None
        if isinstance(value, bool) or value in (None, ""):
            raise ValueError(f"{field} is required.")
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a valid amount.") from exc
        if not parsed.is_finite() or parsed < 0:
            raise ValueError(f"{field} must be a finite, non-negative amount.")
        return parsed

    prepared_unit_types = []
    try:
        for index, raw_unit_type in enumerate(raw_unit_types, start=1):
            if not isinstance(raw_unit_type, dict):
                raise ValueError(f"Unit type {index} must be an object.")
            unit_type_name = raw_unit_type.get("name")
            if (
                not isinstance(unit_type_name, str)
                or not unit_type_name.strip()
                or len(unit_type_name.strip()) > 100
            ):
                raise ValueError(f"Unit type {index} needs a name of at most 100 characters.")
            raw_unit_count = raw_unit_type.get("unit_count")
            if isinstance(raw_unit_count, bool) or raw_unit_count in (None, ""):
                raise ValueError(f"Unit count for {unit_type_name} is required.")
            try:
                unit_count = int(raw_unit_count)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Unit count for {unit_type_name} must be a whole number from 0 to 500.") from exc
            if (
                unit_count < 0
                or unit_count > 500
                or (isinstance(raw_unit_count, str) and not re.fullmatch(r"\d+", raw_unit_count.strip()))
                or (not isinstance(raw_unit_count, str) and unit_count != raw_unit_count)
            ):
                raise ValueError(f"Unit count for {unit_type_name} must be a whole number from 0 to 500.")
            utilities = raw_unit_type.get("utilities", [])
            if not isinstance(utilities, list) or len(utilities) > 30:
                raise ValueError(f"Utilities for unit type {unit_type_name} must be an array of at most 30 items.")
            prepared_utilities = []
            for utility_index, utility in enumerate(utilities, start=1):
                if not isinstance(utility, dict):
                    raise ValueError(f"Utility {utility_index} for {unit_type_name} must be an object.")
                utility_name = utility.get("name")
                utility_type = utility.get("utility_type")
                billing_method = utility.get("billing_method")
                if (
                    not isinstance(utility_name, str)
                    or not utility_name.strip()
                    or len(utility_name.strip()) > 100
                ):
                    raise ValueError(f"Utility {utility_index} for {unit_type_name} needs a name.")
                if (
                    not isinstance(utility_type, str)
                    or utility_type not in dict(PropertyUnitTypeUtility.UTILITY_CHOICES)
                ):
                    raise ValueError(f"Utility {utility_name} has an unsupported utility type.")
                if (
                    not isinstance(billing_method, str)
                    or billing_method not in dict(PropertyUnitTypeUtility.BILLING_METHOD_CHOICES)
                ):
                    raise ValueError(f"Utility {utility_name} has an unsupported billing method.")
                if (
                    billing_method == PropertyUnitTypeUtility.METHOD_METERED
                    and utility_type != PropertyUnitTypeUtility.UTILITY_WATER
                ):
                    raise ValueError(
                        "Only water utilities can use metered billing until other meter billing is supported."
                    )
                fixed_amount = amount(
                    utility.get("fixed_amount"),
                    f"{utility_name} monthly amount",
                    optional=billing_method == PropertyUnitTypeUtility.METHOD_METERED,
                )
                rate_per_unit = amount(
                    utility.get("rate_per_unit"),
                    f"{utility_name} rate per unit",
                    optional=billing_method == PropertyUnitTypeUtility.METHOD_FIXED,
                )
                if billing_method == PropertyUnitTypeUtility.METHOD_FIXED and rate_per_unit is not None:
                    raise ValueError(f"Fixed utility {utility_name} cannot include a metered rate.")
                if billing_method == PropertyUnitTypeUtility.METHOD_METERED and fixed_amount is not None:
                    raise ValueError(f"Metered utility {utility_name} cannot include a fixed amount.")
                prepared_utilities.append(
                    {
                        "utility_type": utility_type,
                        "name": utility_name.strip(),
                        "billing_method": billing_method,
                        "fixed_amount": fixed_amount,
                        "rate_per_unit": rate_per_unit,
                        "deposit_amount": amount(
                            utility.get("deposit_amount"),
                            f"{utility_name} deposit",
                            optional=True,
                        ),
                    }
                )
            prepared_unit_types.append(
                {
                    "name": unit_type_name.strip(),
                    "unit_count": unit_count,
                    "monthly_rent": amount(
                        raw_unit_type.get("monthly_rent"),
                        f"Monthly rent for {unit_type_name}",
                    ),
                    "security_deposit": amount(
                        raw_unit_type.get("security_deposit", 0),
                        f"Security deposit for {unit_type_name}",
                    ),
                    "utilities": prepared_utilities,
                }
            )
    except ValueError as exc:
        return _problem(400, "Invalid unit type details", str(exc), "invalid-property-unit-types")

    if not prepared_unit_types and default_unit_type.strip():
        try:
            legacy_rent = amount(body.get("default_rent", 0), "Default monthly rent")
            legacy_security_deposit = amount(
                body.get("security_deposit", 0),
                "Default security deposit",
            )
            legacy_water_deposit = amount(body.get("water_deposit", 0), "Water deposit")
            legacy_water_rate = amount(body.get("default_water_rate", 0), "Default water rate")
            legacy_garbage_charge = amount(
                body.get("default_garbage_charge", 0),
                "Default garbage charge",
            )
        except ValueError as exc:
            return _problem(400, "Invalid unit type details", str(exc), "invalid-property-unit-types")
        legacy_utilities = []
        if (
            body.get("billing_mode", Property.BILLING_MODE_METERED_WATER)
            == Property.BILLING_MODE_METERED_WATER
            and legacy_water_rate > 0
        ):
            legacy_utilities.append(
                {
                    "utility_type": PropertyUnitTypeUtility.UTILITY_WATER,
                    "name": "Water",
                    "billing_method": PropertyUnitTypeUtility.METHOD_METERED,
                    "fixed_amount": None,
                    "rate_per_unit": legacy_water_rate,
                    "deposit_amount": legacy_water_deposit,
                }
            )
        if legacy_garbage_charge > 0:
            legacy_utilities.append(
                {
                    "utility_type": PropertyUnitTypeUtility.UTILITY_OTHER,
                    "name": "Garbage",
                    "billing_method": PropertyUnitTypeUtility.METHOD_FIXED,
                    "fixed_amount": legacy_garbage_charge,
                    "rate_per_unit": None,
                    "deposit_amount": None,
                }
            )
        prepared_unit_types.append(
            {
                "name": default_unit_type.strip(),
                "unit_count": expected_unit_count,
                "monthly_rent": legacy_rent,
                "security_deposit": legacy_security_deposit,
                "utilities": legacy_utilities,
            }
        )

    if sum(unit_type["unit_count"] for unit_type in prepared_unit_types) != expected_unit_count:
        return _problem(
            400,
            "Unit counts do not match",
            "The sum of unit counts across all unit types must equal expected_unit_count.",
            "invalid-property-unit-counts",
        )

    try:
        move_in_letting_fee = amount(
            body.get("move_in_letting_fee", 0),
            "Move-in letting fee",
        )
    except ValueError as exc:
        return _problem(400, "Invalid letting fee", str(exc), "invalid-letting-fee")
    raw_handover_items = body.get("default_handover_items", [])
    try:
        default_handover_items = _validated_handover_inventory(raw_handover_items)
    except ValueError as exc:
        return _problem(400, "Invalid handover inventory", str(exc), "invalid-handover-inventory")

    if prepared_unit_types:
        first_unit_type = prepared_unit_types[0]
        default_unit_type = first_unit_type["name"]
        default_rent = first_unit_type["monthly_rent"]
        security_deposit = first_unit_type["security_deposit"]
        water_utility = next(
            (
                item
                for item in first_unit_type["utilities"]
                if item["utility_type"] == PropertyUnitTypeUtility.UTILITY_WATER
            ),
            None,
        )
        water_deposit = (
            water_utility["deposit_amount"]
            if water_utility and water_utility["deposit_amount"] is not None
            else 0
        )
        default_water_rate = (
            water_utility["rate_per_unit"]
            if water_utility
            and water_utility["billing_method"] == PropertyUnitTypeUtility.METHOD_METERED
            else 0
        )
    else:
        default_rent = body.get("default_rent", 0)
        security_deposit = body.get("security_deposit", 0)
        water_deposit = body.get("water_deposit", 0)
        default_water_rate = body.get("default_water_rate", 0)
    property_obj = Property(
        branch=branch,
        landlord=landlord,
        name=body["name"].strip(),
        property_type=body["property_type"].strip(),
        location=body["location"].strip(),
        address=str(body.get("address") or "").strip(),
        rent_due_day=body.get("rent_due_day", 10),
        billing_mode=body.get("billing_mode", Property.BILLING_MODE_METERED_WATER),
        expected_unit_count=expected_unit_count,
        default_unit_type=default_unit_type.strip(),
        default_rent=default_rent,
        security_deposit=security_deposit,
        move_in_letting_fee=move_in_letting_fee,
        water_deposit=water_deposit,
        default_garbage_charge=body.get("default_garbage_charge", 0),
        default_water_rate=default_water_rate,
        default_handover_items=default_handover_items,
        gps_coordinates=body.get("gps_coordinates") or {},
        amenities=body.get("amenities") or [],
        house_rules=house_rules,
        photos=body.get("photos") or [],
    )
    if body.get("units") not in (None, []):
        return _problem(
            400,
            "Unit import required",
            "Property registration does not create units. Import unit records after registering the property.",
            "invalid-property-units",
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
        profile__agency_memberships__agency=branch.agency,
        profile__agency_memberships__agency__is_active=True,
        profile__agency_memberships__roles__name=CARETAKER_ROLE,
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
            for unit_type_data in prepared_unit_types:
                utilities = unit_type_data.pop("utilities")
                unit_type = PropertyUnitType(
                    property=property_obj,
                    **unit_type_data,
                )
                unit_type.full_clean()
                unit_type.save()
                for utility_data in utilities:
                    utility = PropertyUnitTypeUtility(
                        unit_type=unit_type,
                        **utility_data,
                    )
                    utility.full_clean()
                    utility.save()
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
    except (ValidationError, IntegrityError, ValueError) as error:
        detail = error.message_dict if isinstance(error, ValidationError) else str(error)
        return _problem(400, "Invalid property", str(detail), "invalid-property")
    record_audit_event(
        request,
        "property.created",
        "Property",
        new_values={
            "id": property_obj.pk,
            "branch_id": property_obj.branch_id,
            "unit_type_count": len(prepared_unit_types),
        },
    )
    return JsonResponse(
        {
            "property": _property_payload(property_obj),
            "units": [],
        },
        status=201,
    )


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
    error = _property_read_access_error(request)
    if error:
        return error
    units = list(
        Unit.objects.filter(property__in=_properties_visible_to_user(request.user))
        .select_related("property__branch", "floor__block", "unit_type_definition")
        .order_by("property__name", "code", "id")
    )
    current_rents = {}
    for charge in UnitCharge.objects.filter(
        unit_id__in=[unit.pk for unit in units],
        charge_type=UnitCharge.CHARGE_RENT,
        effective_from__lte=timezone.localdate(),
    ).filter(
        models.Q(effective_to__isnull=True)
        | models.Q(effective_to__gte=timezone.localdate())
    ).order_by("unit_id", "-effective_from", "-id"):
        current_rents.setdefault(charge.unit_id, charge)
    payload = [
        {
            **_unit_payload(unit),
            "property_name": unit.property.name,
            "branch_name": unit.property.branch.name,
            "rent": (
                str(current_rents[unit.pk].amount)
                if unit.pk in current_rents
                else None
            ),
        }
        for unit in units
    ]
    if _has_role(request.user, LANDLORD_PORTAL_ROLE):
        record_audit_event(
            request,
            "landlord_units.viewed",
            "LandlordUnitList",
            new_values={"unit_count": len(payload)},
        )
    return JsonResponse({"units": payload})


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
def public_vacancy_board(request):
    units = Unit.objects.filter(
        listing_enabled=True,
        status=Unit.STATUS_VACANT,
    ).select_related(
        "property__branch",
    ).prefetch_related("charges")
    branch = request.GET.get("branch", "").strip()
    if branch:
        units = units.filter(
            Q(property__branch__name__iexact=branch)
            | Q(property__branch__town__iexact=branch)
        )
    area = request.GET.get("area", "").strip()
    if area:
        units = units.filter(Q(property__location__icontains=area) | Q(property__name__icontains=area))
    unit_type = request.GET.get("type", "").strip()
    if unit_type:
        units = units.filter(unit_type__iexact=unit_type)
    try:
        rent_min = Decimal(request.GET["rent_min"]) if request.GET.get("rent_min") else None
        rent_max = Decimal(request.GET["rent_max"]) if request.GET.get("rent_max") else None
    except InvalidOperation:
        return _problem(400, "Invalid filter", "Rent filters must be valid amounts.", "invalid-filter")
    if any(
        value is not None and (not value.is_finite() or value < 0)
        for value in (rent_min, rent_max)
    ):
        return _problem(400, "Invalid filter", "Rent filters must be finite non-negative amounts.", "invalid-filter")
    if rent_min is not None and rent_max is not None and rent_min > rent_max:
        return _problem(400, "Invalid filter", "Minimum rent cannot exceed maximum rent.", "invalid-filter")

    listings = []
    for unit in units.order_by("property__branch__name", "property__name", "code", "id")[:100]:
        rent = _current_rent(unit)
        if rent is None or (rent_min is not None and rent.amount < rent_min):
            continue
        if rent_max is not None and rent.amount > rent_max:
            continue
        photos = unit.property.photos if isinstance(unit.property.photos, list) else []
        listings.append({
            "code": unit.code,
            "unit_type": unit.unit_type,
            "bedrooms": unit.bedrooms,
            "property_name": unit.property.name,
            "branch": unit.property.branch.name,
            "town": unit.property.branch.town,
            "location": unit.property.location,
            "rent": str(rent.amount),
            "deposit": str(unit.property.security_deposit),
            "photos": [
                photo for photo in photos
                if isinstance(photo, str) and photo.startswith(("https://", "http://"))
            ][:6],
        })
    return JsonResponse({"listings": listings})


@require_GET
def get_unit(request, unit_id):
    error = _property_read_access_error(request)
    if error:
        return error
    unit = Unit.objects.filter(
        pk=unit_id,
        property__in=_properties_visible_to_user(request.user),
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
    landlord_portal_access = (
        _has_role(request.user, LANDLORD_PORTAL_ROLE)
        and Landlord.objects.filter(portal_user=request.user).exists()
    )
    if not (
        _has_landlord_management_access(request.user)
        or _is_caretaker(request.user)
        or landlord_portal_access
    ):
        return _problem(
            403,
            "Permission denied",
            "You do not have access to maintenance tickets.",
            "maintenance-access-denied",
        )
    return None


def _accessible_maintenance_properties(user):
    if _has_landlord_management_access(user) or _is_caretaker(user):
        return _accessible_properties(user)
    if _has_role(user, LANDLORD_PORTAL_ROLE):
        return Property.objects.filter(landlord__portal_user=user)
    return Property.objects.none()


def _accessible_maintenance_tickets(user):
    return MaintenanceTicket.objects.filter(
        property__in=_accessible_maintenance_properties(user)
    ).select_related(
        "property",
        "unit",
        "reported_by",
        "reported_by__profile",
        "assigned_to",
        "assigned_to__profile",
        "vendor",
    ).prefetch_related(
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
        "reported_by_name": (
            getattr(getattr(ticket.reported_by, "profile", None), "name", "")
            or ticket.reported_by.get_full_name()
            or ticket.reported_by.email
        ),
        "assigned_to": ticket.assigned_to_id,
        "assigned_to_name": (
            getattr(getattr(ticket.assigned_to, "profile", None), "name", "")
            or ticket.assigned_to.get_full_name()
            or ticket.assigned_to.email
            if ticket.assigned_to_id
            else None
        ),
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
        "evidence": [
            {
                "name": item.get("name") or f"Evidence {index + 1}",
                "content_type": item.get("content_type"),
                "size_bytes": item.get("size_bytes"),
                "download_path": (
                    f"/api/v1/maintenance-tickets/{ticket.pk}/evidence/{index}"
                ),
            }
            for index, item in enumerate(ticket.photos)
            if isinstance(item, dict)
        ],
        "resolution_notes": ticket.resolution_notes,
        "resolution_fundi_name": ticket.resolution_fundi_name,
        "repair_cost": str(ticket.repair_cost) if ticket.repair_cost is not None else None,
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
            original_ticket_status = ticket.status
            if ticket.status == MaintenanceTicket.STATUS_ASSIGNED:
                ticket.transition_to(MaintenanceTicket.STATUS_QUOTATION)
            if (
                quotation.amount > _maintenance_approval_threshold(ticket)
                and ticket.status == MaintenanceTicket.STATUS_QUOTATION
            ):
                ticket.transition_to(
                    MaintenanceTicket.STATUS_AWAITING_LANDLORD_APPROVAL
                )
            if ticket.status != original_ticket_status:
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
    return _apply_maintenance_quotation_decision(request, quotation)


@require_GET
def list_landlord_maintenance_quotations(request):
    error = _landlord_portal_error(request)
    if error:
        return error

    landlord = Landlord.objects.filter(portal_user=request.user).first()
    if landlord is None:
        return _problem(
            404,
            "Landlord record not found",
            "No landlord record is linked to this account.",
            "landlord-not-found",
        )

    quotations = (
        MaintenanceQuotation.objects.filter(
            ticket__property__landlord=landlord,
            status=MaintenanceQuotation.STATUS_SUBMITTED,
        )
        .select_related("ticket__property", "ticket__unit", "vendor", "submitted_by")
        .order_by("-created_at", "-id")
    )
    pending = []
    for quotation in quotations:
        threshold = _maintenance_approval_threshold(quotation.ticket)
        if quotation.amount <= threshold:
            continue
        pending.append(
            {
                "id": quotation.pk,
                "ticket_id": quotation.ticket_id,
                "ticket_title": quotation.ticket.title,
                "ticket_description": quotation.ticket.description,
                "property_id": quotation.ticket.property_id,
                "property_name": quotation.ticket.property.name,
                "unit_code": (
                    quotation.ticket.unit.code
                    if quotation.ticket.unit_id
                    else None
                ),
                "vendor_name": quotation.vendor.name,
                "vendor_trade": quotation.vendor.trade,
                "description": quotation.description,
                "amount": str(quotation.amount),
                "approval_threshold": str(threshold),
                "valid_until": (
                    quotation.valid_until.isoformat()
                    if quotation.valid_until
                    else None
                ),
                "submitted_at": quotation.created_at.isoformat(),
            }
        )
    return JsonResponse({"quotations": pending})


@require_POST
def decide_landlord_maintenance_quotation(request, quotation_id):
    error = _landlord_portal_error(request)
    if error:
        return error

    landlord = Landlord.objects.filter(portal_user=request.user).first()
    if landlord is None:
        return _problem(
            404,
            "Landlord record not found",
            "No landlord record is linked to this account.",
            "landlord-not-found",
        )

    with transaction.atomic():
        quotation = (
            MaintenanceQuotation.objects.select_for_update(of=("self",))
            .filter(
                pk=quotation_id,
                ticket__property__landlord=landlord,
            )
            .select_related("ticket__property__landlord", "ticket__unit", "vendor")
            .prefetch_related("allocations")
            .first()
        )
        if quotation is None:
            return _problem(
                404,
                "Quotation not found",
                "The quotation is unavailable.",
                "quotation-not-found",
            )
        threshold = _maintenance_approval_threshold(quotation.ticket)
        if quotation.amount <= threshold:
            return _problem(
                403,
                "Permission denied",
                "Only quotations above the agreement threshold require landlord approval.",
                "landlord-quotation-approval-not-required",
            )
        if quotation.status != MaintenanceQuotation.STATUS_SUBMITTED:
            return _problem(
                400,
                "Invalid quotation",
                "Only submitted quotations can be decided.",
                "invalid-quotation",
            )
        return _apply_maintenance_quotation_decision(request, quotation)


def _apply_maintenance_quotation_decision(request, quotation):
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid quotation", "A valid JSON request body is required.", "invalid-quotation")
    body = body or {}
    approved = body.get("approved", True)
    if not isinstance(approved, bool):
        return _problem(400, "Invalid quotation", "approved must be a boolean.", "invalid-quotation")
    rejection_reason = body.get("rejection_reason", "")
    if not approved and (
        not isinstance(rejection_reason, str) or not rejection_reason.strip()
    ):
        return _problem(400, "Invalid quotation", "A rejection_reason is required when rejecting.", "invalid-quotation")
    old_status = quotation.status
    quotation.approved_by = request.user if approved else None
    quotation.approved_at = timezone.now() if approved else None
    quotation.status = MaintenanceQuotation.STATUS_APPROVED if approved else MaintenanceQuotation.STATUS_REJECTED
    quotation.rejection_reason = "" if approved else rejection_reason.strip()
    try:
        quotation.full_clean()
        with transaction.atomic():
            quotation.save()
            if approved and quotation.ticket.status in {
                MaintenanceTicket.STATUS_QUOTATION,
                MaintenanceTicket.STATUS_AWAITING_LANDLORD_APPROVAL,
            }:
                quotation.ticket.transition_to(MaintenanceTicket.STATUS_IN_PROGRESS)
                quotation.ticket.save(update_fields=["status", "updated_at"])
            elif (
                not approved
                and quotation.ticket.status
                == MaintenanceTicket.STATUS_AWAITING_LANDLORD_APPROVAL
            ):
                quotation.ticket.transition_to(MaintenanceTicket.STATUS_QUOTATION)
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
    ticket_rows = []
    for ticket in tickets:
        payload = _maintenance_ticket_payload(ticket)
        payload["can_update"] = (
            _has_landlord_management_access(request.user)
            or (
                _is_caretaker(request.user)
                and ticket.assigned_to_id in (None, request.user.pk)
            )
        )
        ticket_rows.append(payload)
    return JsonResponse({"tickets": ticket_rows})


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
    resolution_fundi_name = None
    repair_cost = None
    if (
        new_status == MaintenanceTicket.STATUS_COMPLETED
        and ticket.status != MaintenanceTicket.STATUS_COMPLETED
    ):
        resolution_fundi_name = body.get("fundi_name")
        try:
            repair_cost = Decimal(str(body["repair_cost"]))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            return _problem(
                400,
                "Invalid resolution",
                "A technician name and non-negative repair_cost are required to complete a ticket.",
                "invalid-resolution",
            )
        if (
            not isinstance(resolution_fundi_name, str)
            or not resolution_fundi_name.strip()
            or not repair_cost.is_finite()
            or repair_cost < 0
        ):
            return _problem(
                400,
                "Invalid resolution",
                "A technician name and non-negative repair_cost are required to complete a ticket.",
                "invalid-resolution",
            )
    caretaker_self_assignment = (
        _is_caretaker(request.user)
        and new_status == MaintenanceTicket.STATUS_ASSIGNED
        and body.get("assigned_to") in (None, "", request.user.pk, str(request.user.pk))
    )
    if (
        not _has_landlord_management_access(request.user)
        and ticket.assigned_to_id != request.user.pk
        and not caretaker_self_assignment
    ):
        return _problem(403, "Permission denied", "Only the assigned caretaker or authorized staff can update this ticket.", "ticket-update-denied")
    if (
        _is_caretaker(request.user)
        and new_status == MaintenanceTicket.STATUS_COMPLETED
        and not any(
            item.get("evidence_type") == "completion"
            for item in ticket.photos
            if isinstance(item, dict)
        )
    ):
        return _problem(
            409,
            "Completion evidence required",
            "Upload at least one completion photo before completing this ticket.",
            "completion-evidence-required",
        )
    old_status = ticket.status
    if resolution_fundi_name is not None:
        ticket.resolution_fundi_name = resolution_fundi_name.strip()
        ticket.repair_cost = repair_cost
    if body.get("assigned_to") is not None:
        try:
            assignee = get_user_model().objects.filter(
                pk=int(body["assigned_to"]),
                is_active=True,
                profile__agency_memberships__agency=ticket.property.branch.agency,
                profile__agency_memberships__agency__is_active=True,
                profile__agency_memberships__roles__name=CARETAKER_ROLE,
                profile__branches=ticket.property.branch,
            ).first()
        except (TypeError, ValueError):
            assignee = None
        if assignee is None:
            return _problem(400, "Invalid assignee", "The assignee must be an active caretaker in the property branch.", "invalid-assignee")
        ticket.assigned_to = assignee
    elif caretaker_self_assignment:
        ticket.assigned_to = request.user
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
    requested_assignee = ticket.assigned_to
    requested_vendor = ticket.vendor
    try:
        with transaction.atomic():
            locked_ticket = (
                _accessible_maintenance_tickets(request.user)
                .select_for_update(of=("self",))
                .filter(pk=ticket.pk)
                .first()
            )
            if locked_ticket is None:
                return _problem(404, "Ticket not found", "The maintenance ticket is unavailable.", "ticket-not-found")
            if locked_ticket.status != old_status:
                return _problem(
                    409,
                    "Ticket changed",
                    "Refresh the ticket before applying another status update.",
                    "ticket-status-conflict",
                )
            ticket = locked_ticket
            ticket.assigned_to = requested_assignee
            ticket.vendor = requested_vendor
            if resolution_fundi_name is not None:
                ticket.resolution_fundi_name = resolution_fundi_name.strip()
                ticket.repair_cost = repair_cost
            ticket.transition_to(new_status)
            ticket.full_clean()
            ticket.save()
            record_audit_event(
                request,
                "maintenance_ticket.status_changed",
                "MaintenanceTicket",
                old_values={"id": ticket.pk, "status": old_status},
                new_values={
                    "id": ticket.pk,
                    "status": ticket.status,
                    "assigned_to": ticket.assigned_to_id,
                    "resolution_fundi_name": ticket.resolution_fundi_name,
                    "repair_cost": (
                        str(ticket.repair_cost) if ticket.repair_cost is not None else None
                    ),
                },
            )
            if (
                new_status == MaintenanceTicket.STATUS_COMPLETED
                and old_status != MaintenanceTicket.STATUS_COMPLETED
            ):
                tenancy = None
                if ticket.unit_id:
                    today = timezone.localdate()
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
                        str(ticket.repair_cost)
                        if ticket.repair_cost is not None
                        else "Not recorded"
                    ),
                }
                notified_staff = _queue_property_staff_sms(
                    ticket.property,
                    (BRANCH_MANAGER_ROLE, SUPER_ADMIN_ROLE, "Property Officer / Agent"),
                    "maintenance_ticket_completed_staff",
                    context,
                    request.user,
                    request,
                )
                if not notified_staff:
                    raise ValueError(
                        "No scoped Property Officer or branch manager is available for SMS notification."
                    )
                if tenancy:
                    if not tenancy.tenant.phone:
                        raise ValueError(
                            f"Tenant {tenancy.tenant_id} has no phone number for ticket completion SMS."
                        )
                    queue_template_sms(
                        tenancy.tenant.phone,
                        "maintenance_ticket_completed_tenant",
                        context,
                        actor=request.user,
                        request=request,
                    )
    except ValidationError as exc:
        return _problem(400, "Invalid status transition", str(exc.message_dict), "invalid-status-transition")
    except (ImproperlyConfigured, ValueError) as exc:
        logger.exception("Could not queue maintenance completion notification.")
        return _problem(
            503,
            "Completion notification unavailable",
            str(exc),
            "maintenance-notification-unavailable",
        )
    except DatabaseError:
        logger.exception("Could not update maintenance ticket status.")
        return _problem(
            500,
            "Ticket update failed",
            "The status update could not be saved. Please try again.",
            "maintenance-ticket-update-failed",
        )
    return JsonResponse({"ticket": _maintenance_ticket_payload(ticket)})


def _maintenance_ledger_tickets(user):
    return _accessible_maintenance_tickets(user).filter(
        status__in=(MaintenanceTicket.STATUS_COMPLETED, MaintenanceTicket.STATUS_CLOSED),
        completed_at__isnull=False,
    )


@require_GET
def list_maintenance_ledger_months(request):
    error = _maintenance_access_error(request)
    if error:
        return error
    months = sorted(
        {
            timezone.localtime(completed_at).strftime("%Y-%m")
            for completed_at in _maintenance_ledger_tickets(request.user).values_list(
                "completed_at", flat=True
            )
        },
        reverse=True,
    )
    if not months:
        months = [timezone.localdate().strftime("%Y-%m")]
    return JsonResponse({"months": months})


@require_GET
def list_maintenance_ledger(request):
    error = _maintenance_access_error(request)
    if error:
        return error
    month = request.GET.get("month") or timezone.localdate().strftime("%Y-%m")
    try:
        selected_month = datetime.strptime(month, "%Y-%m").date()
    except (TypeError, ValueError):
        return _problem(
            400,
            "Invalid month",
            "month must use the YYYY-MM format.",
            "invalid-month",
        )
    if selected_month.strftime("%Y-%m") != month:
        return _problem(
            400,
            "Invalid month",
            "month must use the YYYY-MM format.",
            "invalid-month",
        )
    next_month = (
        date(selected_month.year + 1, 1, 1)
        if selected_month.month == 12
        else date(selected_month.year, selected_month.month + 1, 1)
    )
    start = timezone.make_aware(datetime.combine(selected_month, datetime.min.time()))
    end = timezone.make_aware(datetime.combine(next_month, datetime.min.time()))
    tickets = (
        _maintenance_ledger_tickets(request.user)
        .filter(completed_at__gte=start, completed_at__lt=end)
        .select_related("property", "unit", "reported_by", "reported_by__profile")
        .order_by("-completed_at", "-pk")
    )
    ledger = []
    total_spend = Decimal("0.00")
    for ticket in tickets:
        cost = ticket.repair_cost
        if cost is not None:
            total_spend += cost
        ledger.append(
            {
                "id": ticket.pk,
                "ticket_id": f"TCK-{ticket.pk:03d}",
                "unit_code": ticket.unit.code if ticket.unit else "-",
                "tenant_name": (
                    getattr(getattr(ticket.reported_by, "profile", None), "name", "")
                    or ticket.reported_by.get_full_name()
                    or ticket.reported_by.email
                ),
                "category": ticket.category,
                "description": ticket.description,
                "fundi_name": ticket.resolution_fundi_name or "N/A",
                "cost": str(cost) if cost is not None else None,
                "resolved_at": ticket.completed_at.isoformat(),
            }
        )
    return JsonResponse(
        {
            "month": month,
            "summary": {"total_spend": str(total_spend), "total_repairs": len(ledger)},
            "ledger": ledger,
        }
    )


@require_http_methods(["GET", "POST"])
def add_maintenance_ticket_comment(request, ticket_id):
    error = _maintenance_access_error(request)
    if error:
        return error
    ticket = _accessible_maintenance_tickets(request.user).filter(pk=ticket_id).first()
    if ticket is None:
        return _problem(404, "Ticket not found", "The maintenance ticket is unavailable.", "ticket-not-found")
    if request.method == "GET":
        comments = ticket.comments.select_related("author", "author__profile")
        return JsonResponse(
            {
                "comments": [
                    {
                        "id": comment.pk,
                        "sender_role": (
                            "caretaker"
                            if _is_caretaker(comment.author)
                            else "tenant"
                            if _has_role(comment.author, TENANT_PORTAL_ROLE)
                            else "landlord"
                        ),
                        "sender_name": (
                            getattr(getattr(comment.author, "profile", None), "name", "")
                            or comment.author.get_full_name()
                            or comment.author.email
                        ),
                        "message": comment.body,
                        "created_at": comment.created_at.isoformat(),
                    }
                    for comment in comments
                ]
            }
        )
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
                "sender_role": (
                    "caretaker" if _is_caretaker(comment.author) else "landlord"
                ),
                "sender_name": (
                    getattr(getattr(comment.author, "profile", None), "name", "")
                    or comment.author.get_full_name()
                    or comment.author.email
                ),
                "message": comment.body,
                "body": comment.body,
                "photos": comment.photos,
                "created_at": comment.created_at.isoformat(),
            }
        },
        status=201,
    )


@require_POST
def upload_maintenance_completion_photos(request, ticket_id):
    error = _maintenance_access_error(request)
    if error:
        return error
    ticket = _accessible_maintenance_tickets(request.user).filter(pk=ticket_id).first()
    if ticket is None:
        return _problem(404, "Ticket not found", "The maintenance ticket is unavailable.", "ticket-not-found")
    if not _is_caretaker(request.user) or ticket.assigned_to_id != request.user.pk:
        return _problem(
            403,
            "Permission denied",
            "Only the caretaker assigned to this ticket can upload completion photos.",
            "ticket-evidence-denied",
        )
    if ticket.status != MaintenanceTicket.STATUS_IN_PROGRESS:
        return _problem(
            409,
            "Ticket unavailable",
            "Completion photos can only be uploaded while work is in progress.",
            "ticket-evidence-conflict",
        )
    files = request.FILES.getlist("photos")
    if not files:
        return _problem(400, "Invalid evidence", "At least one completion photo is required.", "invalid-evidence")
    try:
        prepared = _prepare_tenant_evidence(files)
    except ValueError as exc:
        return _problem(400, "Invalid evidence", str(exc), "invalid-evidence")
    if len(ticket.photos) + len(prepared) > 10:
        return _problem(400, "Invalid evidence", "A ticket can include at most 10 photos.", "invalid-evidence")

    uploaded_keys = []
    added_photos = []
    try:
        for item in prepared:
            key = f"maintenance-tickets/completion/{ticket.pk}/{uuid.uuid4().hex}.{item['extension']}"
            uploaded_keys.append(key)
            upload_private_object(key, item["file"], item["content_type"])
            added_photos.append(
                {
                    "storage_key": key,
                    "content_type": item["content_type"],
                    "size_bytes": item["size_bytes"],
                    "name": f"Completion photo {len(ticket.photos) + len(added_photos) + 1}.{item['extension']}",
                    "evidence_type": "completion",
                }
            )
        with transaction.atomic():
            locked = (
                MaintenanceTicket.objects.select_for_update(of=("self",))
                .filter(
                    pk=ticket.pk,
                    assigned_to=request.user,
                    status=MaintenanceTicket.STATUS_IN_PROGRESS,
                    property__in=_accessible_properties(request.user),
                )
                .first()
            )
            if locked is None:
                _cleanup_tenant_evidence(uploaded_keys)
                return _problem(
                    409,
                    "Ticket changed",
                    "The ticket is no longer assigned to you or in progress.",
                    "ticket-evidence-conflict",
                )
            if len(locked.photos) + len(added_photos) > 10:
                _cleanup_tenant_evidence(uploaded_keys)
                return _problem(409, "Evidence limit reached", "A ticket can include at most 10 photos.", "invalid-evidence")
            locked.photos = [*locked.photos, *added_photos]
            locked.full_clean()
            locked.save(update_fields=["photos", "updated_at"])
            record_audit_event(
                request,
                "maintenance_ticket.completion_evidence_added",
                "MaintenanceTicket",
                new_values={
                    "ticket_id": locked.pk,
                    "photo_count": len(added_photos),
                    "evidence": [
                        {
                            "content_type": item["content_type"],
                            "size_bytes": item["size_bytes"],
                        }
                        for item in added_photos
                    ],
                },
            )
    except (BotoCoreError, ClientError, ImproperlyConfigured, OSError):
        _cleanup_tenant_evidence(uploaded_keys)
        logger.exception("Maintenance completion photo upload failed.")
        return _problem(
            503,
            "Evidence storage unavailable",
            "The completion photos could not be stored. Please try again.",
            "maintenance-evidence-unavailable",
        )
    except ValidationError as exc:
        _cleanup_tenant_evidence(uploaded_keys)
        return _problem(400, "Invalid evidence", str(exc.message_dict), "invalid-evidence")
    except DatabaseError:
        _cleanup_tenant_evidence(uploaded_keys)
        logger.exception("Could not save maintenance completion photo metadata.")
        return _problem(
            503,
            "Evidence could not be saved",
            "The completion photos could not be attached to this ticket. Please try again.",
            "maintenance-evidence-unavailable",
        )
    return JsonResponse(
        {"ticket_id": ticket.pk, "uploaded": len(added_photos)},
        status=201,
    )


@require_GET
def get_maintenance_ticket_evidence(request, ticket_id, evidence_index):
    error = _maintenance_access_error(request)
    if error:
        return error
    ticket = _accessible_maintenance_tickets(request.user).filter(pk=ticket_id).first()
    if ticket is None or evidence_index >= len(ticket.photos):
        return _problem(404, "Evidence not found", "The evidence is unavailable.", "evidence-not-found")
    evidence = ticket.photos[evidence_index]
    storage_key = evidence.get("storage_key") if isinstance(evidence, dict) else None
    if (
        not isinstance(storage_key, str)
        or not storage_key.startswith("maintenance/tenant/")
        or evidence.get("content_type") not in {"image/jpeg", "image/png", "image/webp"}
    ):
        return _problem(404, "Evidence not found", "The evidence is unavailable.", "evidence-not-found")
    try:
        content = read_private_object(storage_key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return _problem(
                404,
                "Evidence not found",
                "The evidence is unavailable.",
                "evidence-not-found",
            )
        logger.exception("Could not retrieve maintenance evidence.")
        return _problem(
            503,
            "Evidence storage unavailable",
            "Evidence could not be loaded. Please try again later.",
            "evidence-storage-unavailable",
        )
    except (BotoCoreError, ImproperlyConfigured, OSError):
        logger.exception("Could not retrieve maintenance evidence.")
        return _problem(
            503,
            "Evidence storage unavailable",
            "Evidence could not be loaded. Please try again later.",
            "evidence-storage-unavailable",
        )
    record_audit_event(
        request,
        "maintenance_ticket.evidence_viewed",
        "MaintenanceTicket",
        new_values={"ticket_id": ticket.pk, "evidence_index": evidence_index},
    )
    response = HttpResponse(content, content_type=evidence["content_type"])
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = "inline"
    return response


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
            invoice_type=Invoice.TYPE_STANDARD,
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


@require_GET
def property_officer_migration_ledger(request):
    error = _property_access_error(request)
    if error:
        return error
    if not _has_landlord_management_access(request.user):
        return _problem(
            403,
            "Permission denied",
            "Property officer or management access is required.",
            "migration-ledger-access-denied",
        )

    properties = _accessible_properties(request.user)
    branch_ids = request.GET.getlist("branch_id")
    if branch_ids:
        try:
            requested_branch_ids = {int(branch_id) for branch_id in branch_ids}
        except ValueError:
            return _problem(400, "Invalid request", "branch_id must be an integer.", "invalid-request")
        accessible_branch_ids = set(
            properties.values_list("branch_id", flat=True).distinct()
        )
        if not requested_branch_ids.issubset(accessible_branch_ids):
            return _problem(404, "Branch not found", "The branch is unavailable.", "branch-not-found")
        properties = properties.filter(branch_id__in=requested_branch_ids)

    property_id = request.GET.get("property_id")
    if property_id:
        try:
            property_id = int(property_id)
        except ValueError:
            return _problem(400, "Invalid request", "property_id must be an integer.", "invalid-request")
        properties = properties.filter(pk=property_id)
        if not properties.exists():
            return _problem(404, "Property not found", "The property is unavailable.", "property-not-found")

    status_filter = request.GET.get("status", "all")
    if status_filter not in {"all", "unpaid", "partial", "cleared"}:
        return _problem(
            400,
            "Invalid request",
            "status must be all, unpaid, partial, or cleared.",
            "invalid-request",
        )

    invoices = list(
        Invoice.objects.filter(
            tenancy__unit__property__in=properties,
            status=Invoice.STATUS_ISSUED,
            lines__charge_type="arrears",
            lines__metadata__migration_opening_balance=True,
        )
        .select_related(
            "tenancy__tenant",
            "tenancy__unit__property__branch",
        )
        .distinct()
        .order_by("tenancy__unit__property__name", "tenancy__unit__code", "id")
    )
    invoice_ids = [invoice.pk for invoice in invoices]
    as_of = timezone.localdate()
    allocated_by_invoice = {
        row["invoice_id"]: row["total"] or Decimal("0.00")
        for row in (
            PaymentAllocation.objects.filter(
                invoice_id__in=invoice_ids,
                created_at__date__lte=as_of,
                payment__received_at__date__lte=as_of,
            )
            .exclude(
                payment__instrument_status__in=(
                    Payment.INSTRUMENT_PENDING,
                    Payment.INSTRUMENT_BOUNCED,
                )
            )
            .values("invoice_id")
            .annotate(total=Sum("amount"))
        )
    }
    waived_by_invoice = {
        row["invoice_id"]: row["total"] or Decimal("0.00")
        for row in (
            ArrearsWaiver.objects.filter(
                invoice_id__in=invoice_ids,
                approved_at__date__lte=as_of,
            )
            .values("invoice_id")
            .annotate(total=Sum("amount"))
        )
    }
    rows = []
    for invoice in invoices:
        paid = allocated_by_invoice.get(invoice.pk, Decimal("0.00"))
        waived = waived_by_invoice.get(invoice.pk, Decimal("0.00"))
        outstanding = max(invoice.total - paid - waived, Decimal("0.00"))
        if outstanding == 0:
            status = "cleared"
        elif paid or waived:
            status = "partial"
        else:
            status = "unpaid"
        if status_filter != "all" and status != status_filter:
            continue
        property_obj = invoice.tenancy.unit.property
        rows.append(
            {
                "invoice_id": invoice.pk,
                "invoice_number": invoice.number,
                "property_id": property_obj.pk,
                "property_name": property_obj.name,
                "branch_id": property_obj.branch_id,
                "branch_name": property_obj.branch.name,
                "unit_id": invoice.tenancy.unit_id,
                "unit_code": invoice.tenancy.unit.code,
                "tenancy_id": invoice.tenancy_id,
                "tenant_name": invoice.tenancy.tenant.name,
                "arrears_as_of": invoice.period_start.isoformat(),
                "opening_arrears": str(invoice.total),
                "paid_to_arrears": str(paid),
                "waived_amount": str(waived),
                "outstanding": str(outstanding),
                "status": status,
            }
        )

    zero = Decimal("0.00")
    summary = {
        "records": len(rows),
        "opening_arrears": str(sum((Decimal(row["opening_arrears"]) for row in rows), zero)),
        "paid_to_arrears": str(sum((Decimal(row["paid_to_arrears"]) for row in rows), zero)),
        "waived_amount": str(sum((Decimal(row["waived_amount"]) for row in rows), zero)),
        "outstanding": str(sum((Decimal(row["outstanding"]) for row in rows), zero)),
        "cleared": sum(row["status"] == "cleared" for row in rows),
        "partial": sum(row["status"] == "partial" for row in rows),
        "unpaid": sum(row["status"] == "unpaid" for row in rows),
    }
    return JsonResponse({"as_of": as_of.isoformat(), "summary": summary, "ledger": rows})


def _property_officer_monthly_ledger_properties(request):
    error = _property_access_error(request)
    if error:
        return None, error
    if not _has_landlord_management_access(request.user):
        return None, _problem(
            403,
            "Permission denied",
            "Property officer or management access is required.",
            "monthly-ledger-access-denied",
        )

    properties = _accessible_properties(request.user)
    branch_ids = request.GET.getlist("branch_id")
    if branch_ids:
        try:
            requested_branch_ids = {int(branch_id) for branch_id in branch_ids}
        except ValueError:
            return None, _problem(400, "Invalid request", "branch_id must be an integer.", "invalid-request")
        accessible_branch_ids = set(
            request.user.profile.accessible_branches(
                role_names=LANDLORD_MANAGEMENT_ROLE_NAMES
            ).values_list("pk", flat=True)
        )
        if not requested_branch_ids.issubset(accessible_branch_ids):
            return None, _problem(404, "Branch not found", "The branch is unavailable.", "branch-not-found")
        properties = properties.filter(branch_id__in=requested_branch_ids)

    property_id = request.GET.get("property_id")
    if property_id:
        try:
            property_id = int(property_id)
        except ValueError:
            return None, _problem(400, "Invalid request", "property_id must be an integer.", "invalid-request")
        properties = properties.filter(pk=property_id)
        if not properties.exists():
            return None, _problem(404, "Property not found", "The property is unavailable.", "property-not-found")
    return properties, None


def _monthly_ledger_invoices(properties):
    return (
        Invoice.objects.filter(
            tenancy__unit__property__in=properties,
            status=Invoice.STATUS_ISSUED,
            lines__charge_type__in=(UnitCharge.CHARGE_RENT, UnitCharge.CHARGE_RECURRING, "utility"),
        )
        .exclude(lines__metadata__migration_opening_balance=True)
        .distinct()
    )


def _monthly_ledger_allocation_totals(invoice_ids, *, start_date=None, end_date):
    allocations = PaymentAllocation.objects.filter(
        invoice_id__in=invoice_ids,
        created_at__date__lte=end_date,
        payment__received_at__date__lte=end_date,
    ).exclude(
        payment__instrument_status__in=(
            Payment.INSTRUMENT_PENDING,
            Payment.INSTRUMENT_BOUNCED,
        )
    )
    if start_date is not None:
        allocations = allocations.filter(payment__received_at__date__gte=start_date)
    return allocations.values("invoice__tenancy_id").annotate(total=Sum("amount"))


@require_GET
def property_officer_monthly_ledger_months(request):
    properties, error = _property_officer_monthly_ledger_properties(request)
    if error:
        return error
    months = sorted(
        {
            period_start.strftime("%Y-%m")
            for period_start in _monthly_ledger_invoices(properties).values_list(
                "period_start", flat=True
            )
        },
        reverse=True,
    )
    if not months:
        months = [timezone.localdate().strftime("%Y-%m")]
    return JsonResponse({"months": months})


@require_GET
def property_officer_monthly_ledger(request):
    properties, error = _property_officer_monthly_ledger_properties(request)
    if error:
        return error

    month = request.GET.get("month") or timezone.localdate().strftime("%Y-%m")
    try:
        selected_month = datetime.strptime(month, "%Y-%m").date()
    except (TypeError, ValueError):
        return _problem(400, "Invalid month", "month must use the YYYY-MM format.", "invalid-month")
    if selected_month.strftime("%Y-%m") != month:
        return _problem(400, "Invalid month", "month must use the YYYY-MM format.", "invalid-month")
    month_start = selected_month.replace(day=1)
    next_month = (
        date(selected_month.year + 1, 1, 1)
        if selected_month.month == 12
        else date(selected_month.year, selected_month.month + 1, 1)
    )
    month_end = next_month - timedelta(days=1)
    as_of = min(month_end, timezone.localdate())
    prior_as_of = month_start - timedelta(days=1)

    status_filter = request.GET.get("status", "all")
    if status_filter not in {"all", "unpaid", "partial", "cleared"}:
        return _problem(
            400,
            "Invalid status",
            "status must be all, unpaid, partial, or cleared.",
            "invalid-ledger-status",
        )

    selected_invoices = list(
        _monthly_ledger_invoices(properties)
        .filter(period_start__gte=month_start, period_start__lt=next_month)
        .select_related("tenancy__tenant", "tenancy__unit__property__branch")
        .prefetch_related("lines")
        .order_by("tenancy__unit__property__name", "tenancy__unit__code", "period_start", "id")
    )
    tenancy_ids = {invoice.tenancy_id for invoice in selected_invoices}
    previous_invoices = list(
        Invoice.objects.filter(
            tenancy_id__in=tenancy_ids,
            status=Invoice.STATUS_ISSUED,
            period_end__lt=month_start,
        )
        .prefetch_related("lines")
        .order_by("tenancy_id", "period_end", "id")
    )
    previous_invoice_ids = [invoice.pk for invoice in previous_invoices]
    selected_invoice_ids = [invoice.pk for invoice in selected_invoices]
    all_invoice_ids = previous_invoice_ids + selected_invoice_ids

    prior_allocated_by_tenancy = {
        row["invoice__tenancy_id"]: row["total"] or Decimal("0.00")
        for row in _monthly_ledger_allocation_totals(
            previous_invoice_ids,
            end_date=prior_as_of,
        )
    }
    all_previous_charge_totals = {}
    for invoice in previous_invoices:
        all_previous_charge_totals[invoice.tenancy_id] = (
            all_previous_charge_totals.get(invoice.tenancy_id, Decimal("0.00"))
            + sum(
                (line.amount for line in invoice.lines.all() if line.charge_type != "balance"),
                Decimal("0.00"),
            )
        )
    prior_waiver_totals = {
        row["invoice__tenancy_id"]: row["total"] or Decimal("0.00")
        for row in ArrearsWaiver.objects.filter(
            invoice_id__in=previous_invoice_ids,
            approved_at__date__lte=prior_as_of,
        )
        .values("invoice__tenancy_id")
        .annotate(total=Sum("amount"))
    }
    current_period_payment_totals = {
        row["invoice__tenancy_id"]: row["total"] or Decimal("0.00")
        for row in _monthly_ledger_allocation_totals(
            all_invoice_ids,
            start_date=month_start,
            end_date=as_of,
        )
    }
    current_period_waiver_totals = {
        row["invoice__tenancy_id"]: row["total"] or Decimal("0.00")
        for row in ArrearsWaiver.objects.filter(
            invoice_id__in=all_invoice_ids,
            approved_at__date__gte=month_start,
            approved_at__date__lte=as_of,
        )
        .values("invoice__tenancy_id")
        .annotate(total=Sum("amount"))
    }
    waiver_notes_by_tenancy = {}
    for waiver in (
        ArrearsWaiver.objects.filter(
            invoice_id__in=all_invoice_ids,
            approved_at__date__gte=month_start,
            approved_at__date__lte=as_of,
        )
        .select_related("invoice")
        .order_by("approved_at", "id")
    ):
        waiver_notes_by_tenancy.setdefault(waiver.tenancy_id, []).append(
            {
                "invoice_number": waiver.invoice.number,
                "amount": str(waiver.amount),
                "reason": waiver.reason,
            }
        )
    payment_rows = {}
    for allocation in (
        PaymentAllocation.objects.filter(
            invoice_id__in=all_invoice_ids,
            created_at__date__lte=as_of,
            payment__received_at__date__gte=month_start,
            payment__received_at__date__lte=as_of,
        )
        .exclude(
            payment__instrument_status__in=(
                Payment.INSTRUMENT_PENDING,
                Payment.INSTRUMENT_BOUNCED,
            )
        )
        .select_related("payment", "invoice")
        .order_by("payment__received_at", "payment_id", "id")
    ):
        tenancy_payments = payment_rows.setdefault(allocation.invoice.tenancy_id, {})
        payment = allocation.payment
        entry = tenancy_payments.setdefault(
            payment.pk,
            {
                "id": payment.pk,
                "received_at": payment.received_at.isoformat(),
                "reference": payment.reference,
                "source": payment.source,
                "amount": Decimal("0.00"),
            },
        )
        entry["amount"] += allocation.amount

    invoices_by_tenancy = {}
    for invoice in selected_invoices:
        invoices_by_tenancy.setdefault(invoice.tenancy_id, []).append(invoice)

    rows = []
    for tenancy_id, tenancy_invoices in invoices_by_tenancy.items():
        invoices_by_tenancy_lines = [
            line
            for invoice in tenancy_invoices
            for line in invoice.lines.all()
            if line.charge_type != "balance"
        ]
        new_charges = sum((line.amount for line in invoices_by_tenancy_lines), Decimal("0.00"))
        prior_balance = max(
            all_previous_charge_totals.get(tenancy_id, Decimal("0.00"))
            - prior_allocated_by_tenancy.get(tenancy_id, Decimal("0.00"))
            - prior_waiver_totals.get(tenancy_id, Decimal("0.00")),
            Decimal("0.00"),
        )
        paid = current_period_payment_totals.get(tenancy_id, Decimal("0.00"))
        waived = current_period_waiver_totals.get(tenancy_id, Decimal("0.00"))
        expected = prior_balance + new_charges
        remaining = max(expected - paid - waived, Decimal("0.00"))
        if remaining == 0:
            status = "cleared"
        elif paid or waived:
            status = "partial"
        else:
            status = "unpaid"
        if status_filter != "all" and status != status_filter:
            continue

        tenancy = tenancy_invoices[0].tenancy
        property_obj = tenancy.unit.property
        tenant_payments = sorted(
            payment_rows.get(tenancy_id, {}).values(),
            key=lambda item: (item["received_at"], item["id"]),
        )
        rows.append(
            {
                "tenancy_id": tenancy_id,
                "property_id": property_obj.pk,
                "property_name": property_obj.name,
                "branch_id": property_obj.branch_id,
                "branch_name": property_obj.branch.name,
                "unit_id": tenancy.unit_id,
                "unit_code": tenancy.unit.code,
                "tenant_name": tenancy.tenant.name,
                "invoice_numbers": [invoice.number for invoice in tenancy_invoices],
                "new_charges": str(new_charges),
                "prior_balance": str(prior_balance),
                "expected": str(expected),
                "paid": str(paid),
                "waived": str(waived),
                "waiver_notes": waiver_notes_by_tenancy.get(tenancy_id, []),
                "remaining": str(remaining),
                "status": status,
                "bill_lines": [
                    {
                        "invoice_number": invoice.number,
                        "charge_type": line.charge_type,
                        "description": line.description,
                        "amount": str(line.amount),
                    }
                    for invoice in tenancy_invoices
                    for line in invoice.lines.all()
                    if line.charge_type != "balance"
                ],
                "payments": [
                    {
                        **payment,
                        "amount": str(payment["amount"]),
                    }
                    for payment in tenant_payments
                ],
            }
        )

    zero = Decimal("0.00")
    summary = {
        "records": len(rows),
        "new_charges": str(sum((Decimal(row["new_charges"]) for row in rows), zero)),
        "prior_balance": str(sum((Decimal(row["prior_balance"]) for row in rows), zero)),
        "expected": str(sum((Decimal(row["expected"]) for row in rows), zero)),
        "paid": str(sum((Decimal(row["paid"]) for row in rows), zero)),
        "waived": str(sum((Decimal(row["waived"]) for row in rows), zero)),
        "remaining": str(sum((Decimal(row["remaining"]) for row in rows), zero)),
        "cleared": sum(row["status"] == "cleared" for row in rows),
        "partial": sum(row["status"] == "partial" for row in rows),
        "unpaid": sum(row["status"] == "unpaid" for row in rows),
    }
    return JsonResponse({"month": month, "as_of": as_of.isoformat(), "summary": summary, "ledger": rows})


def _waiver_manager_error(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    if not _has_manager_access(request.user):
        return _problem(403, "Permission denied", "Only a manager can approve arrears waivers.", "waiver-access-denied")
    return None


@require_POST
def create_arrears_waiver(request, invoice_id):
    error = _waiver_manager_error(request)
    if error:
        return error
    invoice = Invoice.objects.filter(
        pk=invoice_id,
        tenancy__unit__property__in=_accessible_manager_properties(request.user),
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
        tenancy__unit__property__in=_accessible_manager_properties(request.user),
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
        unit__property__in=_accessible_manager_properties(request.user),
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
        plan__tenancy__unit__property__in=_accessible_manager_properties(request.user),
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


def _create_move_in_package_for_tenancy(tenancy):
    existing = MoveInPackage.objects.filter(tenancy=tenancy).prefetch_related(
        "charges", "checklist_items"
    ).first()
    if existing:
        return existing, False

    settlement = MoveInSettlement.objects.filter(
        unit=tenancy.unit,
        tenant=tenancy.tenant,
        expected_move_in_date=tenancy.start_date,
        status__in=(MoveInSettlement.STATUS_PAID, MoveInSettlement.STATUS_ACTIVE),
    ).first()
    included_charge_types = [UnitCharge.CHARGE_RENT]
    if settlement is None:
        included_charge_types.append(UnitCharge.CHARGE_DEPOSIT)
    month_days = calendar.monthrange(
        tenancy.start_date.year,
        tenancy.start_date.month,
    )[1]
    billable_days = month_days - tenancy.start_date.day + 1
    effective_charges = UnitCharge.objects.filter(
        unit=tenancy.unit,
        effective_from__lte=tenancy.start_date,
        charge_type__in=included_charge_types,
    ).filter(
        models.Q(effective_to__isnull=True)
        | models.Q(effective_to__gte=tenancy.start_date)
    ).order_by("charge_type", "name", "-effective_from")
    seen_charges = set()
    charges = []
    for charge in effective_charges:
        charge_key = (
            charge.charge_type,
            charge.name.casefold()
            if charge.charge_type == UnitCharge.CHARGE_DEPOSIT
            else charge.charge_type,
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
            amount = (
                charge.amount / Decimal(month_days) * Decimal(billable_days)
            ).quantize(Decimal("0.01"))
            calculation.update(
                {
                    "method": "daily_proration",
                    "monthly_amount": str(charge.amount),
                    "month_days": month_days,
                    "billable_days": billable_days,
                }
            )
        else:
            calculation["method"] = "full_effective_charge"
        charges.append((charge.charge_type, charge.name, amount, calculation))

    if settlement is not None:
        if settlement.deposit_amount > 0:
            charges.append((
                MoveInCharge.CHARGE_DEPOSIT,
                "Security deposit",
                settlement.deposit_amount,
                {"method": "full_deposit", "settlement_id": settlement.pk},
            ))
        utility_deposits = settlement.utility_deposits
        if isinstance(utility_deposits, list) and utility_deposits:
            for deposit in utility_deposits:
                amount = Decimal(str(deposit["amount"])).quantize(Decimal("0.01"))
                if amount > 0:
                    charges.append((
                        MoveInCharge.CHARGE_DEPOSIT,
                        str(deposit["name"]),
                        amount,
                        {"method": "full_deposit", "settlement_id": settlement.pk},
                    ))
        else:
            for name, amount in (
                ("Water deposit", settlement.water_deposit_amount),
                ("Utility deposits", settlement.utility_deposit_amount),
            ):
                if amount > 0:
                    charges.append((
                        MoveInCharge.CHARGE_DEPOSIT,
                        name,
                        amount,
                        {"method": "full_deposit", "settlement_id": settlement.pk},
                    ))
    handover_inventory = [
        {
            "name": item["name"],
            "property_quantity": item["quantity"],
            "issued_quantity": None,
        }
        for item in (tenancy.unit.property.default_handover_items or [])
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("quantity"), int)
    ]
    package = MoveInPackage(
        tenancy=tenancy,
        agreement_reference=f"MIA-{timezone.now():%Y%m%d}-{secrets.token_hex(4).upper()}",
        handover_inventory=handover_inventory,
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
    return package, True


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
    package, created = _create_move_in_package_for_tenancy(tenancy)
    if not created:
        return JsonResponse({"move_in_package": _move_in_package_payload(package)})
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


def _create_move_in_invoice_for_tenancy(tenancy, package):
    period_start = tenancy.start_date
    period_end = date(
        period_start.year,
        period_start.month,
        calendar.monthrange(period_start.year, period_start.month)[1],
    )
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
        return existing, False
    if Invoice.objects.filter(
        tenancy=tenancy,
        period_start__lte=period_end,
        period_end__gte=period_start,
        invoice_type=Invoice.TYPE_STANDARD,
    ).exists():
        raise ValueError("An invoice already covers this tenancy's move-in period.")
    if package is None:
        raise ValueError("Generate the move-in package before issuing its invoice.")
    charges = list(package.charges.all())
    if not charges:
        raise ValueError("The move-in package has no billable charges.")
    total = sum((charge.amount for charge in charges), Decimal("0.00"))
    sequence, _ = InvoiceNumberSequence.objects.select_for_update().get_or_create(
        branch=tenancy.unit.property.branch,
        year=period_start.year,
    )
    sequence_number = sequence.next_number
    sequence.next_number += 1
    sequence.save(update_fields=["next_number"])
    invoice = Invoice(
        branch=tenancy.unit.property.branch,
        tenancy=tenancy,
        number=f"INV-{tenancy.unit.property.branch_id}-{period_start.year}-{sequence_number:06d}",
        period_start=period_start,
        period_end=period_end,
        due_date=period_start,
        total=total,
    )
    invoice.full_clean()
    invoice.save()
    InvoiceLine.objects.bulk_create(
        [
            InvoiceLine(
                invoice=invoice,
                charge_type=charge.charge_type,
                description=charge.description,
                amount=charge.amount,
                metadata={
                    "move_in_charge_id": charge.pk,
                    "agreement_reference": package.agreement_reference,
                },
            )
            for charge in charges
        ]
    )
    return invoice, True


def _persist_move_in_documents(
    tenancy,
    settlement,
    package,
    invoice,
    payment,
    receipt,
    actor,
    uploaded_storage_keys,
    identity_document,
):
    documents = []
    document_specs = (
        (
            "move_in_agreement",
            f"{package.agreement_reference}.pdf",
            _agreement_pdf(tenancy, package),
        ),
        (
            "payment_receipt",
            f"{receipt.number}.pdf",
            _payment_receipt_pdf(tenancy, invoice, payment, receipt),
        ),
    )
    for document_type, name, content in document_specs:
        existing = TenantDocument.objects.filter(
            tenant=tenancy.tenant,
            document_type=document_type,
            name=name,
        ).first()
        if existing:
            documents.append(existing)
            continue
        storage_key = (
            f"tenant-documents/{tenancy.tenant_id}/move-in/{uuid.uuid4().hex}.pdf"
        )
        uploaded_storage_keys.append(storage_key)
        upload_private_object(
            storage_key,
            BytesIO(content),
            "application/pdf",
        )
        documents.append(
            TenantDocument.objects.create(
                tenant=tenancy.tenant,
                document_type=document_type,
                name=name,
                storage_key=storage_key,
                metadata={
                    "content_type": "application/pdf",
                    "tenancy_id": tenancy.pk,
                    "move_in_settlement_id": settlement.pk,
                    "invoice_id": invoice.pk,
                    "payment_id": payment.pk,
                    "receipt_id": receipt.pk,
                },
                uploaded_by=actor,
            )
        )
    if identity_document is not None:
        upload, content_type, extension = identity_document
        name = upload.name[:255]
        existing = TenantDocument.objects.filter(
            tenant=tenancy.tenant,
            document_type="identity_document",
            name=name,
        ).first()
        if existing:
            documents.append(existing)
        else:
            storage_key = (
                f"tenant-documents/{tenancy.tenant_id}/move-in/"
                f"{uuid.uuid4().hex}.{extension}"
            )
            uploaded_storage_keys.append(storage_key)
            upload.seek(0)
            upload_private_object(storage_key, upload, content_type)
            documents.append(
                TenantDocument.objects.create(
                    tenant=tenancy.tenant,
                    document_type="identity_document",
                    name=name,
                    storage_key=storage_key,
                    metadata={
                        "content_type": content_type,
                        "size_bytes": upload.size,
                        "tenancy_id": tenancy.pk,
                        "move_in_settlement_id": settlement.pk,
                    },
                    uploaded_by=actor,
                )
            )
    return documents


def _materialize_move_in_payment(
    tenancy,
    settlement,
    actor,
    uploaded_storage_keys,
    identity_document,
    assigned_caretaker=None,
):
    move_in_payment = (
        MoveInPayment.objects.select_for_update()
        .filter(
            settlement=settlement,
            method=MoveInPayment.METHOD_OFFICER_CONFIRMATION,
            status=MoveInPayment.STATUS_SUCCESS,
        )
        .exclude(transaction_reference="")
        .order_by("id")
        .first()
    )
    if move_in_payment is None:
        return None

    package, package_created = _create_move_in_package_for_tenancy(tenancy)
    if (
        assigned_caretaker
        and package.assigned_caretaker_id
        not in {None, assigned_caretaker.pk}
    ):
        raise ValidationError(
            "The move-in inspection is already assigned to a different caretaker."
        )
    if assigned_caretaker and package.assigned_caretaker_id is None:
        package.assigned_caretaker = assigned_caretaker
        package.save(update_fields=["assigned_caretaker", "updated_at"])
    invoice, invoice_created = _create_move_in_invoice_for_tenancy(
        tenancy,
        package,
    )
    if invoice.total != settlement.total_required:
        raise ValidationError(
            "The move-in invoice does not match the confirmed settlement total."
        )
    if move_in_payment.amount != invoice.total:
        raise ValidationError(
            "The recorded move-in payment does not match the opening invoice."
        )

    payment_sources = {
        MoveInPayment.CHANNEL_MPESA: Payment.SOURCE_MPESA,
        MoveInPayment.CHANNEL_BANK_TRANSFER: Payment.SOURCE_BANK,
        MoveInPayment.CHANNEL_CASH: Payment.SOURCE_CASH,
    }
    source = payment_sources.get(move_in_payment.payment_channel)
    if source is None:
        raise ValidationError("The confirmed move-in payment channel is invalid.")

    payment_date = move_in_payment.payment_date or timezone.localdate()
    received_at = datetime.combine(payment_date, datetime.min.time())
    if settings.USE_TZ:
        received_at = timezone.make_aware(received_at)
    payment = (
        Payment.objects.select_for_update()
        .filter(reference=move_in_payment.transaction_reference)
        .first()
    )
    payment_created = payment is None
    if payment is None:
        payment = Payment.objects.create(
            tenant=tenancy.tenant,
            amount=move_in_payment.amount,
            reference=move_in_payment.transaction_reference,
            source=source,
            account_reference=move_in_payment.transaction_reference,
            payer_phone=tenancy.tenant.phone,
            instrument_status=Payment.INSTRUMENT_NOT_APPLICABLE,
            collected_by=actor if source == Payment.SOURCE_CASH else None,
            notes="Move-in payment confirmed during tenant onboarding.",
            received_at=received_at,
            status=Payment.STATUS_UNMATCHED,
        )
    elif (
        payment.tenant_id != tenancy.tenant_id
        or payment.amount != move_in_payment.amount
        or payment.source != source
    ):
        raise ValidationError(
            "The payment reference is already recorded with different details."
        )

    allocations = list(
        PaymentAllocation.objects.select_for_update()
        .filter(payment=payment)
        .order_by("id")
    )
    allocation_created = False
    if allocations:
        if (
            len(allocations) != 1
            or allocations[0].invoice_id != invoice.pk
            or allocations[0].amount != invoice.total
        ):
            raise ValidationError(
                "The payment reference is already allocated to another invoice."
            )
    else:
        PaymentAllocation.objects.create(
            payment=payment,
            invoice=invoice,
            amount=invoice.total,
        )
        allocation_created = True

    payment.allocated_amount = invoice.total
    payment.credit_amount = Decimal("0.00")
    payment.status = Payment.STATUS_ALLOCATED
    payment.save(
        update_fields=["allocated_amount", "credit_amount", "status"]
    )
    receipt_created = not Receipt.objects.filter(payment=payment).exists()
    receipt = _ensure_payment_receipt(payment, notify_whatsapp=False)
    if receipt is None:
        raise ValidationError("The move-in payment receipt could not be created.")
    documents = _persist_move_in_documents(
        tenancy,
        settlement,
        package,
        invoice,
        payment,
        receipt,
        actor,
        uploaded_storage_keys,
        identity_document,
    )
    return {
        "package": package,
        "package_created": package_created,
        "invoice": invoice,
        "invoice_created": invoice_created,
        "payment": payment,
        "payment_created": payment_created,
        "allocation_created": allocation_created,
        "receipt": receipt,
        "receipt_created": receipt_created,
        "documents": documents,
    }


@require_POST
def create_move_in_invoice(request, tenancy_id):
    error = _tenancy_access_error(request)
    if error:
        return error

    with transaction.atomic():
        tenancy = (
            Tenancy.objects.select_for_update()
            .filter(
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
        package = (
            MoveInPackage.objects.filter(tenancy=tenancy)
            .prefetch_related("charges")
            .first()
        )
        if package is None:
            return _problem(
                409,
                "Move-in invoice unavailable",
                "Generate the move-in package before issuing its invoice.",
                "move-in-package-required",
            )
        try:
            invoice, created = _create_move_in_invoice_for_tenancy(tenancy, package)
        except ValueError as exc:
            return _problem(
                409 if "already covers" in str(exc) else 400,
                "Move-in invoice unavailable",
                str(exc),
                "move-in-invoice-exists" if "already covers" in str(exc) else "missing-move-in-charges",
            )
        if not created:
            return JsonResponse({"invoice": _invoice_payload(invoice)})
    record_audit_event(
        request,
        "invoice.move_in_issued",
        "Invoice",
        new_values=_invoice_payload(invoice),
    )
    return JsonResponse({"invoice": _invoice_payload(invoice)}, status=201)


@require_GET
def get_move_in_status(request, tenancy_id):
    error = _tenancy_access_error(request)
    if error:
        return error
    tenancy = (
        Tenancy.objects.filter(
            pk=tenancy_id,
            unit__property__in=_accessible_properties(request.user),
        )
        .select_related("unit", "tenant")
        .first()
    )
    if tenancy is None:
        return _problem(
            404,
            "Tenancy not found",
            "The tenancy is unavailable or outside your access scope.",
            "tenancy-not-found",
        )

    sync_move_in_occupancy(tenancy.pk)
    tenancy.unit.refresh_from_db(fields=["status"])
    invoice = (
        Invoice.objects.filter(
            tenancy=tenancy,
            status=Invoice.STATUS_ISSUED,
            lines__metadata__move_in_charge_id__isnull=False,
        )
        .distinct()
        .prefetch_related("lines")
        .first()
    )
    if invoice is None or not MoveInPackage.objects.filter(tenancy=tenancy).exists():
        return _problem(
            404,
            "Move-in invoice not found",
            "Issue a move-in invoice before checking its payment status.",
            "move-in-invoice-not-found",
        )

    allocations = list(
        PaymentAllocation.objects.filter(invoice=invoice)
        .select_related("payment")
        .order_by("created_at", "pk")
    )
    confirmed_paid = sum(
        (
            allocation.amount
            for allocation in allocations
            if allocation.payment.instrument_status
            not in {Payment.INSTRUMENT_PENDING, Payment.INSTRUMENT_BOUNCED}
        ),
        Decimal("0.00"),
    )
    recorded_amount = sum(
        (allocation.amount for allocation in allocations),
        Decimal("0.00"),
    )
    outstanding = max(invoice.total - confirmed_paid, Decimal("0.00"))
    return JsonResponse(
        {
            "tenancy_id": tenancy.pk,
            "unit_status": tenancy.unit.status,
            "start_date": tenancy.start_date.isoformat(),
            "invoice": {
                "id": invoice.pk,
                "number": invoice.number,
                "total": str(invoice.total),
                "confirmed_paid": str(confirmed_paid),
                "recorded_amount": str(recorded_amount),
                "outstanding": str(outstanding),
                "status": "paid" if outstanding == 0 else "partially_paid" if confirmed_paid else "unpaid",
                "payments": [
                    {
                        "reference": allocation.payment.reference,
                        "source": allocation.payment.source,
                        "instrument_status": allocation.payment.instrument_status,
                        "amount": str(allocation.amount),
                        "received_at": allocation.payment.received_at.isoformat(),
                    }
                    for allocation in allocations
                ],
            },
        }
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
    if MoveInPackage.objects.filter(tenancy=tenancy).exists():
        invoice = Invoice.objects.filter(
            tenancy=tenancy,
            lines__metadata__move_in_charge_id__isnull=False,
        ).distinct().first()
        if invoice is None:
            return _problem(
                409,
                "Activation unavailable",
                "The move-in invoice must be issued and fully paid before activation can be emailed.",
                "activation-not-ready",
            )
        result = _send_move_in_invoice_activation(request, invoice.pk, force=True)
        if result is None:
            return _problem(
                409,
                "Activation unavailable",
                "The move-in invoice must be fully paid before activation can be emailed.",
                "activation-not-ready",
            )
        if not result["activation_email_sent"]:
            return _problem(
                502,
                "Activation email failed",
                result["activation_email_error"],
                "activation-email-failed",
            )
        return JsonResponse(result)
    return _issue_tenant_activation_challenge(request, tenancy)


def _issue_tenant_activation_challenge(request, tenancy):
    token = secrets.token_urlsafe(32)
    activation_code = _new_tenant_activation_code()
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
        "identity_required": tenant.is_provisional,
        "phone_verification_required": challenge.phone_verification_required,
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
    if isinstance(token, str) and (
        not isinstance(code, str) or len(code.strip()) != 6 or not code.strip().isdigit()
    ):
        return _problem(400, "Invalid request", "The activation link must be used with its six-digit email code.", "invalid-request")
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
    national_id_or_passport = body.get("national_id_or_passport")
    if full_name is not None and (not isinstance(full_name, str) or not full_name.strip()):
        return _problem(400, "Invalid request", "full_name must be a non-empty string.", "invalid-request")
    if phone is not None and not isinstance(phone, str):
        return _problem(400, "Invalid request", "phone must be a string.", "invalid-request")
    if email is not None and not isinstance(email, str):
        return _problem(400, "Invalid request", "email must be a string.", "invalid-request")
    if national_id_or_passport is not None and not isinstance(national_id_or_passport, str):
        return _problem(400, "Invalid request", "national_id_or_passport must be a string.", "invalid-request")
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
            if isinstance(token, str) and challenge.code_hash != _activation_hash(code.strip().upper()):
                challenge.failed_attempts += 1
                update_fields = ["failed_attempts"]
                if challenge.failed_attempts >= 5:
                    challenge.expires_at = timezone.now()
                    update_fields.append("expires_at")
                challenge.save(update_fields=update_fields)
                return _problem(400, "Invalid activation", "The activation code is incorrect or expired.", "invalid-activation")
            unit_code = body.get("unit_code")
            if isinstance(unit_code, str) and unit_code.strip() and (
                challenge.unit.code.casefold() != unit_code.strip().casefold()
            ):
                return _problem(400, "Invalid activation", "The activation code does not match this unit.", "invalid-activation")
            tenant = challenge.tenant
            was_provisional = tenant.is_provisional
            if (
                challenge.phone_verification_required
                and normalized_phone
                and normalized_phone != tenant.phone
            ):
                return _problem(
                    403,
                    "Phone verification required",
                    "The registered phone cannot be changed during activation. Contact the property office to update it.",
                    "activation-phone-mismatch",
                )
            if was_provisional:
                if (
                    not isinstance(national_id_or_passport, str)
                    or not national_id_or_passport.strip()
                    or len(national_id_or_passport.strip()) > 100
                ):
                    return _problem(400, "Identity required", "A national ID or passport number is required to activate this account.", "identity-required")
                tenant.national_id_or_passport = national_id_or_passport.strip()
                tenant.name = (full_name or tenant.name).strip()
                tenant.is_provisional = False
            if email and tenant.email and email.casefold() != tenant.email.casefold():
                return _problem(403, "Email mismatch", "The activation code is not assigned to this email address.", "email-mismatch")
            tenant_changed = was_provisional
            if challenge.phone_verification_required and tenant.phone_verified_at is None:
                tenant.phone_verified_at = timezone.now()
                tenant_changed = True
            if email and not tenant.email:
                tenant.email = email
                tenant_changed = True
            if normalized_phone and tenant.phone != normalized_phone:
                tenant.phone = normalized_phone
                tenant_changed = True
            if tenant_changed:
                tenant.full_clean()
                tenant.save(
                    update_fields=[
                        "name",
                        "national_id_or_passport",
                        "is_provisional",
                        "phone_verified_at",
                        "email",
                        "phone",
                        "updated_at",
                    ]
                )
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
                if not _has_role(user, TENANT_PORTAL_ROLE):
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
            tenant_portal_role, _ = Group.objects.get_or_create(name=TENANT_PORTAL_ROLE)
            user.groups.add(tenant_portal_role)
            membership, _ = AgencyMembership.objects.get_or_create(
                profile=profile,
                agency=tenant.branch.agency,
            )
            membership.branches.add(tenant.branch)
            membership.roles.add(tenant_portal_role)
            challenge.used_at = timezone.now()
            challenge.save(update_fields=["used_at"])
            MoveInSettlement.objects.filter(
                tenant=tenant,
                status__in={
                    MoveInSettlement.STATUS_PAID,
                    MoveInSettlement.STATUS_MOVE_IN_PENDING,
                },
            ).update(
                status=MoveInSettlement.STATUS_ACTIVE,
                activated_at=timezone.now(),
            )
    except ValidationError as exc:
        return _problem(400, "Invalid activation", "; ".join(exc.messages), "invalid-activation")
    except IntegrityError:
        return _problem(409, "Account conflict", "The tenant account could not be created because its email or phone is already in use.", "account-conflict")
    record_audit_event(
        request,
        "tenant_activation.completed",
        "TenantActivationChallenge",
        new_values={"id": challenge.pk, "tenant_id": tenant.pk, "user_id": user.pk},
        actor=user,
    )
    return JsonResponse({"activated": True, "tenant_id": tenant.pk})


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
            locked_unit.transition_to(Unit.STATUS_BOOKED)
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
    if request.content_type != "multipart/form-data":
        return _problem(
            400,
            "Invalid document",
            "Submit a multipart form with document_type, name, and file.",
            "invalid-document",
        )
    document_type = request.POST.get("document_type", "").strip()
    name = request.POST.get("name", "").strip()
    upload = request.FILES.get("file")
    if (
        document_type
        not in {"signed_agreement", "identity_document", "proof_of_payment", "other"}
        or not upload
        or not name
        or len(name) > 255
    ):
        return _problem(
            400,
            "Invalid request",
            "Choose a supported document type and provide a name of at most 255 characters and a file.",
            "invalid-request",
        )
    tenancy_id = request.POST.get("tenancy_id", "").strip()
    tenancy = None
    if tenancy_id:
        try:
            tenancy = Tenancy.objects.filter(
                pk=int(tenancy_id),
                tenant=tenant,
                unit__property__in=_accessible_properties(request.user),
            ).first()
        except (TypeError, ValueError):
            tenancy = None
        if tenancy is None:
            return _problem(
                404,
                "Tenancy not found",
                "The tenancy is unavailable or outside your access scope.",
                "tenancy-not-found",
            )
    file_details, file_error = _tenant_document_file_details(upload)
    if file_error or file_details is None:
        return _problem(
            400,
            "Invalid document",
            file_error or "The uploaded file type could not be determined.",
            "invalid-document",
        )
    content_type, extension = file_details
    storage_key = f"tenant-documents/{tenant.pk}/{uuid.uuid4().hex}.{extension}"
    try:
        upload_private_object(storage_key, upload, content_type)
        document = TenantDocument.objects.create(
            tenant=tenant,
            document_type=document_type,
            name=name,
            storage_key=storage_key,
            metadata={
                "content_type": content_type,
                "size_bytes": upload.size,
                "tenancy_id": tenancy.pk if tenancy else None,
            },
            uploaded_by=request.user,
        )
    except (BotoCoreError, ClientError, ImproperlyConfigured, OSError):
        try:
            delete_private_object(storage_key)
        except (BotoCoreError, ClientError, ImproperlyConfigured, OSError):
            logger.exception("Could not remove incomplete tenant document object %s.", storage_key)
        logger.exception("Tenant document upload failed.")
        return _problem(
            503,
            "Document storage unavailable",
            "The document could not be stored. Please try again later.",
            "document-storage-unavailable",
        )
    except DatabaseError:
        try:
            delete_private_object(storage_key)
        except (BotoCoreError, ClientError, ImproperlyConfigured, OSError):
            logger.exception("Could not remove incomplete tenant document object %s.", storage_key)
        logger.exception("Could not save tenant document metadata.")
        return _problem(
            503,
            "Document could not be saved",
            "The document record could not be saved. Please try again later.",
            "document-save-failed",
        )
    record_audit_event(
        request,
        "tenant_document.created",
        "TenantDocument",
        new_values={
            "id": document.pk,
            "tenant_id": tenant.pk,
            "document_type": document.document_type,
            "name": document.name,
            "branch_id": tenant.branch_id,
            "tenancy_id": tenancy.pk if tenancy else None,
        },
    )
    return JsonResponse(
        {
            "document": {
                "id": document.pk,
                "document_type": document.document_type,
                "name": document.name,
                "metadata": document.metadata,
                "download_path": (
                    f"/api/v1/tenants/{tenant.pk}/documents/{document.pk}"
                ),
            }
        },
        status=201,
    )


@require_GET
def get_tenant_document(request, tenant_id, document_id):
    error = _tenant_access_error(request)
    if error:
        return error
    tenant = _accessible_tenants(request.user).filter(pk=tenant_id).first()
    document = (
        TenantDocument.objects.filter(pk=document_id, tenant=tenant)
        .first()
        if tenant
        else None
    )
    if document is None:
        return _problem(
            404,
            "Document not found",
            "The document is unavailable or outside your access scope.",
            "tenant-document-not-found",
        )
    try:
        content = read_private_object(document.storage_key)
    except (BotoCoreError, ClientError, ImproperlyConfigured, OSError):
        logger.exception("Tenant document download failed for document %s.", document.pk)
        return _problem(
            503,
            "Document storage unavailable",
            "The document could not be retrieved. Please try again later.",
            "document-storage-unavailable",
        )
    content_type = document.metadata.get("content_type", "application/octet-stream")
    safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", document.name).strip() or f"tenant-document-{document.pk}"
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response
def _inspection_payload(inspection, include_private_token=True):
    payload = {
        "id": inspection.pk,
        "property_id": inspection.property_id,
        "property_name": inspection.property.name,
        "unit_id": inspection.unit_id,
        "unit_code": inspection.unit.code if inspection.unit_id else None,
        "move_out_notice_id": inspection.move_out_notice_id,
        "title": inspection.title,
        "inspection_type": inspection.inspection_type,
        "scheduled_for": inspection.scheduled_for.isoformat(),
        "responsible_staff_id": inspection.responsible_staff_id,
        "created_by_id": inspection.created_by_id,
        "status": inspection.status,
        "checklist": inspection.checklist,
        "photos": inspection.photos,
        "actual_move_out_date": (
            inspection.actual_move_out_date.isoformat()
            if inspection.actual_move_out_date
            else None
        ),
        "final_readings": inspection.final_readings,
        "damage_items": inspection.damage_items,
        "maintenance_required": inspection.maintenance_required,
        "findings": inspection.findings,
        "recommendations": inspection.recommendations,
        "metered_utilities": (
            [
                {
                    "utility_type": utility.utility_type,
                    "name": utility.name,
                    "meter": utility.metadata.get("meter")
                    or inspection.unit.meters.get(utility.utility_type),
                    "previous_reading": _move_out_meter_baseline(
                        inspection.unit,
                        utility,
                        inspection.actual_move_out_date
                        or timezone.localtime(inspection.scheduled_for).date(),
                    ),
                }
                for utility in _move_out_utility_configs(
                    inspection.unit,
                    inspection.actual_move_out_date
                    or timezone.localtime(inspection.scheduled_for).date(),
                )
            ]
            if inspection.unit_id
            and inspection.inspection_type.lower() in {"move_out", "exit"}
            else []
        ),
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
    if _is_caretaker(request.user) and not _has_landlord_management_access(request.user):
        inspections = inspections.filter(responsible_staff=request.user)
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
        if (
            body["inspection_type"].strip().lower() in {"move_out", "exit"}
            and not checklist
        ):
            checklist = [
                {"title": "Room condition and damage", "status": "pending"},
                {"title": "Fixtures, keys, and access devices", "status": "pending"},
                {"title": "Final utility meter readings", "status": "pending"},
            ]
    except (KeyError, TypeError, ValueError, Property.DoesNotExist, get_user_model().DoesNotExist):
        return _problem(
            400,
            "Invalid inspection",
            "property_id, title, inspection_type, scheduled_for, and responsible_staff_id are required.",
            "invalid-inspection",
        )
    if (
        body["inspection_type"].strip().lower() in {"move_out", "exit"}
        and not _has_landlord_management_access(request.user)
    ):
        return _problem(
            403,
            "Permission denied",
            "Only property-management staff can schedule an exit inspection.",
            "inspection-access-denied",
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
        with transaction.atomic():
            inspection.full_clean()
            inspection.save()
            record_audit_event(
                request,
                "property_inspection.created",
                "PropertyInspection",
                new_values=_inspection_payload(inspection),
            )
            if inspection.inspection_type.lower() in {"move_out", "exit"}:
                tenancy = (
                    Tenancy.objects.filter(
                        unit=inspection.unit,
                        status=Tenancy.STATUS_ACTIVE,
                    )
                    .select_related("tenant")
                    .first()
                )
                if tenancy:
                    try:
                        context = {
                            "tenant_name": tenancy.tenant.name,
                            "property_name": inspection.property.name,
                            "unit_code": inspection.unit.code,
                            "move_out_date": timezone.localtime(
                                inspection.scheduled_for
                            ).date().isoformat(),
                        }
                        _queue_move_out_user_sms(
                            responsible_staff,
                            "move_out_inspection_assigned",
                            context,
                            request.user,
                            request,
                        )
                        _queue_move_out_tenant_sms(
                            tenancy,
                            "tenant_move_out_inspection_scheduled",
                            context,
                            request.user,
                            request,
                        )
                    except (ImproperlyConfigured, ValueError) as notification_error:
                        transaction.set_rollback(True)
                        logger.exception("Could not queue move-out inspection assignment SMS.")
                        return _problem(
                            503,
                            "Move-out notification unavailable",
                            str(notification_error),
                            "move-out-notification-unavailable",
                        )
    except ValidationError as exc:
        return _problem(400, "Invalid inspection", str(exc.message_dict), "invalid-inspection")
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
    move_out_inspection = inspection.inspection_type.lower() in {"move_out", "exit"}
    actual_move_out_date = inspection.actual_move_out_date
    final_readings = inspection.final_readings
    damage_items = inspection.damage_items
    maintenance_required = inspection.maintenance_required
    if move_out_inspection:
        if not checklist or any(
            item.get("status") not in {"complete", "completed"}
            for item in checklist
        ):
            return _problem(
                400,
                "Incomplete inspection",
                "Complete every exit checklist item before submitting the inspection.",
                "incomplete-exit-inspection",
            )
        if inspection.unit_id is None:
            return _problem(
                400,
                "Invalid inspection",
                "Exit inspections must be linked to a unit.",
                "invalid-inspection",
            )
        move_out_tenancy = (
            Tenancy.objects.filter(
                unit_id=inspection.unit_id,
                status=Tenancy.STATUS_ACTIVE,
                move_out_notices__status=MoveOutNotice.STATUS_ACCEPTED,
            )
            .select_related("tenant")
            .first()
        )
        if move_out_tenancy is None:
            return _problem(
                409,
                "Move-out notice required",
                "An accepted move-out notice is required before completing an exit inspection.",
                "move-out-notice-required",
            )
        try:
            actual_move_out_date = date.fromisoformat(
                str(body.get("actual_move_out_date", ""))
            )
        except ValueError:
            return _problem(
                400,
                "Invalid inspection",
                "A valid actual_move_out_date is required for an exit inspection.",
                "invalid-inspection",
            )
        if actual_move_out_date < move_out_tenancy.start_date:
            return _problem(
                400,
                "Invalid inspection",
                "The actual move-out date cannot precede the tenancy start.",
                "invalid-inspection",
            )
        if (
            inspection.move_out_notice_id
            and actual_move_out_date
            < inspection.move_out_notice.proposed_move_out_date
        ):
            return _problem(
                400,
                "Inspection not yet due",
                "The assigned exit inspection cannot be completed before the proposed move-out date.",
                "move-out-inspection-not-due",
            )
        if actual_move_out_date > timezone.localdate():
            return _problem(
                400,
                "Invalid inspection",
                "The actual move-out date cannot be in the future.",
                "invalid-inspection",
            )
        try:
            submitted_readings = body.get("final_readings", [])
            final_readings = _validate_move_out_inspection_readings(
                inspection.unit,
                actual_move_out_date,
                submitted_readings,
            )
            damage_items, _ = _move_out_damage_items(body.get("damage_items", []))
        except ValueError as exc:
            return _problem(400, "Invalid inspection", str(exc), "invalid-inspection")
        maintenance_required = body.get("maintenance_required", False)
        if not isinstance(maintenance_required, bool):
            return _problem(
                400,
                "Invalid inspection",
                "maintenance_required must be a boolean.",
                "invalid-inspection",
            )
    try:
        with transaction.atomic():
            inspection.checklist = checklist
            inspection.photos = photos
            inspection.actual_move_out_date = actual_move_out_date
            inspection.final_readings = final_readings
            inspection.damage_items = damage_items
            inspection.maintenance_required = maintenance_required
            inspection.findings = str(body.get("findings", inspection.findings)).strip()
            inspection.recommendations = str(body.get("recommendations", inspection.recommendations)).strip()
            if move_out_inspection:
                final_readings, _ = _move_out_final_readings(
                    inspection.unit,
                    actual_move_out_date,
                    final_readings,
                    request,
                )
                inspection.final_readings = final_readings
            inspection.status = PropertyInspection.STATUS_COMPLETED
            inspection.completed_at = timezone.now()
            inspection.full_clean()
            inspection.save()
            inspection_payload = _inspection_payload(inspection)
            audit_source = getattr(request, "audit_source", "")
            if audit_source:
                inspection_payload["source"] = audit_source
            record_audit_event(
                request,
                "property_inspection.completed",
                "PropertyInspection",
                new_values=inspection_payload,
            )
            if move_out_inspection:
                tenancy = move_out_tenancy
                if tenancy:
                    context = {
                        "tenant_name": tenancy.tenant.name,
                        "property_name": inspection.property.name,
                        "unit_code": inspection.unit.code,
                    }
                    try:
                        _queue_move_out_tenant_sms(
                            tenancy,
                            "move_out_inspection_completed",
                            context,
                            request.user,
                            request,
                        )
                        _queue_move_out_staff_sms(
                            inspection.property,
                            (BRANCH_MANAGER_ROLE, "Property Officer / Agent"),
                            "move_out_inspection_completed",
                            context,
                            request.user,
                            request,
                        )
                    except (ImproperlyConfigured, ValueError) as notification_error:
                        transaction.set_rollback(True)
                        logger.exception("Could not queue move-out inspection completion SMS.")
                        return _problem(
                            503,
                            "Move-out notification unavailable",
                            str(notification_error),
                            "move-out-notification-unavailable",
                        )
    except ValidationError as exc:
        return _problem(400, "Invalid inspection", str(exc.message_dict), "invalid-inspection")
    except ValueError as exc:
        return _problem(400, "Invalid inspection", str(exc), "invalid-inspection")
    except (ImproperlyConfigured, RuntimeError, OSError) as exc:
        logger.exception("Could not queue move-out inspection notification.")
        return _problem(
            503,
            "Move-out notification unavailable",
            str(exc),
            "move-out-notification-unavailable",
        )
    except IntegrityError:
        return _problem(409, "Inspection conflict", "This inspection changed before completion.", "inspection-conflict")
    return JsonResponse({"inspection": _inspection_payload(inspection)})


@require_POST
def upload_inspection_photos(request, inspection_id):
    error = _inspection_access_error(request)
    if error:
        return error
    inspection = _accessible_inspections(request.user).filter(pk=inspection_id).first()
    if inspection is None:
        return _problem(404, "Inspection not found", "The inspection is unavailable.", "inspection-not-found")
    if not _has_landlord_management_access(request.user) and inspection.responsible_staff_id != request.user.pk:
        return _problem(403, "Permission denied", "Only the responsible caretaker can upload inspection photos.", "inspection-access-denied")
    if (
        inspection.status == PropertyInspection.STATUS_COMPLETED
        or inspection.inspection_type.lower() not in {"move_out", "exit"}
    ):
        return _problem(409, "Inspection unavailable", "Photos can only be added to an incomplete exit inspection.", "inspection-photo-conflict")
    files = request.FILES.getlist("photos")
    if not files:
        return _problem(400, "Invalid evidence", "At least one inspection photo is required.", "invalid-evidence")
    try:
        prepared = _prepare_tenant_evidence(files)
    except ValueError as exc:
        return _problem(400, "Invalid evidence", str(exc), "invalid-evidence")
    if len(inspection.photos) + len(prepared) > 10:
        return _problem(400, "Invalid evidence", "An exit inspection can include at most 10 photos.", "invalid-evidence")

    uploaded_keys = []
    new_photos = []
    try:
        for item in prepared:
            key = f"inspections/move-out/{inspection.pk}/{uuid.uuid4().hex}.{item['extension']}"
            uploaded_keys.append(key)
            upload_private_object(key, item["file"], item["content_type"])
            new_photos.append(
                {
                    "storage_key": key,
                    "content_type": item["content_type"],
                    "size_bytes": item["size_bytes"],
                    "name": f"Exit inspection evidence {len(inspection.photos) + len(new_photos) + 1}.{item['extension']}",
                }
            )
        with transaction.atomic():
            locked = PropertyInspection.objects.select_for_update().get(pk=inspection.pk)
            if locked.status == PropertyInspection.STATUS_COMPLETED:
                _cleanup_tenant_evidence(uploaded_keys)
                return _problem(409, "Inspection unavailable", "The inspection was completed before the photos could be attached.", "inspection-photo-conflict")
            locked.photos = [*locked.photos, *new_photos]
            locked.full_clean()
            locked.save(update_fields=["photos", "updated_at"])
            record_audit_event(
                request,
                "property_inspection.photos_added",
                "PropertyInspection",
                new_values=_inspection_payload(locked),
            )
    except (BotoCoreError, ClientError, ImproperlyConfigured, OSError):
        _cleanup_tenant_evidence(uploaded_keys)
        logger.exception("Move-out inspection photo upload failed.")
        return _problem(
            503,
            "Evidence storage unavailable",
            "The inspection photos could not be stored. Please try again.",
            "inspection-evidence-unavailable",
        )
    except ValidationError as exc:
        _cleanup_tenant_evidence(uploaded_keys)
        return _problem(400, "Invalid evidence", str(exc.message_dict), "invalid-evidence")
    except DatabaseError:
        _cleanup_tenant_evidence(uploaded_keys)
        logger.exception("Could not save uploaded move-out inspection photos.")
        return _problem(
            503,
            "Inspection evidence unavailable",
            "The inspection photos could not be linked to the inspection.",
            "inspection-evidence-unavailable",
        )
    return JsonResponse({"inspection": _inspection_payload(locked)}, status=201)


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
    if not user.is_authenticated or not _has_role(user, TENANT_PORTAL_ROLE):
        return None
    return Tenant.objects.filter(portal_user=user).first()


def _tenant_maintenance_context(request):
    if not request.user.is_authenticated:
        return None, None, _problem(
            401,
            "Authentication required",
            "Sign in first.",
            "authentication-required",
        )
    tenant = _tenant_record_for_user(request.user)
    if tenant is None:
        if _has_role(request.user, TENANT_PORTAL_ROLE):
            return None, None, _problem(
                404,
                "Tenant record not found",
                "This tenant account is not linked to a tenant record.",
                "tenant-not-found",
            )
        return None, None, _problem(
            403,
            "Permission denied",
            "Tenant portal access is required.",
            "tenant-access-denied",
        )
    tenancy = (
        Tenancy.objects.filter(
            tenant=tenant,
            status=Tenancy.STATUS_ACTIVE,
            move_out_settlement__isnull=True,
        )
        .select_related("unit__property")
        .order_by("-start_date", "-id")
        .first()
    )
    return tenant, tenancy, None


def _tenant_maintenance_ticket_payload(ticket):
    return {
        "id": ticket.pk,
        "title": ticket.title,
        "description": ticket.description,
        "category": ticket.category,
        "priority": ticket.priority,
        "status": ticket.status,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
        "evidence": [
            {
                "name": item.get("name") or f"Evidence {index + 1}",
                "content_type": item.get("content_type"),
                "size_bytes": item.get("size_bytes"),
                "download_path": (
                    f"/api/v1/tenant/maintenance-tickets/{ticket.pk}/evidence/{index}"
                ),
            }
            for index, item in enumerate(ticket.photos)
            if isinstance(item, dict)
        ],
    }


def _tenant_maintenance_comment_payload(comment, tenant_user):
    profile = getattr(comment.author, "profile", None)
    return {
        "id": comment.pk,
        "sender_role": "tenant" if comment.author_id == tenant_user.pk else "staff",
        "sender_name": (
            getattr(profile, "name", "")
            or comment.author.get_full_name()
            or comment.author.email
        ),
        "message": comment.body,
        "created_at": comment.created_at.isoformat(),
    }


def _prepare_tenant_evidence(files, max_files=5):
    if len(files) > max_files:
        raise ValueError(f"A request can include at most {max_files} evidence images.")
    if sum(upload.size for upload in files) > 20 * 1024 * 1024:
        raise ValueError("Evidence images must total no more than 20 MB.")
    prepared = []
    signatures = (
        (b"\xff\xd8\xff", "image/jpeg", "jpg"),
        (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    )
    for upload in files:
        if upload.size <= 0 or upload.size > 5 * 1024 * 1024:
            raise ValueError("Each evidence image must be from 1 byte to 5 MB.")
        header = upload.read(12)
        upload.seek(0)
        match = next(
            (
                (content_type, extension)
                for signature, content_type, extension in signatures
                if header.startswith(signature)
            ),
            None,
        )
        if match is None and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            match = ("image/webp", "webp")
        if match is None:
            raise ValueError("Evidence files must be JPEG, PNG, or WebP images.")
        content_type, extension = match
        prepared.append(
            {
                "file": upload,
                "content_type": content_type,
                "extension": extension,
                "size_bytes": upload.size,
            }
        )
    return prepared


def _cleanup_tenant_evidence(keys):
    for key in keys:
        try:
            delete_private_object(key)
        except (BotoCoreError, ClientError, ImproperlyConfigured, OSError):
            logger.exception("Could not remove incomplete maintenance evidence object %s.", key)


@require_GET
def list_tenant_maintenance_tickets(request):
    tenant, _, error = _tenant_maintenance_context(request)
    if error:
        return error
    tickets = MaintenanceTicket.objects.filter(reported_by=request.user)
    payload = [_tenant_maintenance_ticket_payload(ticket) for ticket in tickets]
    record_audit_event(
        request,
        "tenant_maintenance.viewed",
        "MaintenanceTicket",
        new_values={"tenant_id": tenant.pk, "ticket_count": len(payload)},
    )
    return JsonResponse({"tickets": payload})


@require_POST
def create_tenant_maintenance_ticket(request):
    tenant, tenancy, error = _tenant_maintenance_context(request)
    if error:
        return error
    if tenancy is None:
        return _problem(
            403,
            "Active tenancy required",
            "An active tenancy is required to submit a maintenance request.",
            "active-tenancy-required",
        )
    if request.content_type == "application/json":
        body = _json_body(request)
        files = []
    elif request.content_type == "multipart/form-data":
        body = request.POST
        files = request.FILES.getlist("evidence")
    else:
        return _problem(
            400,
            "Invalid ticket",
            "Submit a multipart form with an optional evidence field.",
            "invalid-ticket",
        )
    category = body.get("category") if body else None
    description = body.get("description") if body else None
    priority = body.get("priority", MaintenanceTicket.PRIORITY_NORMAL) if body else None
    if not isinstance(category, str) or category not in dict(MaintenanceTicket.CATEGORY_CHOICES):
        return _problem(400, "Invalid ticket", "The category is not supported.", "invalid-ticket")
    if not isinstance(description, str) or not description.strip() or len(description) > 5000:
        return _problem(
            400,
            "Invalid ticket",
            "Description is required and must be at most 5000 characters.",
            "invalid-ticket",
        )
    if not isinstance(priority, str) or priority not in dict(MaintenanceTicket.PRIORITY_CHOICES):
        return _problem(400, "Invalid ticket", "The priority is not supported.", "invalid-ticket")
    try:
        evidence = _prepare_tenant_evidence(files)
    except ValueError as exc:
        return _problem(400, "Invalid evidence", str(exc), "invalid-evidence")

    description = description.strip()
    ticket = MaintenanceTicket(
        property=tenancy.unit.property,
        unit=tenancy.unit,
        reported_by=request.user,
        title=description[:252] + ("..." if len(description) > 255 else ""),
        description=description,
        category=category,
        priority=priority,
    )
    try:
        ticket.full_clean()
    except ValidationError as exc:
        return _problem(400, "Invalid ticket", str(exc.message_dict), "invalid-ticket")

    uploaded_keys = []
    photos = []
    try:
        for item in evidence:
            key = (
                f"maintenance/tenant/{tenant.pk}/"
                f"{uuid.uuid4().hex}.{item['extension']}"
            )
            uploaded_keys.append(key)
            upload_private_object(key, item["file"], item["content_type"])
            photos.append(
                {
                    "storage_key": key,
                    "content_type": item["content_type"],
                    "size_bytes": item["size_bytes"],
                    "name": f"Evidence {len(photos) + 1}.{item['extension']}",
                }
            )
        ticket.photos = photos
        ticket.full_clean()
        with transaction.atomic():
            ticket.save()
    except (BotoCoreError, ClientError, ImproperlyConfigured, OSError):
        _cleanup_tenant_evidence(uploaded_keys)
        logger.exception("Tenant maintenance evidence upload failed.")
        return _problem(
            503,
            "Evidence storage unavailable",
            "Evidence could not be stored. Please try again later.",
            "evidence-storage-unavailable",
        )
    except ValidationError as exc:
        _cleanup_tenant_evidence(uploaded_keys)
        return _problem(400, "Invalid ticket", str(exc.message_dict), "invalid-ticket")
    except DatabaseError:
        _cleanup_tenant_evidence(uploaded_keys)
        logger.exception("Could not save tenant maintenance ticket.")
        return _problem(
            500,
            "Ticket could not be saved",
            "The request could not be saved. Please try again.",
            "ticket-save-failed",
        )

    record_audit_event(
        request,
        "maintenance_ticket.tenant_created",
        "MaintenanceTicket",
        new_values=_tenant_maintenance_ticket_payload(ticket),
    )
    return JsonResponse({"ticket": _tenant_maintenance_ticket_payload(ticket)}, status=201)


@require_http_methods(["GET", "POST"])
def tenant_maintenance_ticket_comments(request, ticket_id):
    _, _, error = _tenant_maintenance_context(request)
    if error:
        return error
    ticket = MaintenanceTicket.objects.filter(
        pk=ticket_id,
        reported_by=request.user,
    ).first()
    if ticket is None:
        return _problem(404, "Ticket not found", "The maintenance ticket is unavailable.", "ticket-not-found")
    if request.method == "GET":
        staff_roles = [
            CARETAKER_ROLE,
            LANDLORD_PORTAL_ROLE,
            *LANDLORD_MANAGEMENT_ROLE_NAMES,
        ]
        comments = ticket.comments.filter(
            models.Q(author=request.user)
            | models.Q(
                author__profile__agency_memberships__agency=ticket.property.branch.agency,
                author__profile__agency_memberships__agency__is_active=True,
                author__profile__agency_memberships__roles__name__in=staff_roles,
            )
        ).select_related("author", "author__profile").distinct()
        return JsonResponse(
            {
                "comments": [
                    _tenant_maintenance_comment_payload(comment, request.user)
                    for comment in comments
                ]
            }
        )

    body = _json_body(request)
    message = body.get("body") if body else None
    if not isinstance(message, str) or not message.strip() or len(message) > 5000:
        return _problem(
            400,
            "Invalid comment",
            "A non-empty comment of at most 5000 characters is required.",
            "invalid-comment",
        )
    if ticket.status in {
        MaintenanceTicket.STATUS_COMPLETED,
        MaintenanceTicket.STATUS_CLOSED,
    }:
        return _problem(
            409,
            "Ticket is closed",
            "Comments cannot be added to a completed or closed ticket.",
            "ticket-closed",
        )
    comment = MaintenanceTicketComment(
        ticket=ticket,
        author=request.user,
        body=message.strip(),
    )
    try:
        comment.full_clean()
        comment.save()
    except ValidationError as exc:
        return _problem(400, "Invalid comment", str(exc.message_dict), "invalid-comment")
    record_audit_event(
        request,
        "maintenance_ticket.tenant_comment_added",
        "MaintenanceTicketComment",
        new_values={"id": comment.pk, "ticket_id": ticket.pk},
    )
    return JsonResponse(
        {"comment": _tenant_maintenance_comment_payload(comment, request.user)},
        status=201,
    )


@require_GET
def get_tenant_maintenance_evidence(request, ticket_id, evidence_index):
    tenant, _, error = _tenant_maintenance_context(request)
    if error:
        return error
    ticket = MaintenanceTicket.objects.filter(
        pk=ticket_id,
        reported_by=request.user,
    ).first()
    if ticket is None or evidence_index >= len(ticket.photos):
        return _problem(404, "Evidence not found", "The evidence is unavailable.", "evidence-not-found")
    evidence = ticket.photos[evidence_index]
    storage_key = evidence.get("storage_key") if isinstance(evidence, dict) else None
    if (
        not isinstance(storage_key, str)
        or not storage_key.startswith(f"maintenance/tenant/{tenant.pk}/")
        or evidence.get("content_type") not in {"image/jpeg", "image/png", "image/webp"}
    ):
        return _problem(404, "Evidence not found", "The evidence is unavailable.", "evidence-not-found")
    try:
        content = read_private_object(storage_key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return _problem(
                404,
                "Evidence not found",
                "The evidence is unavailable.",
                "evidence-not-found",
            )
        logger.exception("Could not retrieve tenant maintenance evidence.")
        return _problem(
            503,
            "Evidence storage unavailable",
            "Evidence could not be loaded. Please try again later.",
            "evidence-storage-unavailable",
        )
    except (BotoCoreError, ImproperlyConfigured, OSError):
        logger.exception("Could not retrieve tenant maintenance evidence.")
        return _problem(
            503,
            "Evidence storage unavailable",
            "Evidence could not be loaded. Please try again later.",
            "evidence-storage-unavailable",
        )
    record_audit_event(
        request,
        "maintenance_ticket.evidence_viewed",
        "MaintenanceTicket",
        new_values={"ticket_id": ticket.pk, "evidence_index": evidence_index},
    )
    response = HttpResponse(content, content_type=evidence["content_type"])
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = "inline"
    return response


def _property_paybill_number(property_obj):
    billing_settings = getattr(property_obj, "billing_settings", None)
    if billing_settings and billing_settings.paybill_number:
        return billing_settings.paybill_number
    branch_paybills = property_obj.branch.paybill_numbers
    if isinstance(branch_paybills, list) and branch_paybills:
        return str(branch_paybills[0])
    if isinstance(branch_paybills, str):
        return branch_paybills
    return None


@require_GET
def tenant_dashboard(request):
    if not request.user.is_authenticated:
        return _problem(
            401,
            "Authentication required",
            "Sign in first.",
            "authentication-required",
        )
    if not _has_role(request.user, TENANT_PORTAL_ROLE):
        return _problem(
            403,
            "Permission denied",
            "Tenant portal access is required.",
            "tenant-portal-access-denied",
        )

    tenant = Tenant.objects.filter(portal_user=request.user).first()
    if tenant is None:
        return _problem(
            404,
            "Tenant record not found",
            "No tenant record is linked to this account.",
            "tenant-record-not-found",
        )

    today = timezone.localdate()
    zero = Decimal("0.00")
    invoice_rows = []
    current_balance = zero
    outstanding_due_dates = []
    invoices = list(
        Invoice.objects.filter(
            tenancy__tenant=tenant,
            status=Invoice.STATUS_ISSUED,
        )
        .select_related("branch__arrears_policy", "tenancy__unit__property")
        .prefetch_related("lines")
        .order_by("-period_start", "-id")
    )
    invoice_ids = [invoice.pk for invoice in invoices]
    allocated_by_invoice = {
        row["invoice_id"]: row["total"] or zero
        for row in (
            PaymentAllocation.objects.filter(
                invoice_id__in=invoice_ids,
                created_at__date__lte=today,
                payment__received_at__date__lte=today,
            )
            .exclude(
                payment__instrument_status__in=(
                    Payment.INSTRUMENT_PENDING,
                    Payment.INSTRUMENT_BOUNCED,
                )
            )
            .values("invoice_id")
            .annotate(total=Sum("amount"))
        )
    }
    waived_by_invoice = {
        row["invoice_id"]: row["total"] or zero
        for row in (
            ArrearsWaiver.objects.filter(
                invoice_id__in=invoice_ids,
                approved_at__date__lte=today,
            )
            .values("invoice_id")
            .annotate(total=Sum("amount"))
        )
    }
    waiver_details_by_invoice = {}
    for waiver in ArrearsWaiver.objects.filter(
        invoice_id__in=invoice_ids,
        approved_at__date__lte=today,
    ).order_by("approved_at", "id"):
        waiver_details_by_invoice.setdefault(waiver.invoice_id, []).append(
            {
                "amount": str(waiver.amount),
                "reason": waiver.reason,
                "approved_at": waiver.approved_at.isoformat(),
            }
        )
    for invoice in invoices:
        amount_paid = allocated_by_invoice.get(invoice.pk, zero)
        waived_amount = waived_by_invoice.get(invoice.pk, zero)
        arrears = invoice_arrears(
            invoice,
            today,
            allocated_amount=amount_paid,
            waiver_amount=waived_amount,
        )
        balance_due = arrears["total_due"]
        current_balance += balance_due
        if balance_due > zero:
            outstanding_due_dates.append(invoice.due_date)
        if balance_due == zero:
            status = "settled"
        elif amount_paid or waived_amount:
            status = "partial"
        else:
            status = "unpaid"
        invoice_rows.append(
            {
                "id": invoice.pk,
                "number": invoice.number,
                "invoice_type": invoice.invoice_type,
                "period_start": invoice.period_start.isoformat(),
                "period_end": invoice.period_end.isoformat(),
                "due_date": invoice.due_date.isoformat(),
                "status": status,
                "total": str(invoice.total),
                "amount_paid": str(amount_paid),
                "waived_amount": str(waived_amount),
                "approved_waivers": waiver_details_by_invoice.get(invoice.pk, []),
                "penalty": str(arrears["penalty"]),
                "balance_due": str(balance_due),
                "lines": [
                    {
                        "id": line.pk,
                        "charge_type": line.charge_type,
                        "description": line.description,
                        "amount": str(line.amount),
                    }
                    for line in invoice.lines.all()
                ],
            }
        )

    payments = list(
        Payment.objects.filter(tenant=tenant)
        .prefetch_related("allocations__invoice")
        .order_by("-received_at", "-id")
    )
    receipts_by_payment = {
        receipt.payment_id: receipt
        for receipt in Receipt.objects.filter(
            tenant=tenant,
            payment_id__in=[payment.pk for payment in payments],
        )
    }
    payment_rows = [
        {
            "id": payment.pk,
            "amount": str(payment.amount),
            "reference": payment.reference,
            "source": payment.source,
            "status": payment.status,
            "instrument_status": payment.instrument_status,
            "received_at": payment.received_at.isoformat(),
            "allocations": [
                {
                    "invoice_id": allocation.invoice_id,
                    "invoice_number": allocation.invoice.number,
                    "amount": str(allocation.amount),
                }
                for allocation in payment.allocations.all()
            ],
            "receipt": (
                _receipt_payload(receipts_by_payment[payment.pk])
                if payment.pk in receipts_by_payment
                else None
            ),
        }
        for payment in payments
    ]

    active_tenancy = (
        Tenancy.objects.filter(
            tenant=tenant,
            status=Tenancy.STATUS_ACTIVE,
            move_out_settlement__isnull=True,
        )
        .select_related("unit__property__branch")
        .order_by("-start_date", "-id")
        .first()
    )
    deposit_account = (
        DepositAccount.objects.filter(tenancy=active_tenancy)
        .prefetch_related("entries")
        .first()
        if active_tenancy
        else None
    )
    move_out_settlements = MoveOutSettlement.objects.filter(
        tenancy__tenant=tenant
    ).select_related(
        "tenancy__unit__property__branch",
        "exit_inspection",
        "notice",
    ).order_by("-created_at")
    paybill_number = (
        _property_paybill_number(active_tenancy.unit.property)
        if active_tenancy
        else None
    )

    profile = {
        "id": tenant.pk,
        "name": tenant.name,
        "email": tenant.email or request.user.email,
        "phone": tenant.phone,
        "unit_code": active_tenancy.unit.code if active_tenancy else None,
        "property_name": active_tenancy.unit.property.name if active_tenancy else None,
        "paybill_number": paybill_number,
        "move_in_date": active_tenancy.start_date.isoformat() if active_tenancy else None,
        "notice_period_days": active_tenancy.notice_period_days if active_tenancy else None,
    }
    credit_balance = available_tenant_credit(tenant)
    net_balance_due = max(current_balance - credit_balance, zero).quantize(
        Decimal("0.01")
    )
    record_audit_event(
        request,
        "tenant_dashboard.viewed",
        "TenantDashboard",
        new_values={
            "tenant_id": tenant.pk,
            "invoice_count": len(invoice_rows),
            "payment_count": len(payment_rows),
            "credit_balance": str(credit_balance),
        },
    )
    return JsonResponse(
        {
            "tenant": profile,
            "summary": {
                "balance_due": str(current_balance),
                "credit_balance": str(credit_balance),
                "net_balance_due": str(net_balance_due),
                "next_due_date": (
                    min(outstanding_due_dates).isoformat()
                    if outstanding_due_dates
                    else None
                ),
            },
            "invoices": invoice_rows,
            "payments": payment_rows,
            "deposit": (
                {
                    "balance": str(deposit_account.balance),
                    "target_amount": str(deposit_account.target_amount),
                    "entries": [
                        {
                            "id": entry.pk,
                            "entry_type": entry.entry_type,
                            "amount": str(entry.amount),
                            "reference": entry.reference,
                            "description": entry.description,
                            "created_at": entry.created_at.isoformat(),
                        }
                        for entry in deposit_account.entries.all()
                    ],
                }
                if deposit_account
                else None
            ),
            "move_out_settlements": [
                {
                    "id": settlement.pk,
                    "property_name": settlement.tenancy.unit.property.name,
                    "unit_code": settlement.tenancy.unit.code,
                    "paybill_number": _property_paybill_number(
                        settlement.tenancy.unit.property
                    ),
                    "actual_move_out_date": settlement.actual_move_out_date.isoformat(),
                    "notice_status": settlement.notice.status,
                    "inspection_completed_at": (
                        settlement.exit_inspection.completed_at.isoformat()
                        if settlement.exit_inspection.completed_at
                        else None
                    ),
                    "settlement_reviewed_at": (
                        settlement.reviewed_at.isoformat()
                        if settlement.reviewed_at
                        else None
                    ),
                    "refund_amount": str(settlement.refund_amount),
                    "deposit_balance": str(settlement.deposit_balance),
                    "arrears_amount": str(settlement.arrears_amount),
                    "utility_amount": str(settlement.utility_amount),
                    "damages_amount": str(settlement.damages_amount),
                    "deduction_from_deposit": str(settlement.deduction_from_deposit),
                    "shortfall_amount": str(settlement.shortfall_amount),
                    "final_readings": settlement.final_readings,
                    "damage_items": settlement.damage_items,
                    "maintenance_required": settlement.maintenance_required,
                    "inspection_photos": [
                        {
                            "index": index,
                            "caption": (
                                photo.get("caption")
                                or photo.get("name")
                                or f"Exit inspection photo {index + 1}"
                            ),
                        }
                        for index, photo in enumerate(settlement.exit_inspection.photos)
                    ],
                    "status": settlement.status,
                    "payout_reference": settlement.payout_reference,
                    "payout_date": settlement.payout_date.isoformat() if settlement.payout_date else None,
                    "rejection_reason": settlement.rejection_reason,
                }
                for settlement in move_out_settlements
            ],
        }
    )


@require_GET
def tenant_move_out_inspection_photo(request, settlement_id, photo_index):
    tenant, error = _move_out_tenant(request)
    if error:
        return error
    settlement = (
        MoveOutSettlement.objects.filter(
            pk=settlement_id,
            tenancy__tenant=tenant,
        )
        .select_related("exit_inspection")
        .first()
    )
    if settlement is None:
        return _problem(
            404,
            "Inspection photo not found",
            "The move-out inspection photo is unavailable.",
            "move-out-inspection-photo-not-found",
        )
    photos = settlement.exit_inspection.photos
    if not isinstance(photos, list) or not 0 <= photo_index < len(photos):
        return _problem(
            404,
            "Inspection photo not found",
            "The move-out inspection photo is unavailable.",
            "move-out-inspection-photo-not-found",
        )
    photo = photos[photo_index]
    storage_key = photo.get("storage_key") if isinstance(photo, dict) else None
    if not isinstance(storage_key, str) or not storage_key.strip():
        return _problem(
            404,
            "Inspection photo not found",
            "The move-out inspection photo is unavailable.",
            "move-out-inspection-photo-not-found",
        )
    try:
        content = read_private_object(storage_key)
    except (BotoCoreError, ClientError, ImproperlyConfigured, OSError):
        logger.exception("Could not retrieve move-out inspection photo.")
        return _problem(
            503,
            "Inspection evidence unavailable",
            "The inspection photo could not be retrieved.",
            "inspection-evidence-unavailable",
        )
    content_type = (
        photo.get("content_type")
        if isinstance(photo.get("content_type"), str)
        else "application/octet-stream"
    )
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        content_type = "application/octet-stream"
    response = HttpResponse(content, content_type=content_type)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
def tenant_move_out_settlement_statement(request, settlement_id):
    tenant, error = _move_out_tenant(request)
    if error:
        return error
    settlement = (
        MoveOutSettlement.objects.filter(
            pk=settlement_id,
            tenancy__tenant=tenant,
        )
        .select_related(
            "tenancy__tenant",
            "tenancy__unit__property",
            "exit_inspection__closing_utility_invoice",
        )
        .first()
    )
    if settlement is None:
        return _problem(
            404,
            "Settlement statement not found",
            "The move-out settlement statement is unavailable.",
            "move-out-statement-not-found",
        )
    response = HttpResponse(
        _move_out_settlement_pdf(settlement),
        content_type="application/pdf",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="move-out-settlement-{settlement.pk}.pdf"'
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


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


@csrf_exempt
@require_POST
def tenant_rent_stk_payment(request):
    if not request.user.is_authenticated:
        return _problem(
            401,
            "Authentication required",
            "Sign in first.",
            "authentication-required",
        )
    if not _has_role(request.user, TENANT_PORTAL_ROLE):
        return _problem(
            403,
            "Permission denied",
            "Tenant portal access is required.",
            "tenant-portal-access-denied",
        )

    tenant = Tenant.objects.filter(portal_user=request.user).first()
    if tenant is None:
        return _problem(
            404,
            "Tenant record not found",
            "No tenant record is linked to this account.",
            "tenant-record-not-found",
        )

    body = _json_body(request)
    if body is None:
        return _problem(
            400, "Invalid request", "Expected a JSON object.", "invalid-request"
        )

    invoice_id = body.get("invoiceId")
    invoice = None
    if invoice_id:
        try:
            invoice = Invoice.objects.filter(
                pk=int(invoice_id),
                tenancy__tenant=tenant,
                status=Invoice.STATUS_ISSUED,
            ).first()
        except (TypeError, ValueError):
            return _problem(
                400, "Invalid invoice", "Invalid invoice ID.", "invalid-invoice"
            )
        if invoice is None:
            return _problem(
                404,
                "Invoice not found",
                "The invoice was not found or is not outstanding.",
                "invoice-not-found",
            )
    else:
        invoices = Invoice.objects.filter(
            tenancy__tenant=tenant,
            status=Invoice.STATUS_ISSUED,
        ).order_by("due_date", "id")
        today = timezone.localdate()
        for inv in invoices:
            paid = (
                PaymentAllocation.objects.filter(invoice=inv)
                .exclude(
                    payment__instrument_status__in=(
                        Payment.INSTRUMENT_PENDING,
                        Payment.INSTRUMENT_BOUNCED,
                    )
                )
                .aggregate(total=models.Sum("amount"))["total"]
                or Decimal("0.00")
            )
            waived = (
                ArrearsWaiver.objects.filter(
                    invoice=inv,
                    approved_at__date__lte=today,
                ).aggregate(total=models.Sum("amount"))["total"]
                or Decimal("0.00")
            )
            arrears = invoice_arrears(
                inv, today, allocated_amount=paid, waiver_amount=waived
            )
            if arrears["total_due"] > Decimal("0.00"):
                invoice = inv
                break
        if invoice is None:
            # Fall back to latest issued invoice or first invoice
            invoice = (
                Invoice.objects.filter(
                    tenancy__tenant=tenant,
                    status=Invoice.STATUS_ISSUED,
                )
                .order_by("-period_start", "-id")
                .first()
            )
        if invoice is None:
            return _problem(
                400,
                "No invoice available",
                "You have no invoices linked to this tenant account.",
                "no-invoices",
            )

    today = timezone.localdate()
    paid = (
        PaymentAllocation.objects.filter(invoice=invoice)
        .exclude(
            payment__instrument_status__in=(
                Payment.INSTRUMENT_PENDING,
                Payment.INSTRUMENT_BOUNCED,
            )
        )
        .aggregate(total=models.Sum("amount"))["total"]
        or Decimal("0.00")
    )
    waived = (
        ArrearsWaiver.objects.filter(
            invoice=invoice,
            approved_at__date__lte=today,
        ).aggregate(total=models.Sum("amount"))["total"]
        or Decimal("0.00")
    )
    amount_due = invoice_arrears(
        invoice, today, allocated_amount=paid, waiver_amount=waived
    )["total_due"]
    if amount_due <= Decimal("0.00"):
        return _problem(
            400,
            "No outstanding balance",
            "This invoice has no remaining balance to pay.",
            "invoice-already-paid",
        )

    credit_balance = available_tenant_credit(tenant)
    cash_due = max(amount_due - credit_balance, Decimal("0.00"))
    amount = body.get("amount")
    if amount is not None:
        try:
            amount = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError):
            return _problem(
                400,
                "Invalid amount",
                "A valid payment amount is required.",
                "invalid-amount",
            )
        if (
            not amount.is_finite()
            or amount < 0
            or amount != amount.to_integral_value()
            or (amount == 0 and cash_due > 0)
        ):
            return _problem(
                400,
                "Invalid amount",
                "Payment amount must be a whole number of shillings; zero is "
                "only valid when available rent credit covers the invoice.",
                "invalid-amount",
            )
    else:
        amount = cash_due.to_integral_value(rounding=ROUND_CEILING)

    amount = amount.quantize(Decimal("1"))

    if amount == 0:
        with transaction.atomic():
            invoice = lock_tenant_invoice(tenant.pk, invoice.pk)
            current_due = invoice_arrears(invoice, timezone.localdate())["total_due"]
            if available_tenant_credit(tenant) >= current_due:
                credit_applied, remaining_due = apply_tenant_credit(
                    invoice, outstanding_amount=current_due
                )
            else:
                credit_applied = Decimal("0.00")
                remaining_due = current_due
        if remaining_due > 0:
            return _problem(
                409,
                "Rent credit changed",
                "Your available credit changed. Refresh your balance and try again.",
                "credit-balance-changed",
            )
        record_audit_event(
            request,
            "payment.rent_credit_applied",
            "TenantCredit",
            new_values={
                "tenant_id": tenant.pk,
                "invoice_id": invoice.pk,
                "amount": str(credit_applied),
            },
        )
        return JsonResponse(
            {
                "success": True,
                "paymentCoveredByCredit": True,
                "invoiceNumber": invoice.number,
                "amount": "0",
                "creditApplied": str(credit_applied),
                "message": (
                    f"Your rent credit of KES {credit_applied:,.2f} covered "
                    f"invoice {invoice.number}. No M-PESA payment was needed."
                ),
            }
        )

    phone = body.get("phone")
    if not isinstance(phone, str) or not phone.strip():
        return _problem(
            400,
            "Phone number required",
            "A Safaricom phone number is required to receive the M-Pesa prompt.",
            "phone-required",
        )
    try:
        normalized_phone = daraja.normalize_ke_phone(phone)
    except ValueError as exc:
        return _problem(400, "Invalid phone number", str(exc), "invalid-phone")

    try:
        result = daraja.stk_push(
            normalized_phone,
            amount,
            invoice.number,
            "Rent Payment",
        )
    except Exception as exc:
        logger.exception("Rent STK push failed for tenant %s: %s", tenant.pk, exc)
        return _problem(
            502,
            "Payment push failed",
            f"Unable to trigger M-Pesa prompt: {exc}",
            "stk-push-failed",
        )

    RentStkRequest.objects.create(
        invoice=invoice,
        tenant=tenant,
        contact=None,
        amount=amount,
        phone=normalized_phone,
        checkout_request_id=result["checkout_request_id"],
        merchant_request_id=result.get("merchant_request_id", ""),
    )

    record_audit_event(
        request,
        "payment.rent_stk_initiated",
        "RentStkRequest",
        new_values={
            "tenant_id": tenant.pk,
            "invoice_id": invoice.pk,
            "amount": str(amount),
            "phone": normalized_phone,
            "checkout_request_id": result["checkout_request_id"],
        },
    )

    return JsonResponse(
        {
            "success": True,
            "checkoutRequestId": result["checkout_request_id"],
            "invoiceNumber": invoice.number,
            "invoiceId": invoice.pk,
            "amount": str(amount),
            "phone": normalized_phone,
            "message": f"M-Pesa payment prompt sent to {normalized_phone}. Enter your PIN on your phone to complete.",
        },
        status=201,
    )


def tenant_rent_stk_status(request, checkout_request_id):
    if not request.user.is_authenticated:
        return _problem(
            401,
            "Authentication required",
            "Sign in first.",
            "authentication-required",
        )
    tenant = Tenant.objects.filter(portal_user=request.user).first()
    if tenant is None:
        return _problem(
            404,
            "Tenant not found",
            "No tenant record is linked to this account.",
            "tenant-not-found",
        )

    stk_request = RentStkRequest.objects.filter(
        checkout_request_id=checkout_request_id,
        tenant=tenant,
    ).first()
    if stk_request is None:
        return _problem(
            404,
            "STK request not found",
            "The payment request was not found.",
            "stk-request-not-found",
        )

    return JsonResponse({
        "status": stk_request.status,
        "resultCode": stk_request.result_code,
        "resultDescription": stk_request.result_description,
        "paymentId": stk_request.payment_id,
        "amount": str(stk_request.amount),
        "processedAt": (
            stk_request.processed_at.isoformat()
            if stk_request.processed_at
            else None
        ),
    })
