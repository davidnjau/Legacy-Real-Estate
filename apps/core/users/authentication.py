from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q
from django.utils import timezone


class EmailOrPhoneBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = username or kwargs.get("email") or kwargs.get("phone")
        if not identifier or not password:
            return None

        user_model = get_user_model()
        lookup = Q(email__iexact=identifier)
        phone = identifier[1:] if identifier.startswith("+") else identifier
        lookup |= Q(profile__phone=phone)

        try:
            user = user_model._default_manager.get(lookup)
        except user_model.DoesNotExist:
            user_model().set_password(password)
            return None
        except user_model.MultipleObjectsReturned:
            return None

        if not self.user_can_authenticate(user):
            return None

        profile = getattr(user, "profile", None)
        if profile and profile.locked_until and profile.locked_until > timezone.now():
            return None

        if user.check_password(password):
            if profile and (profile.failed_login_attempts or profile.locked_until):
                profile.failed_login_attempts = 0
                profile.locked_until = None
                profile.save(update_fields=["failed_login_attempts", "locked_until"])
            return user

        if profile:
            profile.failed_login_attempts += 1
            update_fields = ["failed_login_attempts"]
            if profile.failed_login_attempts >= settings.AUTH_LOGIN_MAX_ATTEMPTS:
                profile.locked_until = timezone.now() + timedelta(
                    seconds=settings.AUTH_LOGIN_LOCKOUT_SECONDS
                )
                update_fields.append("locked_until")
            profile.save(update_fields=update_fields)
        return None
