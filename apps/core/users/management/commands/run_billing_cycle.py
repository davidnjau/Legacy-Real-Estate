from django.core.management.base import BaseCommand

from users.tasks import run_monthly_billing_cycle


class Command(BaseCommand):
    help = "Run the scheduled water-reading, invoice-drafting, and invoice-publication cycle."

    def add_arguments(self, parser):
        parser.add_argument("--quiet", action="store_true")

    def handle(self, *args, **options):
        result = run_monthly_billing_cycle.run()
        if not options["quiet"] or any(
            result[key] for key in ("caretakers_notified", "drafts_created", "drafts_published")
        ):
            self.stdout.write(
                self.style.SUCCESS(
                    "Billing cycle: "
                    f"{result['caretakers_notified']} caretakers notified, "
                    f"{result['drafts_created']} drafts created, "
                    f"{result['drafts_published']} invoices published, "
                    f"{len(result['held'])} held for required data."
                )
            )
        if not options["quiet"]:
            for item in result["held"]:
                self.stdout.write(f"Held {item.get('unit_code', item.get('invoice_id'))}: {item['reason']}")
