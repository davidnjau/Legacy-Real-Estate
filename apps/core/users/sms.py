import importlib
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class ConsoleSMSBackend:
    def send(self, phone, message):
        if not settings.DEBUG:
            raise ImproperlyConfigured(
                "The console SMS backend is available only when DEBUG is enabled."
            )
        logger.warning("Development-only SMS to %s: %s", phone, message)


def send_sms(phone, message):
    backend_path = getattr(settings, "AUTH_SMS_BACKEND", "")
    if not backend_path:
        raise ImproperlyConfigured(
            "AUTH_SMS_BACKEND must be configured before SMS authentication can be used."
        )

    module_name, class_name = backend_path.rsplit(".", 1)
    backend_class = getattr(importlib.import_module(module_name), class_name)
    backend_class().send(phone, message)
