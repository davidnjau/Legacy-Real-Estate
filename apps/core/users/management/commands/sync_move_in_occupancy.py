from django.core.management.base import BaseCommand
from django.utils import timezone

from users.models import Tenancy, Unit
from users.move_in import sync_move_in_occupancy


class Command(BaseCommand):
    help = "Mark fully paid move-in units occupied once their tenancy start date arrives."

    def handle(self, *args, **options):
        tenancy_ids = Tenancy.objects.filter(
            status=Tenancy.STATUS_ACTIVE,
            start_date__lte=timezone.localdate(),
            unit__status=Unit.STATUS_BOOKED,
            move_in_package__isnull=False,
        ).order_by("pk").values_list("pk", flat=True)
        transitioned = sum(
            sync_move_in_occupancy(tenancy_id) for tenancy_id in tenancy_ids
        )
        self.stdout.write(
            self.style.SUCCESS(f"Marked {transitioned} paid move-in unit(s) occupied.")
        )
