import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet():
    if not settings.SECRET_KEY:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be configured for authenticator MFA.")
    key = hashlib.sha256(
        b"legacy-real-estate:totp-secret:v1:" + settings.SECRET_KEY.encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def generate_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def encrypt_secret(secret):
    return _fernet().encrypt(secret.encode("ascii"))


def decrypt_secret(encrypted_secret):
    return _fernet().decrypt(bytes(encrypted_secret)).decode("ascii")


def provisioning_uri(secret, account_name, issuer="Legacy Real Estate"):
    label = quote(f"{issuer}:{account_name}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        "&algorithm=SHA1&digits=6&period=30"
    )


def _code_for_counter(secret, counter):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def matching_counter(secret, code, *, timestamp=None, window=1):
    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
        return None
    current_counter = int((timestamp if timestamp is not None else time.time()) // 30)
    for offset in range(-window, window + 1):
        counter = current_counter + offset
        if counter >= 0 and hmac.compare_digest(_code_for_counter(secret, counter), code):
            return counter
    return None


def verify_code(secret, code, *, timestamp=None, window=1):
    return matching_counter(secret, code, timestamp=timestamp, window=window) is not None
