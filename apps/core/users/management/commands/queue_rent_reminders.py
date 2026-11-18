"""Queue rent-balance reminders on WhatsApp and SMS for tenants in arrears.

Run manually or from a scheduler (e.g. weekly). One reminder per tenant
covering their total outstanding balance across issued invoices.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from users.models import Invoice, Payment, PaymentAllocation
from users.sms import queue_template_sms, sms_backend_is_configured
from users.whatsapp import queue_template_whatsapp_if_enabled


class Command(BaseCommand):
    help = "Queue WhatsApp/SMS rent reminders for tenants with outstanding invoices."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report who would be reminded without queueing anything.",
        )

    def handle(self, *args, **options):
        zero = Decimal("0.00")
        allocated = {
            row["invoice_id"]: row["total"] or zero
            for row in PaymentAllocation.objects.exclude(
                payment__instrument_status__in=(
                    Payment.INSTRUMENT_PENDING,
                    Payment.INSTRUMENT_BOUNCED,
                )
            )
            .values("invoice_id")
            .annotate(total=Sum("amount"))
            .values("invoice_id", "total")
        }
        outstanding_by_tenant = {}
        invoices = Invoice.objects.filter(status=Invoice.STATUS_ISSUED).select_related(
            "tenancy__tenant__branch__agency"
        )
        for invoice in invoices.iterator():
            due = invoice.total - allocated.get(invoice.pk, zero)
            if due > zero:
                tenant = invoice.tenancy.tenant
                outstanding_by_tenant.setdefault(tenant, zero)
                outstanding_by_tenant[tenant] += due

        queued = 0
        for tenant, balance in outstanding_by_tenant.items():
            if not tenant.phone:
                continue
            if options["dry_run"]:
                self.stdout.write(f"  would remind {tenant.name}: KES {balance:,.2f}")
                queued += 1
                continue
            context = {
                "amount": f"{balance:,.2f}",
                "agency_name": tenant.branch.agency.name,
            }
            whatsapp = queue_template_whatsapp_if_enabled(
                tenant.phone, "invoice_reminder", context
            )
            if sms_backend_is_configured():
                try:
                    queue_template_sms(tenant.phone, "invoice_reminder", context)
                except Exception:
                    self.stderr.write(f"  SMS reminder failed for tenant {tenant.pk}")
            if whatsapp is not None or sms_backend_is_configured():
                queued += 1

        verb = "would be reminded" if options["dry_run"] else "reminder(s) queued"
        self.stdout.write(self.style.SUCCESS(f"{queued} tenant(s) {verb}."))
