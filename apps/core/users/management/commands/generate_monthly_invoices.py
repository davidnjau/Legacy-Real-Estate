from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import models

from users.billing import generate_monthly_invoice
from users.models import Tenancy


class Command(BaseCommand):
    help = "Generate idempotent monthly invoices for all eligible active tenancies."

    def add_arguments(self, parser):
        parser.add_argument(
            "--period",
            required=True,
            help="First day of the billing month in YYYY-MM-DD format.",
        )

    def handle(self, *args, **options):
        try:
            period_start = date.fromisoformat(options["period"])
        except ValueError as error:
            raise CommandError("--period must be a valid YYYY-MM-DD date.") from error
        if period_start.day != 1:
            raise CommandError("--period must be the first day of a month.")

        tenancies = Tenancy.objects.filter(
            status=Tenancy.STATUS_ACTIVE,
            start_date__lte=period_start,
        ).filter(
            models.Q(end_date__isnull=True) | models.Q(end_date__gte=period_start)
        ).select_related("unit__property__branch")
        created = 0
        existing = 0
        for tenancy in tenancies.iterator():
            try:
                _, was_created = generate_monthly_invoice(tenancy, period_start)
            except ValueError as error:
                raise CommandError(str(error)) from error
            if was_created:
                created += 1
            else:
                existing += 1
        self.stdout.write(self.style.SUCCESS(f"Monthly invoices: {created} created, {existing} already existed."))
