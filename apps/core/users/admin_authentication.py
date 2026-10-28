import secrets
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from users.audit import record_audit_event
from users.models import SMSLoginChallenge
from users.roles import requires_finance_mfa
from users.sms import send_sms

ADMIN_CHALLENGE_SESSION_KEY = "admin_sms_challenge_id"


class FinanceMFAAdminAuthenticationForm(AdminAuthenticationForm):
    mfa_code = forms.RegexField(
        regex=r"^[0-9]{6}$",
        required=False,
        label="SMS verification code",
        error_messages={"invalid": "Enter the six-digit code sent to your phone."},
    )

    def clean(self):
        try:
            cleaned_data = super().clean()
        except ValidationError:
            if (
                self.cleaned_data.get("username")
                and self.cleaned_data.get("password")
                and getattr(self, "user_cache", None) is None
            ):
                record_audit_event(
                    self.request,
                    "login.failed",
                    "User",
                )
            raise

        user = self.get_user()
        if not requires_finance_mfa(user):
            record_audit_event(
                self.request,
                "login.succeeded",
                "User",
                new_values={"user_id": user.pk},
                actor=user,
            )
        return cleaned_data

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not requires_finance_mfa(user):
            return

        profile = getattr(user, "profile", None)
        if profile is None or not profile.phone:
            record_audit_event(
                self.request,
                "login.mfa_unavailable",
                "User",
                new_values={"user_id": user.pk},
                actor=user,
            )
            raise ValidationError(
                "A phone number is required for this account's finance-access roles.",
                code="mfa_contact_required",
            )

        code = self.cleaned_data.get("mfa_code", "")
        if not code:
            self._issue_challenge(user, profile.phone)
            raise ValidationError(
                "A sign-in code was sent to your registered phone. Enter it to continue.",
                code="mfa_code_required",
            )

        challenge_id = self.request.session.get(ADMIN_CHALLENGE_SESSION_KEY)
        if not challenge_id:
            raise ValidationError(
                "The sign-in challenge is missing. Sign in again to request a new code.",
                code="mfa_challenge_missing",
            )

        self._verify_challenge(user, challenge_id, code)

    def _issue_challenge(self, user, phone):
        challenge_id = self.request.session.get(ADMIN_CHALLENGE_SESSION_KEY)
        if challenge_id and SMSLoginChallenge.objects.filter(
            pk=challenge_id,
            user=user,
            expires_at__gt=timezone.now(),
            attempts__lt=settings.AUTH_SMS_MAX_ATTEMPTS,
        ).exists():
            return

        code = f"{secrets.randbelow(1_000_000):06d}"
        with transaction.atomic():
            challenge = SMSLoginChallenge.objects.create(
                user=user,
                code_hash=make_password(code),
                expires_at=timezone.now()
                + timedelta(seconds=settings.AUTH_SMS_CODE_LIFETIME_SECONDS),
            )
            send_sms(phone, f"Your Legacy Real Estate sign-in code is {code}.")

        self.request.session[ADMIN_CHALLENGE_SESSION_KEY] = challenge.pk
        record_audit_event(
            self.request,
            "login.mfa_challenge_issued",
            "User",
            new_values={"user_id": user.pk},
            actor=user,
        )

    def _verify_challenge(self, user, challenge_id, code):
        error = None
        with transaction.atomic():
            try:
                challenge = SMSLoginChallenge.objects.select_for_update().get(
                    pk=challenge_id,
                    user=user,
                )
            except SMSLoginChallenge.DoesNotExist:
                self.request.session.pop(ADMIN_CHALLENGE_SESSION_KEY, None)
                record_audit_event(
                    self.request,
                    "login.mfa_failed",
                    "User",
                    new_values={"user_id": user.pk},
                    actor=user,
                )
                error = ValidationError(
                    "The sign-in challenge is invalid. Sign in again to request a new code.",
                    code="invalid_mfa_challenge",
                )
            else:
                if (
                    challenge.is_expired()
                    or challenge.attempts >= settings.AUTH_SMS_MAX_ATTEMPTS
                ):
                    challenge.delete()
                    self.request.session.pop(ADMIN_CHALLENGE_SESSION_KEY, None)
                    record_audit_event(
                        self.request,
                        "login.mfa_failed",
                        "User",
                        new_values={"user_id": user.pk},
                        actor=user,
                    )
                    error = ValidationError(
                        "The sign-in challenge expired. Sign in again to request a new code.",
                        code="expired_mfa_challenge",
                    )
                elif not check_password(code, challenge.code_hash):
                    challenge.attempts += 1
                    challenge.save(update_fields=["attempts"])
                    if challenge.attempts >= settings.AUTH_SMS_MAX_ATTEMPTS:
                        challenge.delete()
                        self.request.session.pop(ADMIN_CHALLENGE_SESSION_KEY, None)
                    record_audit_event(
                        self.request,
                        "login.mfa_failed",
                        "User",
                        new_values={"user_id": user.pk},
                        actor=user,
                    )
                    error = ValidationError(
                        "The sign-in code is invalid.",
                        code="invalid_mfa_code",
                    )
                else:
                    challenge.delete()
                    self.request.session.pop(ADMIN_CHALLENGE_SESSION_KEY, None)
                    record_audit_event(
                        self.request,
                        "login.succeeded",
                        "User",
                        new_values={"user_id": user.pk},
                        actor=user,
                    )

        if error is not None:
            raise error
