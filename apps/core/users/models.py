from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from users.roles import SUPER_ADMIN_ROLE


class Branch(models.Model):
    name = models.CharField(max_length=100, unique=True)
    town = models.CharField(max_length=100)
    paybill_numbers = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    name = models.CharField(max_length=255)
    phone = models.CharField(
        max_length=12,
        blank=True,
        validators=[
            RegexValidator(
                r"^254[17]\d{8}$",
                "Use international format 2547XXXXXXXX or 2541XXXXXXXX.",
            )
        ],
    )
    branches = models.ManyToManyField(Branch, related_name="user_profiles", blank=True)
    failed_login_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["name", "user__email"]
        permissions = [
            ("manage_branch_assignments", "Can manage user branch assignments"),
        ]
        constraints = [
            models.UniqueConstraint(
                Lower("phone"),
                condition=~Q(phone=""),
                name="users_profile_phone_ci_unique",
            )
        ]

    def __str__(self):
        return self.name

    def accessible_branches(self):
        if self.user.groups.filter(name=SUPER_ADMIN_ROLE).exists():
            return Branch.objects.all()
        return self.branches.all()


class SMSLoginChallenge(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sms_login_challenges",
    )
    code_hash = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["user", "expires_at"])]

    def is_expired(self):
        return timezone.now() >= self.expires_at


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Audit log entries are immutable.")

    def delete(self):
        raise ValidationError("Audit log entries are immutable.")

    def bulk_create(self, objs, **kwargs):
        raise ValidationError("Create audit log entries individually.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError("Audit log entries are immutable.")


class AuditLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="audit_events",
        blank=True,
        null=True,
    )
    action = models.CharField(max_length=100)
    entity = models.CharField(max_length=100)
    old_values = models.JSONField(blank=True, null=True)
    new_values = models.JSONField(blank=True, null=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["-timestamp", "-id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Audit log entries are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit log entries are immutable.")

    def __str__(self):
        return f"{self.action} {self.entity} at {self.timestamp}"
