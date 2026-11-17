from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from users.audit import record_audit_event
from users.models import (
    Invoice,
    Payment,
    PaymentAllocation,
    Tenancy,
    Unit,
)


def confirmed_invoice_amount(invoice):
    return PaymentAllocation.objects.filter(invoice=invoice).exclude(
        payment__instrument_status__in=(
            Payment.INSTRUMENT_PENDING,
            Payment.INSTRUMENT_BOUNCED,
        )
    ).aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))["total"]


def sync_move_in_occupancy(tenancy_id, *, as_of: date | None = None):
    transition_date = as_of or timezone.localdate()
    with transaction.atomic():
        tenancy = (
            Tenancy.objects.select_for_update()
            .filter(
                pk=tenancy_id,
                status=Tenancy.STATUS_ACTIVE,
                move_in_package__isnull=False,
                start_date__lte=transition_date,
                unit__status=Unit.STATUS_BOOKED,
            )
            .select_related("unit__property")
            .first()
        )
        if tenancy is None:
            return False
        invoice = (
            Invoice.objects.filter(
                tenancy=tenancy,
                status=Invoice.STATUS_ISSUED,
                lines__metadata__move_in_charge_id__isnull=False,
            )
            .distinct()
            .first()
        )
        if invoice is None or confirmed_invoice_amount(invoice) < invoice.total:
            return False

        unit = Unit.objects.select_for_update().get(pk=tenancy.unit_id)
        if unit.status != Unit.STATUS_BOOKED:
            return False
        unit.transition_to(Unit.STATUS_OCCUPIED)
        unit.save(update_fields=["status", "listing_enabled", "updated_at"])

    record_audit_event(
        None,
        "unit.move_in_occupied",
        "Unit",
        new_values={
            "id": unit.pk,
            "status": unit.status,
            "tenancy_id": tenancy.pk,
            "branch_id": unit.property.branch_id,
        },
        actor=None,
    )
    return True
