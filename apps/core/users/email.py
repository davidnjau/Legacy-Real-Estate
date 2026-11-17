from django.conf import settings


def uses_mailpit_backend():
    return settings.EMAIL_DELIVERY_BACKEND == "mailpit"


def email_delivery_is_configured():
    if uses_mailpit_backend():
        return True
    return bool(settings.BREVO_API_KEY and settings.BREVO_SENDER_EMAIL)
