import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Point Meta's WhatsApp webhook at this deployment "
        "(/api/v1/whatsapp/webhook) using the WABA callback override."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            default="",
            help="Full callback URL. Defaults to PUBLIC_SITE_URL + /api/v1/whatsapp/webhook.",
        )
        parser.add_argument(
            "--skip-if-unconfigured",
            action="store_true",
            help="Exit quietly when the WhatsApp channel is not configured "
            "(for use in deployment pipelines).",
        )

    def handle(self, *args, **options):
        required = {
            "WHATSAPP_ACCESS_TOKEN": settings.WHATSAPP_ACCESS_TOKEN,
            "WHATSAPP_WABA_ID": settings.WHATSAPP_WABA_ID,
            "WHATSAPP_VERIFY_TOKEN": settings.WHATSAPP_VERIFY_TOKEN,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            if options["skip_if_unconfigured"]:
                self.stdout.write(
                    "WhatsApp is not configured; webhook registration skipped "
                    f"(missing {', '.join(missing)})."
                )
                return
            raise CommandError(f"Missing settings: {', '.join(missing)}.")

        callback_url = options["url"] or (
            settings.PUBLIC_SITE_URL.rstrip("/") + "/api/v1/whatsapp/webhook"
        )
        if not callback_url.startswith("https://"):
            raise CommandError("The webhook callback URL must use HTTPS.")
        if settings.WHATSAPP_WEBHOOK_TOKEN and "token=" not in callback_url:
            separator = "&" if "?" in callback_url else "?"
            callback_url += f"{separator}token={settings.WHATSAPP_WEBHOOK_TOKEN}"

        request = Request(
            "https://graph.facebook.com/"
            f"{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_WABA_ID}/subscribed_apps",
            data=json.dumps({
                "override_callback_uri": callback_url,
                "verify_token": settings.WHATSAPP_VERIFY_TOKEN,
            }).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=settings.WHATSAPP_TIMEOUT_SECONDS) as response:
                result = json.loads(response.read())
        except HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8"))["error"]["message"]
            except Exception:
                pass
            raise CommandError(
                f"Meta rejected the webhook registration (HTTP {exc.code}): {detail}"
            ) from None
        except (URLError, TimeoutError):
            raise CommandError("The WhatsApp Cloud API could not be reached.") from None

        if not result.get("success"):
            raise CommandError(f"Unexpected response from Meta: {result}")
        self.stdout.write(
            self.style.SUCCESS(f"WhatsApp webhook registered: {callback_url}")
        )
