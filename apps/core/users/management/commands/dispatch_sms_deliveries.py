import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from users.models import SMSDelivery
from users.sms import dispatch_next_sms_delivery


class Command(BaseCommand):
    help = "Send queued SMS messages through the configured AUTH_SMS_BACKEND."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument(
            "--watch",
            action="store_true",
            help="Continuously process queued SMS messages.",
        )
        parser.add_argument("--poll-interval", type=float, default=5)

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 1:
            raise CommandError("--limit must be a positive integer.")
        poll_interval = options["poll_interval"]
        if poll_interval <= 0:
            raise CommandError("--poll-interval must be positive.")
        max_attempts = getattr(settings, "AUTH_SMS_MAX_DELIVERY_ATTEMPTS", 5)
        if max_attempts < 1:
            raise CommandError("AUTH_SMS_MAX_DELIVERY_ATTEMPTS must be positive.")
        watch = options["watch"]

        while True:
            processed = 0
            failures = 0
            try:
                while processed < limit:
                    delivery = dispatch_next_sms_delivery(max_attempts=max_attempts)
                    if delivery is None:
                        break
                    processed += 1
                    if delivery.status in {
                        SMSDelivery.STATUS_QUEUED,
                        SMSDelivery.STATUS_FAILED,
                    }:
                        failures += 1
            except Exception as error:
                if not watch:
                    raise
                self.stderr.write(
                    self.style.ERROR(
                        f"SMS worker cycle failed ({type(error).__name__}); retrying."
                    )
                )
                time.sleep(poll_interval)
                continue

            if processed:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Processed {processed} SMS delivery(s); {failures} attempt(s) failed."
                    )
                )
            if failures and not watch:
                raise CommandError(
                    f"{failures} SMS delivery attempt(s) failed; see delivery status and error codes."
                )
            if not watch:
                return
            if failures:
                self.stderr.write(
                    self.style.WARNING(
                        f"{failures} SMS delivery attempt(s) failed; retrying queued messages."
                    )
                )
            time.sleep(poll_interval)
