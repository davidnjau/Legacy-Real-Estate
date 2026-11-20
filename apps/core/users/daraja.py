"""Minimal Safaricom Daraja client for STK push rent payments."""

import base64
import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

logger = logging.getLogger(__name__)


def normalize_ke_phone(phone: str) -> str:
    """Normalize a Kenyan phone number to 254XXXXXXXXX format."""
    if not phone or not isinstance(phone, str):
        raise ValueError("A phone number is required.")
    cleaned = re.sub(r"[\s\-\+]+", "", phone.strip())
    if cleaned.startswith("0") and len(cleaned) == 10:
        cleaned = "254" + cleaned[1:]
    elif (cleaned.startswith("7") or cleaned.startswith("1")) and len(cleaned) == 9:
        cleaned = "254" + cleaned
    elif cleaned.startswith("254") and len(cleaned) == 12:
        pass
    else:
        raise ValueError(
            f"Invalid Kenyan phone number '{phone}'. Expected format e.g. 0712345678 or 254712345678."
        )
    if not (cleaned.startswith("2547") or cleaned.startswith("2541")):
        raise ValueError(
            f"Phone number '{phone}' does not appear to be a Kenyan mobile line (must start with 07 or 01)."
        )
    return cleaned


def mpesa_is_configured() -> bool:
    has_creds = bool(
        settings.MPESA_CONSUMER_KEY
        and settings.MPESA_CONSUMER_SECRET
        and settings.MPESA_SHORTCODE
        and settings.MPESA_PASSKEY
    )
    has_callback_target = bool(
        getattr(settings, "PUBLIC_SITE_URL", "")
        or getattr(settings, "MPESA_RENT_CALLBACK_URL", "")
    )
    return has_creds and has_callback_target


def _request_json(request: Request) -> dict:
    try:
        with urlopen(request, timeout=settings.MPESA_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:300]
        except Exception:
            pass
        raise RuntimeError(f"Payment gateway rejected the request (HTTP {exc.code}): {detail}") from None
    except (URLError, TimeoutError):
        raise RuntimeError("The payment gateway could not be reached.") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RuntimeError("Payment gateway returned an unreadable response.") from None


def _access_token() -> str:
    credentials = base64.b64encode(
        f"{settings.MPESA_CONSUMER_KEY}:{settings.MPESA_CONSUMER_SECRET}".encode("utf-8")
    ).decode("ascii")
    result = _request_json(Request(
        f"{settings.MPESA_BASE_URL}/oauth/v1/generate?grant_type=client_credentials",
        headers={"Authorization": f"Basic {credentials}"},
    ))
    token = result.get("access_token")
    if not token:
        raise RuntimeError("Payment gateway did not return an access token.")
    return token


def stk_push(phone: str, amount, account_reference: str, description: str) -> dict:
    """Initiate an STK push; returns CheckoutRequestID/MerchantRequestID."""
    if not mpesa_is_configured():
        raise ImproperlyConfigured("Safaricom Daraja payment settings are not configured.")
    normalized_phone = normalize_ke_phone(phone)
    timestamp = timezone.localtime().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(
        f"{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}".encode("utf-8")
    ).decode("ascii")

    callback_url = getattr(settings, "MPESA_RENT_CALLBACK_URL", "")
    if not callback_url:
        callback_url = (
            settings.PUBLIC_SITE_URL.rstrip("/")
            + "/api/v1/payments/mpesa/rent-stk-callback"
        )

    ref = str(account_reference).strip()[:12]

    payload = json.dumps({
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": str(int(amount) if hasattr(amount, "is_finite") and amount == int(amount) else amount),
        "PartyA": normalized_phone,
        "PartyB": settings.MPESA_SHORTCODE,
        "PhoneNumber": normalized_phone,
        "CallBackURL": callback_url,
        "AccountReference": ref,
        "TransactionDesc": description[:13],
    }).encode("utf-8")

    result = _request_json(Request(
        f"{settings.MPESA_BASE_URL}/mpesa/stkpush/v1/processrequest",
        data=payload,
        headers={
            "Authorization": f"Bearer {_access_token()}",
            "Content-Type": "application/json",
        },
        method="POST",
    ))
    if str(result.get("ResponseCode", "")) != "0":
        raise RuntimeError(
            f"Payment gateway declined the STK push: {result.get('ResponseDescription', result)}"
        )
    return {
        "checkout_request_id": result.get("CheckoutRequestID", ""),
        "merchant_request_id": result.get("MerchantRequestID", ""),
    }
