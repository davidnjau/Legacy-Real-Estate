import json
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from users.audit import record_audit_event
from users.models import Branch, SMSLoginChallenge
from users.roles import requires_finance_mfa
from users.sms import send_sms


def _record_auth_event(request, action, user=None):
    record_audit_event(
        request,
        action,
        "User",
        new_values={"user_id": user.pk} if user else None,
        actor=user,
    )


def _problem(status, title, detail, problem_type):
    return JsonResponse(
        {
            "type": f"https://legacy.example/problems/{problem_type}",
            "title": title,
            "status": status,
            "detail": detail,
        },
        status=status,
        content_type="application/problem+json",
    )


def _json_body(request):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _user_payload(user):
    profile = getattr(user, "profile", None)
    if profile is None:
        name = user.get_full_name() or user.get_username()
        phone = None
        branches = Branch.objects.none()
    else:
        name = profile.name
        phone = profile.phone or None
        branches = profile.accessible_branches()
    return {
        "id": user.pk,
        "name": name,
        "email": user.email or None,
        "phone": phone,
        "roles": list(user.groups.order_by("name").values_list("name", flat=True)),
        "branches": list(branches.order_by("name").values("id", "name", "town")),
    }


@require_GET
@ensure_csrf_cookie
def csrf_token(request):
    return JsonResponse({"csrfToken": get_token(request)})


@require_POST
def login_view(request):
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")

    identifier = body.get("identifier")
    password = body.get("password")
    if not isinstance(identifier, str) or not isinstance(password, str):
        return _problem(
            400,
            "Invalid request",
            "Both identifier and password are required.",
            "invalid-request",
        )

    user = authenticate(request, username=identifier.strip(), password=password)
    if user is None:
        _record_auth_event(request, "login.failed")
        return _problem(
            401,
            "Authentication failed",
            "The supplied credentials are invalid.",
            "authentication-failed",
        )

    if requires_finance_mfa(user):
        profile = getattr(user, "profile", None)
        if profile is None or not profile.phone:
            _record_auth_event(request, "login.mfa_unavailable", user)
            return _problem(
                403,
                "Two-factor authentication unavailable",
                "A phone number is required for this account's finance-access roles.",
                "mfa-contact-required",
            )
        code = f"{secrets.randbelow(1_000_000):06d}"
        with transaction.atomic():
            challenge = SMSLoginChallenge.objects.create(
                user=user,
                code_hash=make_password(code),
                expires_at=timezone.now()
                + timedelta(seconds=settings.AUTH_SMS_CODE_LIFETIME_SECONDS),
            )
            send_sms(
                profile.phone,
                f"Your Legacy Real Estate sign-in code is {code}.",
            )
        _record_auth_event(request, "login.mfa_challenge_issued", user)

        return JsonResponse(
            {
                "mfa_required": True,
                "challenge_id": challenge.pk,
            },
            status=202,
        )

    login(request, user, backend="users.authentication.EmailOrPhoneBackend")
    _record_auth_event(request, "login.succeeded", user)
    return JsonResponse({"user": _user_payload(user)})


@require_POST
def verify_sms_challenge(request):
    body = _json_body(request)
    if body is None:
        return _problem(400, "Invalid request", "Expected a JSON object.", "invalid-request")

    challenge_id = body.get("challenge_id")
    code = body.get("code")
    if (
        not isinstance(challenge_id, int)
        or isinstance(challenge_id, bool)
        or not isinstance(code, str)
        or len(code) != 6
        or not code.isascii()
        or not code.isdigit()
    ):
        return _problem(
            400,
            "Invalid request",
            "A challenge_id and six-digit code are required.",
            "invalid-request",
        )

    response = None
    user = None
    with transaction.atomic():
        try:
            challenge = SMSLoginChallenge.objects.select_for_update().select_related(
                "user"
            ).get(
                pk=challenge_id,
                user__is_active=True,
            )
        except SMSLoginChallenge.DoesNotExist:
            _record_auth_event(request, "login.mfa_failed")
            response = _problem(
                401,
                "Invalid challenge",
                "The challenge is invalid.",
                "invalid-mfa",
            )
        else:
            user = challenge.user
            if challenge.is_expired():
                _record_auth_event(request, "login.mfa_failed", user)
                challenge.delete()
                response = _problem(
                    401,
                    "Expired challenge",
                    "Request a new sign-in code.",
                    "expired-mfa",
                )
            elif challenge.attempts >= settings.AUTH_SMS_MAX_ATTEMPTS:
                _record_auth_event(request, "login.mfa_failed", user)
                challenge.delete()
                response = _problem(
                    401,
                    "Invalid challenge",
                    "Request a new sign-in code.",
                    "invalid-mfa",
                )
            elif not check_password(code, challenge.code_hash):
                challenge.attempts += 1
                challenge.save(update_fields=["attempts"])
                _record_auth_event(request, "login.mfa_failed", user)
                if challenge.attempts >= settings.AUTH_SMS_MAX_ATTEMPTS:
                    challenge.delete()
                response = _problem(
                    401,
                    "Invalid code",
                    "The sign-in code is invalid.",
                    "invalid-mfa",
                )
            else:
                challenge.delete()

    if response is not None:
        return response

    login(request, user, backend="users.authentication.EmailOrPhoneBackend")
    _record_auth_event(request, "login.succeeded", user)
    return JsonResponse({"user": _user_payload(user)})


@require_POST
def logout_view(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    user = request.user
    logout(request)
    _record_auth_event(request, "logout.succeeded", user)
    return JsonResponse({"status": "ok"})


@require_GET
def current_user(request):
    if not request.user.is_authenticated:
        return _problem(401, "Authentication required", "Sign in first.", "authentication-required")
    return JsonResponse({"user": _user_payload(request.user)})
