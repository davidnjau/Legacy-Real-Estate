from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from users.arrears import invoice_arrears
from users.models import (
    AuditLog,
    ArrearsWaiver,
    Invoice,
    Payment,
    PaymentAllocation,
    Tenant,
    TenantCredit,
)


ZERO = Decimal("0.00")
UNCONFIRMED_INSTRUMENTS = (
    Payment.INSTRUMENT_PENDING,
    Payment.INSTRUMENT_BOUNCED,
)


def available_tenant_credit(tenant):
    total = (
        TenantCredit.objects.filter(tenant=tenant, remaining_amount__gt=ZERO)
        .exclude(payment__instrument_status__in=UNCONFIRMED_INSTRUMENTS)
        .aggregate(total=Sum("remaining_amount"))["total"]
        or ZERO
    )
    return total.quantize(Decimal("0.01"))


def invoice_outstanding_amount(invoice):
    today = timezone.localdate()
    allocated = (
        PaymentAllocation.objects.filter(invoice=invoice)
        .filter(
            created_at__date__lte=today,
            payment__received_at__date__lte=today,
        )
        .exclude(payment__instrument_status__in=UNCONFIRMED_INSTRUMENTS)
        .aggregate(total=Sum("amount"))["total"]
        or ZERO
    )
    waived = (
        ArrearsWaiver.objects.filter(
            invoice=invoice,
            approved_at__date__lte=today,
        ).aggregate(total=Sum("amount"))["total"]
        or ZERO
    )
    return invoice_arrears(
        invoice,
        today,
        allocated_amount=allocated,
        waiver_amount=waived,
    )["total_due"]


def apply_tenant_credit(invoice, outstanding_amount=None):
    remaining_due = (
        invoice_outstanding_amount(invoice)
        if outstanding_amount is None
        else max(outstanding_amount, ZERO)
    )
    credit_applied = ZERO
    credits = (
        TenantCredit.objects.select_for_update()
        .filter(
            tenant_id=invoice.tenancy.tenant_id,
            remaining_amount__gt=ZERO,
        )
        .exclude(payment__instrument_status__in=UNCONFIRMED_INSTRUMENTS)
        .select_related("payment")
        .order_by("created_at", "id")
    )
    for credit in credits:
        if remaining_due <= ZERO:
            break
        amount = min(credit.remaining_amount, remaining_due)
        PaymentAllocation.objects.create(
            payment=credit.payment,
            invoice=invoice,
            amount=amount,
        )
        credit.remaining_amount -= amount
        credit.save(update_fields=["remaining_amount"])
        credit.payment.allocated_amount += amount
        credit.payment.credit_amount -= amount
        credit.payment.status = (
            Payment.STATUS_PARTIALLY_ALLOCATED
            if credit.remaining_amount
            else Payment.STATUS_ALLOCATED
        )
        credit.payment.save(
            update_fields=["allocated_amount", "credit_amount", "status"]
        )
        credit_applied += amount
        remaining_due -= amount
        AuditLog.objects.create(
            user=None,
            action="payment.tenant_credit_applied",
            entity="TenantCredit",
            new_values={
                "tenant_id": invoice.tenancy.tenant_id,
                "payment_id": credit.payment_id,
                "invoice_id": invoice.pk,
                "amount": str(amount),
            },
        )
    return credit_applied, remaining_due


def lock_tenant_invoice(tenant_id, invoice_id):
    Tenant.objects.select_for_update().get(pk=tenant_id)
    return (
        Invoice.objects.select_for_update()
        .select_related("tenancy")
        .get(pk=invoice_id, tenancy__tenant_id=tenant_id)
    )
