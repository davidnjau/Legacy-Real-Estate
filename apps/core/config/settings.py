import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("DJANGO_DEBUG", "0").lower() in {"1", "true", "yes"}
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DEBUG is disabled.")
    SECRET_KEY = "unsafe-local-development-key"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

AUTHENTICATION_BACKENDS = ["users.authentication.EmailOrPhoneBackend"]
AUTH_SMS_BACKEND = os.environ.get(
    "AUTH_SMS_BACKEND",
    "users.sms.ConsoleSMSBackend" if DEBUG else "",
)
SOFTLINK_SMS_API_URL = os.environ.get(
    "SOFTLINK_SMS_API_URL",
    "https://api.softlinksms.co.ke/api/v1/sms",
)
SOFTLINK_SMS_API_KEY = os.environ.get("SOFTLINK_SMS_API_KEY", "")
SOFTLINK_SMS_TRAFFIC_CLASS = os.environ.get(
    "SOFTLINK_SMS_TRAFFIC_CLASS",
    "promotional",
)
SOFTLINK_SMS_TIMEOUT_SECONDS = int(os.environ.get("SOFTLINK_SMS_TIMEOUT_SECONDS", "10"))
AUTH_SMS_CODE_LIFETIME_SECONDS = int(os.environ.get("AUTH_SMS_CODE_LIFETIME_SECONDS", "300"))
AUTH_SMS_MAX_ATTEMPTS = int(os.environ.get("AUTH_SMS_MAX_ATTEMPTS", "5"))
AUTH_SMS_RESEND_COOLDOWN_SECONDS = int(
    os.environ.get("AUTH_SMS_RESEND_COOLDOWN_SECONDS", "60")
)
if AUTH_SMS_RESEND_COOLDOWN_SECONDS <= 0:
    raise ImproperlyConfigured(
        "AUTH_SMS_RESEND_COOLDOWN_SECONDS must be a positive integer."
    )
AUTH_MFA_CHALLENGE_TTL_SECONDS = int(os.environ.get("AUTH_MFA_CHALLENGE_TTL_SECONDS", "300"))
AUTH_MFA_MAX_ATTEMPTS = int(os.environ.get("AUTH_MFA_MAX_ATTEMPTS", "5"))
AUTH_DIRECTOR_ACTIVATION_TTL_SECONDS = int(
    os.environ.get("AUTH_DIRECTOR_ACTIVATION_TTL_SECONDS", "900")
)
AUTH_SMS_MAX_DELIVERY_ATTEMPTS = int(
    os.environ.get("AUTH_SMS_MAX_DELIVERY_ATTEMPTS", "5")
)
WHATSAPP_BACKEND = os.environ.get(
    "WHATSAPP_BACKEND",
    "users.whatsapp.ConsoleWhatsAppBackend" if DEBUG else "",
)
WHATSAPP_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_WABA_ID = os.environ.get("WHATSAPP_WABA_ID", "")
WHATSAPP_APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")
WHATSAPP_WEBHOOK_TOKEN = os.environ.get("WHATSAPP_WEBHOOK_TOKEN", "")
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_ACTIVATION_TEMPLATE = os.environ.get("WHATSAPP_ACTIVATION_TEMPLATE", "")
WHATSAPP_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v21.0")
WHATSAPP_LANGUAGE_CODE = os.environ.get("WHATSAPP_LANGUAGE_CODE", "en")
WHATSAPP_TIMEOUT_SECONDS = int(os.environ.get("WHATSAPP_TIMEOUT_SECONDS", "10"))
WHATSAPP_MAX_DELIVERY_ATTEMPTS = int(
    os.environ.get("WHATSAPP_MAX_DELIVERY_ATTEMPTS", "5")
)
NCBA_ENVIRONMENT = os.environ.get("NCBA_ENVIRONMENT", "sandbox").strip().lower()
NCBA_USERNAME = os.environ.get("NCBA_USERNAME", "").strip()
NCBA_PASSWORD = os.environ.get("NCBA_PASSWORD", "").strip()
NCBA_SECRET_KEY = os.environ.get("NCBA_SECRET_KEY", "").strip()
NCBA_PAYBILL_NUMBER = (
    os.environ.get("NCBA_PAYBILL_NUMBER")
    or os.environ.get("NCBA_PAYBILL")
    or os.environ.get("PAYBILL_NUMBER")
    or os.environ.get("MPESA_SHORTCODE")
    or ""
).strip()
NCBA_ACCOUNT_NUMBER = (
    os.environ.get("NCBA_ACCOUNT_NUMBER")
    or os.environ.get("NCBA_ACCOUNT")
    or os.environ.get("ACCOUNT_NUMBER")
    or ""
).strip()
NCBA_BASE_URL = os.environ.get(
    "NCBA_BASE_URL",
    "https://api.ncbagroup.com" if NCBA_ENVIRONMENT == "production" else "https://uat.ncbagroup.com",
).strip()

MPESA_CONSUMER_KEY = os.environ.get("MPESA_CONSUMER_KEY", "").strip()
MPESA_CONSUMER_SECRET = os.environ.get("MPESA_CONSUMER_SECRET", "").strip()
MPESA_SHORTCODE = os.environ.get("MPESA_SHORTCODE", "").strip()
MPESA_PASSKEY = os.environ.get("MPESA_PASSKEY", "").strip()
MPESA_BASE_URL = os.environ.get(
    "MPESA_BASE_URL",
    "https://sandbox.safaricom.co.ke",
).strip()
MPESA_TIMEOUT_SECONDS = int(os.environ.get("MPESA_TIMEOUT_SECONDS", "30"))
MPESA_RENT_CALLBACK_URL = os.environ.get("MPESA_RENT_CALLBACK_URL", "").strip()
AUTH_LOGIN_MAX_ATTEMPTS = int(os.environ.get("AUTH_LOGIN_MAX_ATTEMPTS", "5"))
AUTH_LOGIN_LOCKOUT_SECONDS = int(os.environ.get("AUTH_LOGIN_LOCKOUT_SECONDS", "900"))
SESSION_COOKIE_AGE = int(os.environ.get("AUTH_SESSION_AGE_SECONDS", "28800"))
if SESSION_COOKIE_AGE <= 0:
    raise ImproperlyConfigured("AUTH_SESSION_AGE_SECONDS must be a positive integer.")
AUTH_TENANT_SESSION_AGE_SECONDS = int(
    os.environ.get("AUTH_TENANT_SESSION_AGE_SECONDS", "43200")
)
if AUTH_TENANT_SESSION_AGE_SECONDS <= 0:
    raise ImproperlyConfigured(
        "AUTH_TENANT_SESSION_AGE_SECONDS must be a positive integer."
    )

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:3001",
    ).split(",")
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

INSTALLED_APPS = [
    "corsheaders",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "users.apps.UsersConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "users.middleware.ResidentialSessionAccessMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "legacy_core"),
        "USER": os.environ.get("POSTGRES_USER", "legacy"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "legacy_local_only"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

OBJECT_STORAGE_ENDPOINT_URL = os.environ.get(
    "OBJECT_STORAGE_ENDPOINT_URL",
    "http://localhost:9000" if DEBUG else "",
)
OBJECT_STORAGE_ACCESS_KEY = os.environ.get("OBJECT_STORAGE_ACCESS_KEY", "")
OBJECT_STORAGE_SECRET_KEY = os.environ.get("OBJECT_STORAGE_SECRET_KEY", "")
OBJECT_STORAGE_BUCKET = os.environ.get("OBJECT_STORAGE_BUCKET", "legacy-private")
OBJECT_STORAGE_REGION = os.environ.get("OBJECT_STORAGE_REGION", "us-east-1")
OBJECT_STORAGE_AUTO_CREATE_BUCKET = os.environ.get(
    "OBJECT_STORAGE_AUTO_CREATE_BUCKET",
    "1" if DEBUG else "0",
).lower() in {"1", "true", "yes"}
PUBLIC_SITE_URL = os.environ.get(
    "PUBLIC_SITE_URL",
    "http://localhost:3000" if DEBUG else "",
).rstrip("/")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
BREVO_SENDER_EMAIL = os.environ.get("BREVO_SENDER_EMAIL", "")
BREVO_SENDER_NAME = os.environ.get("BREVO_SENDER_NAME", "Property Management")
SOFTLINK_EMAIL_API_URL = os.environ.get(
    "SOFTLINK_EMAIL_API_URL",
    "https://api.softlinksms.co.ke/api/v1/email",
)
SOFTLINK_EMAIL_API_KEY = os.environ.get("SOFTLINK_EMAIL_API_KEY", "")
SOFTLINK_EMAIL_TIMEOUT_SECONDS = int(
    os.environ.get("SOFTLINK_EMAIL_TIMEOUT_SECONDS", "10")
)
EMAIL_DELIVERY_BACKEND = os.environ.get(
    "EMAIL_DELIVERY_BACKEND",
    "mailpit" if DEBUG and not BREVO_API_KEY else "brevo",
).strip().lower()
if EMAIL_DELIVERY_BACKEND not in {"brevo", "mailpit", "softlink"}:
    raise ImproperlyConfigured(
        "EMAIL_DELIVERY_BACKEND must be 'brevo', 'mailpit', or 'softlink'."
    )
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "webmaster@localhost")
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "mailpit" if DEBUG else "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "1025" if DEBUG else "25"))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "0").lower() in {"1", "true", "yes"}
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

LANGUAGE_CODE = "en"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True
CELERY_BROKER_URL = os.environ.get(
    "CELERY_BROKER_URL",
    "amqp://guest:guest@rabbitmq:5672//",
)
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_BEAT_SCHEDULE = {
    "run-monthly-billing-cycle": {
        "task": "users.tasks.run_monthly_billing_cycle",
        "schedule": 60.0,
    },
}
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
