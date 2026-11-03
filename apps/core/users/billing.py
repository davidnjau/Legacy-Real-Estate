import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from django.db import IntegrityError, models, transaction

from users.audit import record_audit_event
from users.models import (
    Invoice,
    InvoiceLine,
    InvoiceNumberSequence,
    MeterReading,
    UnitCharge,
    UtilityConfiguration,
)


def month_end(period_start):
    return date(
        period_start.year,
        period_start.month,
        calendar.monthrange(period_start.year, period_start.month)[1],
    )


def _effective_charges(unit, period_start):
    return list(
        unit.charges.filter(effective_from__lte=period_start)
        .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=period_start))
        .filter(charge_type__in={UnitCharge.CHARGE_RENT, UnitCharge.CHARGE_RECURRING})
        .order_by("charge_type", "name", "id")
    )


def _water_line(unit, period_start):
    utility = (
        unit.utilities.filter(
            utility_type=UtilityConfiguration.UTILITY_WATER,
            billing_method=UtilityConfiguration.METHOD_METERED,
            effective_from__lte=period_start,
        )
        .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=period_start))
        .order_by("-effective_from", "-id")
        .first()
    )
    if utility is None:
        return None
    meter = utility.metadata.get("meter") or unit.meters.get("water")
    if not isinstance(meter, str) or not meter.strip():
        raise ValueError(f"Unit {unit.pk} has no configured water meter identifier.")
    reading = MeterReading.objects.filter(unit=unit, meter=meter.strip(), period=period_start).first()
    if reading is None:
        raise ValueError(f"Unit {unit.pk} has no water reading for {period_start.isoformat()}.")
    usage = reading.current_value - reading.previous_value
    amount = max(usage * utility.rate_per_unit, utility.minimum_charge).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return InvoiceLine(
        charge_type="utility",
        description=utility.name,
        amount=amount,
        metadata={
            "utility_id": utility.pk,
            "meter_reading_id": reading.pk,
            "meter": meter.strip(),
            "usage": str(usage),
            "rate_per_unit": str(utility.rate_per_unit),
            "minimum_charge": str(utility.minimum_charge),
        },
    )


def _carried_balance(tenancy, period_start):
    previous_lines = InvoiceLine.objects.filter(
        invoice__tenancy=tenancy,
        invoice__status=Invoice.STATUS_ISSUED,
        invoice__period_end__lt=period_start,
    ).exclude(charge_type="balance")
    amount = sum((line.amount for line in previous_lines), Decimal("0.00"))
    if amount <= 0:
        return None
    return InvoiceLine(
        charge_type="balance",
        description="Carried outstanding balance",
        amount=amount,
        metadata={"through": period_start.isoformat()},
    )


def generate_monthly_invoice(tenancy, period_start):
    period_end = month_end(period_start)
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

    lines = [
        InvoiceLine(
            charge_type=charge.charge_type,
            description=charge.name,
            amount=charge.amount,
            metadata={"unit_charge_id": charge.pk},
        )
        for charge in _effective_charges(tenancy.unit, period_start)
    ]
    water_line = _water_line(tenancy.unit, period_start)
    if water_line:
        lines.append(water_line)
    balance_line = _carried_balance(tenancy, period_start)
    if balance_line:
        lines.append(balance_line)
    if not lines:
        raise ValueError(f"Tenancy {tenancy.pk} has no billable charges for {period_start.isoformat()}.")

    total = sum((line.amount for line in lines), Decimal("0.00"))
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
            for line in lines:
                line.invoice = invoice
            InvoiceLine.objects.bulk_create(lines)
    except IntegrityError:
        existing = Invoice.objects.filter(
            tenancy=tenancy,
            period_start=period_start,
            period_end=period_end,
        ).first()
        if existing:
            return existing, False
        raise
    request = SimpleNamespace(META={}, user=None)
    record_audit_event(request, "invoice.issued", "Invoice", new_values={"invoice_id": invoice.pk})
    return invoice, True
