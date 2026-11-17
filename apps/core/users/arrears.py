from decimal import Decimal

from django.db.models import Sum

from users.models import Invoice, Payment, PaymentAllocation


def ageing_bucket(days_overdue):
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "1-30"
    if days_overdue <= 60:
        return "31-60"
    if days_overdue <= 90:
        return "61-90"
    return "90+"


def invoice_arrears(invoice, as_of, *, allocated_amount=None, waiver_amount=None):
    if allocated_amount is None:
        allocated_amount = (
            PaymentAllocation.objects.filter(
                invoice=invoice,
                created_at__date__lte=as_of,
                payment__received_at__date__lte=as_of,
            )
            .exclude(
                payment__instrument_status__in=(
                    Payment.INSTRUMENT_PENDING,
                    Payment.INSTRUMENT_BOUNCED,
                )
            )
            .aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
    if waiver_amount is None:
        waiver_amount = (
            invoice.arrears_waivers.filter(approved_at__date__lte=as_of)
            .aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
    outstanding = max(invoice.total - allocated_amount - waiver_amount, Decimal("0.00"))
    days_overdue = max((as_of - invoice.due_date).days, 0)
    policy = getattr(invoice.branch, "arrears_policy", None)
    penalty = Decimal("0.00")
    if policy and days_overdue > policy.grace_days:
        if policy.penalty_type == "flat":
            penalty = policy.penalty_flat_fee
        else:
            penalty = (outstanding * policy.penalty_rate / Decimal(100)).quantize(Decimal("0.01"))
    return {
        "invoice": invoice,
        "outstanding": outstanding,
        "days_overdue": days_overdue,
        "bucket": ageing_bucket(days_overdue),
        "penalty": penalty,
        "total_due": outstanding + penalty,
        "reminder_due": bool(policy and days_overdue >= policy.reminder_after_days),
    }


def arrears_for_accessible_invoices(invoices, as_of):
    return [
        invoice_arrears(invoice, as_of)
        for invoice in invoices.select_related("branch", "tenancy__tenant", "tenancy__unit")
        .prefetch_related("arrears_waivers")
        .filter(status=Invoice.STATUS_ISSUED)
    ]
