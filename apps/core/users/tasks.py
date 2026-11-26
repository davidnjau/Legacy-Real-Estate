import calendar
from datetime import date, time, timedelta

from celery import shared_task
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from users.audit import record_audit_event
from users.billing import generate_monthly_invoice, water_reading_status
from users.models import (
    BillingNotification,
    Invoice,
    Property,
    PropertyCaretakerAssignment,
    MeterReading,
    Tenancy,
    UserProfile,
    UtilityConfiguration,
)
from users.roles import SUPER_ADMIN_ROLE
from users.sms import queue_template_sms
from users.water_readings import caretaker_water_readings_open_date


PROPERTY_OFFICER_ROLE = "Property Officer / Agent"


def _next_month(period_start):
    return (period_start.replace(day=28) + timedelta(days=4)).replace(day=1)


def _property_requires_metered_water(property_obj, period_start):
    return UtilityConfiguration.objects.filter(
        unit__property=property_obj,
        utility_type=UtilityConfiguration.UTILITY_WATER,
        billing_method=UtilityConfiguration.METHOD_METERED,
        effective_from__lte=period_start,
    ).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=period_start)
    ).exists()


def _queue_property_notification(property_obj, recipient, period_start, notification_type, template, context):
    if not getattr(getattr(recipient, "profile", None), "phone", ""):
        return False
    with transaction.atomic():
        try:
            with transaction.atomic():
                BillingNotification.objects.create(
                    property=property_obj,
                    recipient=recipient,
                    period_start=period_start,
                    notification_type=notification_type,
                )
        except IntegrityError:
            return False
        queue_template_sms(recipient.profile.phone, template, context)
    return True


def _notify_caretakers(property_obj, period_start):
    recipients = set(
        property_obj.caretakers.filter(is_active=True).values_list("pk", flat=True)
    )
    recipients.update(
        PropertyCaretakerAssignment.objects.filter(
            property=property_obj,
            ended_at__isnull=True,
            caretaker__is_active=True,
        ).values_list("caretaker_id", flat=True)
    )
    users = UserProfile.objects.filter(user_id__in=recipients).select_related("user")
    context = {
        "property_name": property_obj.name,
        "billing_month": period_start.strftime("%B %Y"),
    }
    return sum(
        _queue_property_notification(
            property_obj,
            profile.user,
            period_start,
            BillingNotification.TYPE_CARETAKER_READINGS,
            "caretaker_water_readings",
            context,
        )
        for profile in users
    )


def _property_officers(property_obj):
    branch = property_obj.branch
    return UserProfile.objects.filter(
        Q(
            agency_memberships__agency_id=branch.agency_id,
            agency_memberships__is_active=True,
            agency_memberships__branches=branch,
            agency_memberships__roles__name=PROPERTY_OFFICER_ROLE,
        )
        | Q(
            user__groups__name=PROPERTY_OFFICER_ROLE,
            branches=branch,
        )
    ).filter(user__is_active=True).select_related("user").distinct()


def _notify_property_officers(property_obj, period_start, invoice_count):
    context = {
        "property_name": property_obj.name,
        "billing_month": period_start.strftime("%B %Y"),
        "invoice_count": invoice_count,
    }
    return sum(
        _queue_property_notification(
            property_obj,
            profile.user,
            period_start,
            BillingNotification.TYPE_AGENT_DRAFTS,
            "agent_billing_drafts",
            context,
        )
        for profile in _property_officers(property_obj)
    )


def queue_waiver_director_notifications(waiver):
    property_obj = waiver.unit.property
    branch = property_obj.branch
    profiles = UserProfile.objects.filter(
        Q(
            agency_memberships__agency_id=branch.agency_id,
            agency_memberships__is_active=True,
            agency_memberships__roles__name=SUPER_ADMIN_ROLE,
        )
        | Q(user__groups__name=SUPER_ADMIN_ROLE)
        | Q(user__is_superuser=True)
    ).filter(user__is_active=True).select_related("user").distinct()
    notified = 0
    context = {
        "property_name": property_obj.name,
        "unit_code": waiver.unit.code,
        "billing_month": waiver.period_start.strftime("%B %Y"),
        "amount": str(waiver.amount),
        "reason": waiver.reason.strip()[:120],
    }
    for profile in profiles:
        if profile.phone:
            queue_template_sms(profile.phone, "director_invoice_waiver", context)
            notified += 1
    if not notified:
        raise ValueError("No active Director with a phone number can be notified about this waiver.")
    waiver.director_notified_at = timezone.now()
    waiver.save(update_fields=["director_notified_at"])
    return notified


def _active_tenancies(property_obj, period_start):
    return Tenancy.objects.filter(
        unit__property=property_obj,
        status=Tenancy.STATUS_ACTIVE,
        start_date__lte=period_start,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=period_start)
    ).select_related("unit__property__branch", "tenant").order_by("unit__code", "id")


def _draft_property_invoices(property_obj, period_start):
    created = 0
    held = []
    for tenancy in _active_tenancies(property_obj, period_start):
        requires_reading, reading_note = water_reading_status(tenancy.unit, period_start)
        if requires_reading and reading_note:
            held.append({"unit_code": tenancy.unit.code, "reason": reading_note})
            continue
        try:
            invoice, was_created = generate_monthly_invoice(
                tenancy,
                period_start,
                status=Invoice.STATUS_DRAFT,
            )
        except ValueError as exc:
            held.append({"unit_code": tenancy.unit.code, "reason": str(exc)})
            continue
        if invoice.status == Invoice.STATUS_DRAFT and was_created:
            created += 1

    draft_count = Invoice.objects.filter(
        tenancy__unit__property=property_obj,
        period_start=period_start,
        status=Invoice.STATUS_DRAFT,
    ).count()
    if draft_count:
        _notify_property_officers(property_obj, period_start, draft_count)
    return {"created": created, "drafts": draft_count, "held": held}


@shared_task(name="users.tasks.draft_property_invoices")
def draft_property_invoices(property_id, period_start):
    if isinstance(period_start, str):
        period_start = date.fromisoformat(period_start)
    property_obj = Property.objects.select_related("branch").get(pk=property_id)
    result = _draft_property_invoices(property_obj, period_start)
    if result["created"]:
        record_audit_event(
            type("TaskRequest", (), {"META": {}, "user": None})(),
            "billing_cycle.drafts_prepared",
            "BillingCycle",
            new_values={
                "property_id": property_obj.pk,
                "period_start": period_start.isoformat(),
                "created": result["created"],
            },
        )
    return result


def _publish_due_drafts(period_start):
    published = 0
    held = []
    invoice_ids = Invoice.objects.filter(
        status=Invoice.STATUS_DRAFT,
        period_start__lte=period_start,
    ).order_by("period_start", "id").values_list("pk", flat=True)
    for invoice_id in invoice_ids.iterator():
        with transaction.atomic():
            invoice = (
                Invoice.objects.select_for_update()
                .filter(pk=invoice_id, status=Invoice.STATUS_DRAFT)
                .select_related("tenancy__unit")
                .first()
            )
            if invoice is None:
                continue
            requires_reading, reading_note = water_reading_status(
                invoice.tenancy.unit,
                invoice.period_start,
            )
            if requires_reading and reading_note:
                held.append(
                    {"invoice_id": invoice.pk, "unit_code": invoice.tenancy.unit.code, "reason": reading_note}
                )
                continue
            invoice.status = Invoice.STATUS_ISSUED
            invoice.save(update_fields=["status", "issued_at", "updated_at"])
        published += 1
        record_audit_event(
            type("TaskRequest", (), {"META": {}, "user": None})(),
            "invoice.issued",
            "Invoice",
            new_values={
                "invoice_id": invoice.pk,
                "period_start": invoice.period_start.isoformat(),
                "total": str(invoice.total),
                "source": "scheduled_billing_cycle",
            },
        )
    return published, held


@shared_task(name="users.tasks.run_monthly_billing_cycle")
def run_monthly_billing_cycle():
    today = timezone.localdate()
    current_period = today.replace(day=1)
    next_period = _next_month(current_period)
    billing_day = min(30, calendar.monthrange(today.year, today.month)[1])
    reading_open_day = caretaker_water_readings_open_date(today).day
    summary = {"caretakers_notified": 0, "drafts_created": 0, "drafts_published": 0, "held": []}

    if timezone.localtime().time() >= time(9, 0):
        if today.day == billing_day:
            properties = Property.objects.filter(
                branch__agency__is_active=True
            ).select_related("branch")
            for property_obj in properties.iterator():
                if _property_requires_metered_water(property_obj, next_period):
                    continue
                result = _draft_property_invoices(property_obj, next_period)
                summary["drafts_created"] += result["created"]
                summary["held"].extend(result["held"])

        if today.day == reading_open_day:
            properties = Property.objects.filter(
                branch__agency__is_active=True
            ).select_related("branch")
            for property_obj in properties.iterator():
                if not _property_requires_metered_water(property_obj, next_period):
                    continue
                summary["caretakers_notified"] += _notify_caretakers(
                    property_obj,
                    next_period,
                )

    # Retry metered-water drafting on each scheduler tick after publication, so a
    # transient broker outage cannot strand a caretaker's published readings.
    published_periods = (
        MeterReading.objects.filter(
            period__gte=current_period,
            period__lte=next_period,
        )
        .values_list("unit__property_id", "period")
        .distinct()
    )
    for property_id, period_start in published_periods.iterator():
        property_obj = Property.objects.select_related("branch").get(pk=property_id)
        if not _property_requires_metered_water(property_obj, period_start):
            continue
        result = _draft_property_invoices(property_obj, period_start)
        summary["drafts_created"] += result["created"]
        summary["held"].extend(result["held"])

    published, publication_holds = _publish_due_drafts(current_period)
    summary["drafts_published"] = published
    summary["held"].extend(publication_holds)
    return summary
