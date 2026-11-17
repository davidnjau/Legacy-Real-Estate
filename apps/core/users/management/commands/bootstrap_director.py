import re
import secrets
import smtplib
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from users.audit import record_audit_event
from users.models import (
    Agency,
    AgencyMembership,
    DirectorActivationChallenge,
    UserProfile,
)
from users.roles import SUPER_ADMIN_ROLE
from users.views import _send_director_activation_email


class Command(BaseCommand):
    help = "Provision an agency Director and email a one-time activation code."

    def add_arguments(self, parser):
        parser.add_argument("--agency-name", required=True, help="Verified agency name.")
        parser.add_argument("--agency-slug", required=True, help="Unique agency identifier.")
        parser.add_argument("--name", required=True, help="Director's verified full name.")
        parser.add_argument("--email", required=True, help="Director's verified email address.")
        parser.add_argument(
            "--phone",
            required=True,
            help="Director's verified phone in 2547XXXXXXXX or 2541XXXXXXXX format.",
        )
        parser.add_argument(
            "--authorized-by",
            required=True,
            help="Name or change reference for the trusted operator authorizing provisioning.",
        )
        parser.add_argument(
            "--confirm",
            required=True,
            help="Must be exactly PROVISION-AGENCY-DIRECTOR to authorize the operation.",
        )

    def handle(self, *args, **options):
        agency_name = options["agency_name"].strip()
        agency_slug = options["agency_slug"].strip().lower()
        name = options["name"].strip()
        email = options["email"].strip().lower()
        phone = options["phone"].strip()
        authorized_by = options["authorized_by"].strip()

        if options["confirm"] != "PROVISION-AGENCY-DIRECTOR":
            raise CommandError("--confirm must be exactly PROVISION-AGENCY-DIRECTOR.")
        if not agency_name or len(agency_name) > 255:
            raise CommandError("--agency-name must be between 1 and 255 characters.")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", agency_slug):
            raise CommandError(
                "--agency-slug must contain lowercase letters, digits, and hyphens."
            )
        if not name or len(name) > 255:
            raise CommandError("--name must be between 1 and 255 characters.")
        if not authorized_by or len(authorized_by) > 255:
            raise CommandError("--authorized-by must be between 1 and 255 characters.")
        try:
            validate_email(email)
        except ValidationError as error:
            raise CommandError(f"--email is invalid: {'; '.join(error.messages)}") from error
        if not re.fullmatch(r"254[17]\d{8}", phone):
            raise CommandError("--phone must use 2547XXXXXXXX or 2541XXXXXXXX format.")
        if not settings.PUBLIC_SITE_URL:
            raise CommandError("PUBLIC_SITE_URL must be configured before provisioning.")

        user_model = get_user_model()
        audit_request = SimpleNamespace(user=AnonymousUser(), META={})
        activation_code = None
        challenge = None
        send_activation = False
        try:
            with transaction.atomic():
                agency, _ = Agency.objects.get_or_create(
                    slug=agency_slug,
                    defaults={"name": agency_name},
                )
                if agency.name != agency_name:
                    raise CommandError(
                        "The agency slug already exists with a different name."
                    )

                role = Group.objects.select_for_update().get(name=SUPER_ADMIN_ROLE)
                user = user_model.objects.filter(
                    Q(username__iexact=email) | Q(email__iexact=email)
                ).first()
                if user is None:
                    if UserProfile.objects.filter(phone__iexact=phone).exists():
                        raise CommandError("An account already uses this phone number.")
                    user = user_model.objects.create_user(
                        username=email,
                        email=email,
                        password=None,
                        is_active=False,
                        is_staff=False,
                        is_superuser=False,
                    )
                    profile = UserProfile(user=user, name=name, phone=phone)
                    profile.full_clean()
                    profile.save()
                    send_activation = True
                else:
                    if (user.email or "").strip().lower() != email:
                        raise CommandError(
                            "The existing account's email does not match the verified address."
                        )
                    try:
                        profile = user.profile
                    except UserProfile.DoesNotExist as error:
                        raise CommandError(
                            "The existing account has no user profile; resolve it before provisioning."
                        ) from error
                    if profile.phone != phone or profile.name != name:
                        raise CommandError(
                            "The existing account's verified name or phone does not match."
                        )
                    if not user.is_active and user.has_usable_password():
                        raise CommandError(
                            "An inactive account with a password already uses this email."
                        )
                    if user.is_active and not user.has_usable_password():
                        raise CommandError(
                            "An active account without a password must be resolved before provisioning."
                        )
                    send_activation = not user.is_active

                membership, created = AgencyMembership.objects.get_or_create(
                    profile=profile,
                    agency=agency,
                )
                if (
                    not created
                    and membership.roles.filter(pk=role.pk).exists()
                    and (user.is_active or user.has_usable_password())
                ):
                    raise CommandError(
                        "This account is already a Director for the requested agency."
                    )
                membership.roles.add(role)
                if send_activation:
                    activation_code = f"{secrets.randbelow(1_000_000):06d}"
                    DirectorActivationChallenge.objects.filter(user=user).delete()
                    challenge = DirectorActivationChallenge.objects.create(
                        user=user,
                        code_hash=make_password(activation_code),
                        expires_at=timezone.now()
                        + timedelta(seconds=settings.AUTH_DIRECTOR_ACTIVATION_TTL_SECONDS),
                    )
                record_audit_event(
                    audit_request,
                    "director.bootstrap_created",
                    "User",
                    new_values={
                        "user_id": user.pk,
                        "email": user.email,
                        "agency_id": agency.pk,
                        "agency_slug": agency.slug,
                        "role": SUPER_ADMIN_ROLE,
                        "provisioning_method": "trusted_operator_management_command",
                        "authorized_by": authorized_by,
                        "activation_method": "email_otp",
                    },
                )
        except Group.DoesNotExist as error:
            raise CommandError(f"The {SUPER_ADMIN_ROLE} role is not configured.") from error
        except (IntegrityError, ValidationError) as error:
            raise CommandError(f"Agency Director provisioning failed validation: {error}") from error

        if send_activation and challenge is not None and activation_code is not None:
            try:
                _send_director_activation_email(user, challenge.pk, activation_code)
            except (ImproperlyConfigured, RuntimeError, smtplib.SMTPException, OSError) as error:
                challenge.delete()
                record_audit_event(
                    audit_request,
                    "director.bootstrap_email_failed",
                    "User",
                    new_values={
                        "user_id": user.pk,
                        "agency_id": agency.pk,
                        "error_type": type(error).__name__,
                    },
                )
                raise CommandError(
                    "The Director account was created, but its activation email failed. "
                    "Rerun the command with the same verified account details to issue a new code."
                ) from error

        self.stdout.write(
            self.style.SUCCESS(
                f"Director access provisioned for {email} at {agency.name}."
                + (" A one-time activation code was sent." if send_activation else "")
            )
        )
