import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from django.db import IntegrityError, models, transaction

from users.audit import record_audit_event
from users.models import (
    ArrearsWaiver,
    Invoice,
    InvoiceLine,
    InvoiceNumberSequence,
    InvoiceWaiverRequest,
    MeterReading,
    MoveOutNotice,
    Payment,
    PaymentAllocation,
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


def _is_notice_month(tenancy, period_start):
    return MoveOutNotice.objects.filter(
        tenancy=tenancy,
        status=MoveOutNotice.STATUS_ACCEPTED,
        proposed_move_out_date__gte=period_start,
        proposed_move_out_date__lte=month_end(period_start),
    ).exists()


def water_reading_status(unit, period_start):
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
        return False, None
    meter = utility.metadata.get("meter") or unit.meters.get("water")
    if not isinstance(meter, str) or not meter.strip():
        return True, "Water meter identifier is not configured."
    reading_exists = MeterReading.objects.filter(
        unit=unit,
        meter=meter.strip(),
        period=period_start,
    ).exists()
    if not reading_exists:
        return True, "Published water meter reading is required."
    return True, None


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
    previous_invoices = Invoice.objects.filter(
        tenancy=tenancy,
        status=Invoice.STATUS_ISSUED,
        period_end__lt=period_start,
    )
    charges = (
        InvoiceLine.objects.filter(invoice__in=previous_invoices)
        .exclude(charge_type="balance")
        .aggregate(total=models.Sum("amount"))["total"]
        or Decimal("0.00")
    )
    allocated = (
        PaymentAllocation.objects.filter(
            invoice__in=previous_invoices,
            created_at__date__lte=period_start,
            payment__received_at__date__lte=period_start,
        )
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
            invoice__in=previous_invoices,
            approved_at__date__lte=period_start,
        ).aggregate(total=models.Sum("amount"))["total"]
        or Decimal("0.00")
    )
    amount = max(charges - allocated - waived, Decimal("0.00"))
    if amount <= 0:
        return None
    return InvoiceLine(
        charge_type="balance",
        description="Carried outstanding balance",
        amount=amount,
        metadata={"through": period_start.isoformat()},
    )


def _link_invoice_waiver_requests(invoice):
    requests = list(
        InvoiceWaiverRequest.objects.select_for_update()
        .filter(
            unit_id=invoice.tenancy.unit_id,
            period_start=invoice.period_start,
        )
        .filter(models.Q(invoice__isnull=True) | models.Q(invoice=invoice))
        .order_by("id")
    )
    existing_waivers = list(ArrearsWaiver.objects.select_for_update().filter(invoice=invoice))
    approved_total = sum(
        (waiver.amount for waiver in existing_waivers),
        Decimal("0.00"),
    )
    for waiver_request in requests:
        if waiver_request.status == InvoiceWaiverRequest.STATUS_APPROVED:
            matching_waiver = ArrearsWaiver.objects.select_for_update().filter(
                invoice_waiver_request=waiver_request,
            ).first()
            if matching_waiver is None and approved_total + waiver_request.amount > invoice.total:
                raise ValueError(
                    f"Approved waiver for unit {invoice.tenancy.unit.code} exceeds "
                    f"the {invoice.period_start:%B %Y} invoice total."
                )
            if matching_waiver is None:
                matching_waiver = ArrearsWaiver.objects.create(
                    tenancy=invoice.tenancy,
                    invoice=invoice,
                    invoice_waiver_request=waiver_request,
                    amount=waiver_request.amount,
                    reason=waiver_request.reason,
                    approved_by=waiver_request.decided_by,
                )
                existing_waivers.append(matching_waiver)
                approved_total += waiver_request.amount
        if waiver_request.invoice_id is None:
            waiver_request.invoice = invoice
            waiver_request.save(update_fields=["invoice"])


def generate_monthly_invoice(tenancy, period_start, *, status=Invoice.STATUS_ISSUED):
    if status not in {Invoice.STATUS_DRAFT, Invoice.STATUS_ISSUED}:
        raise ValueError("Monthly invoices can only be generated as drafts or issued.")
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
    notice_month = _is_notice_month(tenancy, period_start)
    if existing:
        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().get(pk=existing.pk)
            if notice_month and invoice.status == Invoice.STATUS_DRAFT:
                water_line = _water_line(tenancy.unit, period_start)
                invoice.lines.all().delete()
                if water_line is None:
                    invoice.total = Decimal("0.00")
                    invoice.status = Invoice.STATUS_VOID
                else:
                    water_line.invoice = invoice
                    water_line.save()
                    invoice.total = water_line.amount
                invoice.save(update_fields=["total", "status", "updated_at"])
            _link_invoice_waiver_requests(invoice)
        return invoice, False

    water_line = _water_line(tenancy.unit, period_start)
    if notice_month:
        lines = [water_line] if water_line else []
    else:
        lines = [
            InvoiceLine(
                charge_type=charge.charge_type,
                description=charge.name,
                amount=charge.amount,
                metadata={"unit_charge_id": charge.pk},
            )
            for charge in _effective_charges(tenancy.unit, period_start)
        ]
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
                status=status,
                total=total,
            )
            for line in lines:
                line.invoice = invoice
            InvoiceLine.objects.bulk_create(lines)
            _link_invoice_waiver_requests(invoice)
    except IntegrityError:
        existing = Invoice.objects.filter(
            tenancy=tenancy,
            period_start=period_start,
            period_end=period_end,
        ).first()
        if existing:
            with transaction.atomic():
                invoice = Invoice.objects.select_for_update().get(pk=existing.pk)
                _link_invoice_waiver_requests(invoice)
            return existing, False
        raise
    request = SimpleNamespace(META={}, user=None)
    record_audit_event(
        request,
        "invoice.drafted" if status == Invoice.STATUS_DRAFT else "invoice.issued",
        "Invoice",
        new_values={
            "invoice_id": invoice.pk,
            "status": invoice.status,
            "period_start": invoice.period_start.isoformat(),
            "total": str(invoice.total),
        },
    )
    return invoice, True
