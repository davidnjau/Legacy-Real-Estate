from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from users.arrears import invoice_arrears
from users.audit import record_audit_event
from users.models import ArrearsReminder, Invoice


def reminder_stage(due_date, as_of):
    days_from_due = (as_of - due_date).days
    if days_from_due == -3:
        return ArrearsReminder.STAGE_BEFORE_DUE
    if days_from_due == 0:
        return ArrearsReminder.STAGE_DUE_TODAY
    if days_from_due <= 0 or days_from_due % 3:
        return None
    if days_from_due <= 30:
        return ArrearsReminder.STAGE_OVERDUE_1_30
    if days_from_due <= 60:
        return ArrearsReminder.STAGE_OVERDUE_31_60
    if days_from_due <= 90:
        return ArrearsReminder.STAGE_OVERDUE_61_90
    return ArrearsReminder.STAGE_OVERDUE_90_PLUS


def reminder_message(invoice, stage, amount_due, as_of):
    tenant_name = invoice.tenancy.tenant.name
    property_name = invoice.tenancy.unit.property.name
    amount = f"KES {amount_due:,.2f}"
    due_date = invoice.due_date.strftime("%d %b %Y")
    days_overdue = max((as_of - invoice.due_date).days, 0)
    if stage == ArrearsReminder.STAGE_BEFORE_DUE:
        return (
            f"Hello {tenant_name}, rent of {amount} for {property_name} is due "
            f"on {due_date}. Please arrange payment by the due date."
        )
    if stage == ArrearsReminder.STAGE_DUE_TODAY:
        return (
            f"Hello {tenant_name}, rent of {amount} for {property_name} is due "
            "today. Please make your payment today."
        )
    if stage == ArrearsReminder.STAGE_OVERDUE_1_30:
        return (
            f"Hello {tenant_name}, rent of {amount} for {property_name} is "
            f"{days_overdue} days overdue. Please clear the balance or contact "
            "the office if you need assistance."
        )
    if stage == ArrearsReminder.STAGE_OVERDUE_31_60:
        return (
            f"Hello {tenant_name}, your rent balance of {amount} for "
            f"{property_name} remains unpaid after {days_overdue} days. Please "
            "contact the office promptly to discuss payment."
        )
    if stage == ArrearsReminder.STAGE_OVERDUE_61_90:
        return (
            f"Urgent reminder for {tenant_name}: rent of {amount} for "
            f"{property_name} is {days_overdue} days overdue. Please contact "
            "the office as soon as possible."
        )
    return (
        f"Final overdue reminder for {tenant_name}: rent of {amount} for "
        f"{property_name} is {days_overdue} days overdue. Please contact the "
        "office immediately to discuss the outstanding balance."
    )


class Command(BaseCommand):
    help = (
        "Plan deduplicated rent reminders three days before and on the due date, "
        "then every three days while a balance remains. Does not dispatch messages."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--as-of",
            help="Evaluate reminder occurrences as of YYYY-MM-DD (defaults to today).",
        )

    def handle(self, *args, **options):
        as_of_value = options["as_of"]
        if as_of_value:
            try:
                as_of = date.fromisoformat(as_of_value)
            except ValueError as error:
                raise CommandError("--as-of must be a valid YYYY-MM-DD date.") from error
            if as_of.isoformat() != as_of_value:
                raise CommandError("--as-of must use the YYYY-MM-DD format.")
        else:
            as_of = timezone.localdate()

        created = 0
        existing = 0
        cancelled = 0
        invoices = (
            Invoice.objects.filter(
                status=Invoice.STATUS_ISSUED,
                due_date__lte=as_of + timedelta(days=3),
            )
            .select_related("branch", "tenancy__tenant", "tenancy__unit__property")
            .order_by("pk")
        )
        for invoice in invoices.iterator():
            arrears = invoice_arrears(invoice, as_of)
            planned = ArrearsReminder.objects.filter(
                invoice=invoice,
                status=ArrearsReminder.STATUS_PLANNED,
            )
            if arrears["outstanding"] <= 0:
                with transaction.atomic():
                    for reminder in planned:
                        reminder.status = ArrearsReminder.STATUS_CANCELLED
                        reminder.save(update_fields=["status", "updated_at"])
                        record_audit_event(
                            None,
                            "arrears_reminder.cancelled",
                            "ArrearsReminder",
                            new_values={
                                "reminder_id": reminder.pk,
                                "invoice_id": invoice.pk,
                                "branch_id": invoice.branch_id,
                                "channel": reminder.channel,
                                "scheduled_for": reminder.scheduled_for.isoformat(),
                                "stage": reminder.stage,
                            },
                        )
                        cancelled += 1
                continue

            stage = reminder_stage(invoice.due_date, as_of)
            if stage is None:
                continue

            with transaction.atomic():
                for channel, _ in ArrearsReminder.CHANNEL_CHOICES:
                    reminder, was_created = ArrearsReminder.objects.get_or_create(
                        invoice=invoice,
                        channel=channel,
                        scheduled_for=as_of,
                        defaults={
                            "tenancy_id": invoice.tenancy_id,
                            "stage": stage,
                            "amount_due": arrears["outstanding"],
                            "message": reminder_message(
                                invoice,
                                stage,
                                arrears["outstanding"],
                                as_of,
                            ),
                        },
                    )
                    if was_created:
                        record_audit_event(
                            None,
                            "arrears_reminder.planned",
                            "ArrearsReminder",
                            new_values={
                                "reminder_id": reminder.pk,
                                "invoice_id": invoice.pk,
                                "tenancy_id": invoice.tenancy_id,
                                "branch_id": invoice.branch_id,
                                "channel": channel,
                                "scheduled_for": as_of.isoformat(),
                                "stage": stage,
                                "amount_due": str(arrears["outstanding"]),
                                "dispatched": False,
                            },
                        )
                        created += 1
                    else:
                        existing += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Arrears reminders planned without dispatch: "
                f"{created} created, {existing} already planned, "
                f"{cancelled} cancelled."
            )
        )
