"""Minimal Safaricom Daraja client for STK push rent payments."""

import base64
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

logger = logging.getLogger(__name__)


def mpesa_is_configured() -> bool:
    return bool(
        settings.MPESA_CONSUMER_KEY
        and settings.MPESA_CONSUMER_SECRET
        and settings.MPESA_SHORTCODE
        and settings.MPESA_PASSKEY
        and settings.PUBLIC_SITE_URL
    )


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
        raise RuntimeError(f"Daraja rejected the request (HTTP {exc.code}): {detail}") from None
    except (URLError, TimeoutError):
        raise RuntimeError("The Daraja API could not be reached.") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RuntimeError("Daraja returned an unreadable response.") from None


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
        raise RuntimeError("Daraja did not return an access token.")
    return token


def stk_push(phone: str, amount, account_reference: str, description: str) -> dict:
    """Initiate an STK push; returns Daraja's CheckoutRequestID/MerchantRequestID."""
    if not mpesa_is_configured():
        raise ImproperlyConfigured("The M-PESA Daraja settings are not configured.")
    timestamp = timezone.localtime().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(
        f"{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}".encode("utf-8")
    ).decode("ascii")
    payload = json.dumps({
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": str(amount),
        "PartyA": phone,
        "PartyB": settings.MPESA_SHORTCODE,
        "PhoneNumber": phone,
        "CallBackURL": settings.PUBLIC_SITE_URL.rstrip("/")
        + "/api/v1/payments/mpesa/rent-stk-callback",
        "AccountReference": account_reference[:12],
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
            f"Daraja declined the STK push: {result.get('ResponseDescription', result)}"
        )
    return {
        "checkout_request_id": result.get("CheckoutRequestID", ""),
        "merchant_request_id": result.get("MerchantRequestID", ""),
    }
