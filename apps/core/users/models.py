import builtins
import re
import uuid
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils import timezone

from users.roles import SUPER_ADMIN_ROLE


class Agency(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class AgencySettings(models.Model):
    agency = models.OneToOneField(
        Agency,
        on_delete=models.CASCADE,
        related_name="settings",
    )
    mri_tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=7.50)
    commission_vat_enabled = models.BooleanField(default=True)
    commission_vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=16.00)
    grace_period_days = models.PositiveSmallIntegerField(default=5)
    penalty_type = models.CharField(
        max_length=12,
        choices=(("percentage", "Percentage"), ("flat", "Flat fee")),
        default="percentage",
    )
    penalty_rate = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    penalty_flat_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sms_templates = models.JSONField(default=dict, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="updated_agency_settings",
        blank=True,
        null=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(mri_tax_rate__gte=0)
                    & models.Q(mri_tax_rate__lte=100)
                    & models.Q(commission_vat_rate__gte=0)
                    & models.Q(commission_vat_rate__lte=100)
                    & models.Q(penalty_rate__gte=0)
                    & models.Q(penalty_rate__lte=100)
                    & models.Q(penalty_flat_fee__gte=0)
                    & models.Q(grace_period_days__lte=365)
                ),
                name="users_agency_settings_values_valid",
            ),
        ]

    def clean(self):
        super().clean()
        if self.penalty_type not in {"percentage", "flat"}:
            raise ValidationError({"penalty_type": "Choose percentage or flat fee."})
        if not isinstance(self.sms_templates, dict):
            raise ValidationError({"sms_templates": "SMS templates must be an object."})

    def __str__(self):
        return f"Settings for {self.agency}"


class Branch(models.Model):
    agency = models.ForeignKey(
        Agency,
        on_delete=models.PROTECT,
        related_name="branches",
    )
    name = models.CharField(max_length=100)
    town = models.CharField(max_length=100)
    paybill_numbers = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["agency__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["agency", "name"],
                name="users_branch_agency_name_unique",
            )
        ]

    def __str__(self):
        return self.name


class Landlord(models.Model):
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="landlords",
    )
    portal_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="landlord_record",
        blank=True,
        null=True,
    )
    name = models.CharField(max_length=255)
    id_or_registration_number = models.CharField(max_length=100)
    kra_pin = models.CharField(max_length=20, blank=True)
    phone = models.CharField(
        max_length=12,
        validators=[
            RegexValidator(
                r"^254[17]\d{8}$",
                "Use international format 2547XXXXXXXX or 2541XXXXXXXX.",
            )
        ],
    )
    email = models.EmailField(blank=True)
    payout_details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "id_or_registration_number"],
                name="users_landlord_branch_identity_unique",
            )
        ]

    def __str__(self):
        return self.name


class ManagementAgreement(models.Model):
    landlord = models.ForeignKey(
        Landlord,
        on_delete=models.PROTECT,
        related_name="management_agreements",
    )
    effective_from = models.DateField()
    effective_to = models.DateField(blank=True, null=True)
    commission_model = models.CharField(max_length=50)
    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
    )
    fixed_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
    )
    payout_day = models.PositiveSmallIntegerField(blank=True, null=True)
    mri_enabled = models.BooleanField(default=False)
    terms = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-effective_from", "-id"]

    def clean(self):
        super().clean()
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError(
                {"effective_to": "The agreement end date cannot be before its start date."}
            )
        if self.payout_day is not None and not 1 <= self.payout_day <= 31:
            raise ValidationError({"payout_day": "Payout day must be between 1 and 31."})
        if self.commission_rate is not None and self.commission_rate < 0:
            raise ValidationError({"commission_rate": "Commission rate cannot be negative."})
        if self.fixed_fee is not None and self.fixed_fee < 0:
            raise ValidationError({"fixed_fee": "Fixed fee cannot be negative."})

    def __str__(self):
        return f"{self.landlord} agreement from {self.effective_from}"


class LandlordRemittance(models.Model):
    STATUS_AWAITING_APPROVAL = "awaiting_approval"
    STATUS_REJECTED = "rejected"
    STATUS_APPROVED = "approved"
    STATUS_PAID = "paid"
    STATUS_NO_PAYOUT = "no_payout"
    STATUS_CHOICES = (
        (STATUS_AWAITING_APPROVAL, "Awaiting approval"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_PAID, "Paid"),
        (STATUS_NO_PAYOUT, "No payout due"),
    )

    landlord = models.ForeignKey(
        Landlord,
        on_delete=models.PROTECT,
        related_name="remittances",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    revision = models.PositiveSmallIntegerField(default=1)
    statement_snapshot = models.JSONField()
    collected_amount = models.DecimalField(max_digits=12, decimal_places=2)
    commission_amount = models.DecimalField(max_digits=12, decimal_places=2)
    expenses_amount = models.DecimalField(max_digits=12, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    net_payout = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_AWAITING_APPROVAL,
    )
    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="prepared_landlord_remittances",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reviewed_landlord_remittances",
        blank=True,
        null=True,
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.TextField(blank=True)
    payout_reference = models.CharField(max_length=100, blank=True, null=True, unique=True)
    payout_date = models.DateField(blank=True, null=True)
    payout_recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_landlord_remittances",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_end", "-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["landlord", "period_start", "period_end", "revision"],
                name="users_landlordremittance_period_revision_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(period_end__gte=models.F("period_start")),
                name="users_landlordremittance_period_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(collected_amount__gte=0)
                    & models.Q(commission_amount__gte=0)
                    & models.Q(expenses_amount__gte=0)
                    & models.Q(net_payout__gte=0)
                    & (
                        models.Q(tax_amount__isnull=True)
                        | models.Q(tax_amount__gte=0)
                    )
                ),
                name="users_landlordremittance_amounts_nonnegative",
            ),
        ]

    def clean(self):
        super().clean()
        if self.period_end < self.period_start:
            raise ValidationError({"period_end": "The remittance period end cannot precede its start."})
        if self.status in {
            self.STATUS_REJECTED,
            self.STATUS_APPROVED,
            self.STATUS_PAID,
            self.STATUS_NO_PAYOUT,
        } and (self.reviewed_by_id is None or self.reviewed_at is None):
            raise ValidationError({"reviewed_by": "Reviewed remittances require an approver and approval time."})
        if self.status == self.STATUS_REJECTED and not self.rejection_reason.strip():
            raise ValidationError({"rejection_reason": "Rejected remittances require a reason."})
        if self.status == self.STATUS_PAID and (
            self.net_payout <= 0
            or not self.payout_reference
            or self.payout_date is None
            or self.payout_recorded_by_id is None
        ):
            raise ValidationError({"payout_reference": "Paid remittances require payout details."})
        if self.status == self.STATUS_NO_PAYOUT and self.net_payout != 0:
            raise ValidationError({"status": "Only zero-value remittances can be closed without a payout."})

    def __str__(self):
        return f"{self.landlord} remittance {self.period_start} to {self.period_end} (revision {self.revision})"


class Property(models.Model):
    BILLING_MODE_METERED_WATER = "metered_water"
    BILLING_MODE_FIXED_UTILITIES = "fixed_utilities"
    BILLING_MODE_CHOICES = (
        (BILLING_MODE_METERED_WATER, "Metered water"),
        (BILLING_MODE_FIXED_UTILITIES, "Fixed utilities"),
    )

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="properties")
    landlord = models.ForeignKey(
        Landlord,
        on_delete=models.PROTECT,
        related_name="properties",
        blank=True,
        null=True,
    )
    name = models.CharField(max_length=255)
    property_type = models.CharField(max_length=100)
    location = models.CharField(max_length=255)
    address = models.CharField(max_length=255, blank=True)
    currency_code = models.CharField(
        max_length=3,
        default="KES",
        validators=[RegexValidator(r"^[A-Z]{3}$")],
    )
    timezone = models.CharField(max_length=64, default="Africa/Nairobi")
    rent_due_day = models.PositiveSmallIntegerField(default=10)
    billing_mode = models.CharField(
        max_length=20,
        choices=BILLING_MODE_CHOICES,
        default=BILLING_MODE_METERED_WATER,
    )
    expected_unit_count = models.PositiveSmallIntegerField(default=0)
    default_unit_type = models.CharField(max_length=100, blank=True)
    default_rent = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    security_deposit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    move_in_letting_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    water_deposit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    default_garbage_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    default_water_rate = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    default_handover_items = models.JSONField(default=list, blank=True)
    gps_coordinates = models.JSONField(default=dict, blank=True)
    amenities = models.JSONField(default=list, blank=True)
    house_rules = models.JSONField(default=dict, blank=True)
    photos = models.JSONField(default=list, blank=True)
    caretakers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="assigned_properties",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(rent_due_day__gte=1)
                    & models.Q(rent_due_day__lte=28)
                    & models.Q(default_rent__gte=0)
                    & models.Q(security_deposit__gte=0)
                    & models.Q(move_in_letting_fee__gte=0)
                    & models.Q(water_deposit__gte=0)
                    & models.Q(default_garbage_charge__gte=0)
                    & models.Q(default_water_rate__gte=0)
                ),
                name="users_property_billing_defaults_valid",
            ),
        ]

    def __str__(self):
        return self.name


class PropertyBillingSettings(models.Model):
    property = models.OneToOneField(
        Property,
        on_delete=models.PROTECT,
        related_name="billing_settings",
    )
    paybill_number = models.CharField(max_length=32, blank=True)
    account_number = models.CharField(max_length=64, blank=True)
    universal_security_deposit = models.DecimalField(max_digits=12, decimal_places=2, default=8000)
    universal_water_deposit = models.DecimalField(max_digits=12, decimal_places=2, default=500)
    first_online_billing_month = models.CharField(
        max_length=7,
        blank=True,
        validators=[RegexValidator(r"^\d{4}-(0[1-9]|1[0-2])$")],
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="updated_property_billing_settings",
        blank=True,
        null=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(universal_security_deposit__gte=0)
                & models.Q(universal_water_deposit__gte=0),
                name="users_property_billing_deposits_nonnegative",
            ),
        ]

    def clean(self):
        super().clean()
        if self.first_online_billing_month and not re.fullmatch(
            r"\d{4}-(0[1-9]|1[0-2])", self.first_online_billing_month
        ):
            raise ValidationError(
                {"first_online_billing_month": "Use a valid YYYY-MM billing month."}
            )

    def __str__(self):
        return f"Billing settings for {self.property}"


class PropertyRecurringCharge(models.Model):
    property = models.ForeignKey(
        Property,
        on_delete=models.PROTECT,
        related_name="recurring_charges",
    )
    name = models.CharField(max_length=80)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                "property",
                Lower("name"),
                name="users_property_recurring_charge_name_ci_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="users_property_recurring_charge_amount_nonnegative",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.name.strip():
            raise ValidationError({"name": "A recurring charge name is required."})
        if self.amount < 0:
            raise ValidationError({"amount": "Recurring charges cannot be negative."})

    def __str__(self):
        return f"{self.property} - {self.name}"


class PropertyUnitType(models.Model):
    property = models.ForeignKey(
        Property,
        on_delete=models.PROTECT,
        related_name="unit_types",
    )
    name = models.CharField(max_length=100)
    unit_count = models.PositiveSmallIntegerField(default=0)
    monthly_rent = models.DecimalField(max_digits=12, decimal_places=2)
    security_deposit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                "property",
                Lower("name"),
                name="users_property_unit_type_name_ci_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(monthly_rent__gte=0)
                & models.Q(security_deposit__gte=0),
                name="users_property_unit_type_amounts_nonnegative",
            ),
        ]

    def clean(self):
        super().clean()
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValidationError({"name": "A unit type name is required."})
        if self.monthly_rent < 0 or self.security_deposit < 0:
            raise ValidationError("Rent and security deposits cannot be negative.")

    def __str__(self):
        return f"{self.property} - {self.name}"


class PropertyUnitTypeUtility(models.Model):
    UTILITY_WATER = "water"
    UTILITY_ELECTRICITY = "electricity"
    UTILITY_OTHER = "other"
    UTILITY_CHOICES = (
        (UTILITY_WATER, "Water"),
        (UTILITY_ELECTRICITY, "Electricity"),
        (UTILITY_OTHER, "Other"),
    )
    METHOD_FIXED = "fixed"
    METHOD_METERED = "metered"
    BILLING_METHOD_CHOICES = (
        (METHOD_FIXED, "Fixed"),
        (METHOD_METERED, "Metered"),
    )

    unit_type = models.ForeignKey(
        PropertyUnitType,
        on_delete=models.PROTECT,
        related_name="utilities",
    )
    utility_type = models.CharField(max_length=30, choices=UTILITY_CHOICES)
    name = models.CharField(max_length=100)
    billing_method = models.CharField(max_length=20, choices=BILLING_METHOD_CHOICES)
    fixed_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    rate_per_unit = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    deposit_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["utility_type", "name", "id"]
        constraints = [
            models.UniqueConstraint(
                "unit_type",
                Lower("name"),
                name="users_property_unit_type_utility_name_ci_unique",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(fixed_amount__isnull=True)
                    | models.Q(fixed_amount__gte=0)
                )
                & (
                    models.Q(rate_per_unit__isnull=True)
                    | models.Q(rate_per_unit__gte=0)
                )
                & (
                    models.Q(deposit_amount__isnull=True)
                    | models.Q(deposit_amount__gte=0)
                ),
                name="users_property_unit_type_utility_amounts_nonnegative",
            ),
        ]

    def clean(self):
        super().clean()
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValidationError({"name": "A utility name is required."})
        if self.billing_method == self.METHOD_FIXED:
            if self.fixed_amount is None or self.fixed_amount < 0:
                raise ValidationError(
                    {"fixed_amount": "Fixed utilities need a non-negative monthly amount."}
                )
            if self.rate_per_unit is not None:
                raise ValidationError(
                    {"rate_per_unit": "Fixed utilities cannot have a metered rate."}
                )
        elif self.billing_method == self.METHOD_METERED:
            if self.rate_per_unit is None or self.rate_per_unit < 0:
                raise ValidationError(
                    {"rate_per_unit": "Metered utilities need a non-negative rate per unit."}
                )
            if self.fixed_amount is not None:
                raise ValidationError(
                    {"fixed_amount": "Metered utilities cannot have a fixed monthly amount."}
                )
        if self.deposit_amount is not None and self.deposit_amount < 0:
            raise ValidationError({"deposit_amount": "Utility deposits cannot be negative."})

    def __str__(self):
        return f"{self.unit_type} - {self.name}"


class PropertyCaretakerAssignment(models.Model):
    property = models.ForeignKey(
        Property,
        on_delete=models.PROTECT,
        related_name="caretaker_assignments",
    )
    caretaker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="property_caretaker_assignments",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_caretaker_assignments",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(blank=True, null=True)
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ended_caretaker_assignments",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ["-assigned_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "caretaker"],
                condition=models.Q(ended_at__isnull=True),
                name="users_active_property_caretaker_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["caretaker", "ended_at"]),
            models.Index(fields=["property", "ended_at"]),
        ]

    @builtins.property
    def is_active(self):
        return self.ended_at is None

    def __str__(self):
        return f"{self.caretaker} assigned to {self.property}"


class CaretakerInvitation(models.Model):
    property = models.ForeignKey(
        Property,
        on_delete=models.PROTECT,
        related_name="caretaker_invitations",
    )
    properties = models.ManyToManyField(
        Property,
        related_name="multi_caretaker_invitations",
        blank=True,
    )
    email = models.EmailField()
    full_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=12, blank=True)
    id_or_registration_number = models.CharField(max_length=100, blank=True)
    kra_pin = models.CharField(max_length=20, blank=True)
    token_hash = models.CharField(max_length=64, unique=True)
    code_hash = models.CharField(max_length=128, blank=True, null=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    email_sent_at = models.DateTimeField(blank=True, null=True)
    email_delivery_error = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_caretaker_invitations",
    )
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(blank=True, null=True)
    used_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="accepted_caretaker_invitations",
        blank=True,
        null=True,
    )
    revoked_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["property", "created_at"]),
            models.Index(fields=["email", "expires_at"]),
        ]

    @builtins.property
    def status(self):
        if self.used_at is not None:
            return "used"
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at <= timezone.now():
            return "expired"
        return "active"

    @builtins.property
    def is_usable(self):
        return self.status == "active"

    def __str__(self):
        return f"{self.email} invited to {self.property}"


class Vendor(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="vendors")
    name = models.CharField(max_length=255)
    trade = models.CharField(max_length=100)
    phone = models.CharField(
        max_length=12,
        validators=[
            RegexValidator(
                r"^254[17]\d{8}$",
                "Use international format 2547XXXXXXXX or 2541XXXXXXXX.",
            )
        ],
    )
    email = models.EmailField(blank=True)
    mpesa_number = models.CharField(
        max_length=12,
        blank=True,
        validators=[
            RegexValidator(
                r"^254[17]\d{8}$",
                "Use international format 2547XXXXXXXX or 2541XXXXXXXX.",
            )
        ],
    )
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "phone"],
                name="users_vendor_branch_phone_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(rating__gte=0) & models.Q(rating__lte=5),
                name="users_vendor_rating_valid",
            ),
        ]

    def clean(self):
        super().clean()
        if self.rating < 0 or self.rating > 5:
            raise ValidationError({"rating": "Rating must be between 0 and 5."})

    def __str__(self):
        return f"{self.name} ({self.trade})"


class MaintenanceTicket(models.Model):
    CATEGORY_PLUMBING = "plumbing"
    CATEGORY_ELECTRICAL = "electrical"
    CATEGORY_STRUCTURAL = "structural"
    CATEGORY_SECURITY = "security"
    CATEGORY_OTHER = "other"
    CATEGORY_CHOICES = (
        (CATEGORY_PLUMBING, "Plumbing"),
        (CATEGORY_ELECTRICAL, "Electrical"),
        (CATEGORY_STRUCTURAL, "Structural"),
        (CATEGORY_SECURITY, "Security"),
        (CATEGORY_OTHER, "Other"),
    )
    PRIORITY_LOW = "low"
    PRIORITY_NORMAL = "normal"
    PRIORITY_HIGH = "high"
    PRIORITY_URGENT = "urgent"
    PRIORITY_CHOICES = (
        (PRIORITY_LOW, "Low"),
        (PRIORITY_NORMAL, "Normal"),
        (PRIORITY_HIGH, "High"),
        (PRIORITY_URGENT, "Urgent"),
    )
    STATUS_OPEN = "open"
    STATUS_ASSIGNED = "assigned"
    STATUS_QUOTATION = "quotation"
    STATUS_AWAITING_LANDLORD_APPROVAL = "awaiting_landlord_approval"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = (
        (STATUS_OPEN, "Open"),
        (STATUS_ASSIGNED, "Assigned"),
        (STATUS_QUOTATION, "Quotation"),
        (STATUS_AWAITING_LANDLORD_APPROVAL, "Awaiting landlord approval"),
        (STATUS_IN_PROGRESS, "In progress"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CLOSED, "Closed"),
    )
    ALLOWED_TRANSITIONS = {
        STATUS_OPEN: {STATUS_ASSIGNED},
        STATUS_ASSIGNED: {STATUS_QUOTATION, STATUS_IN_PROGRESS},
        STATUS_QUOTATION: {STATUS_AWAITING_LANDLORD_APPROVAL, STATUS_IN_PROGRESS},
        STATUS_AWAITING_LANDLORD_APPROVAL: {STATUS_QUOTATION, STATUS_IN_PROGRESS},
        STATUS_IN_PROGRESS: {STATUS_COMPLETED},
        STATUS_COMPLETED: {STATUS_CLOSED},
        STATUS_CLOSED: set(),
    }

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="maintenance_tickets")
    unit = models.ForeignKey(
        "Unit",
        on_delete=models.PROTECT,
        related_name="maintenance_tickets",
        blank=True,
        null=True,
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reported_maintenance_tickets",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assigned_maintenance_tickets",
        blank=True,
        null=True,
    )
    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.PROTECT,
        related_name="maintenance_tickets",
        blank=True,
        null=True,
    )
    title = models.CharField(max_length=255)
    description = models.TextField()
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default=PRIORITY_NORMAL)
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default=STATUS_OPEN)
    photos = models.JSONField(default=list, blank=True)
    resolution_notes = models.TextField(blank=True)
    resolution_fundi_name = models.CharField(max_length=255, blank=True)
    repair_cost = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    closed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["property", "status"]),
            models.Index(fields=["assigned_to", "status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(repair_cost__isnull=True) | models.Q(repair_cost__gte=0),
                name="users_maintenanceticket_repair_cost_nonnegative",
            ),
        ]

    def clean(self):
        super().clean()
        if self.unit_id and self.unit.property_id != self.property_id:
            raise ValidationError({"unit": "The unit must belong to the ticket property."})
        if self.vendor_id and self.vendor.branch_id != self.property.branch_id:
            raise ValidationError({"vendor": "The vendor must belong to the ticket property's branch."})
        if not isinstance(self.photos, list):
            raise ValidationError({"photos": "Photos must be an array."})
        if self.status == self.STATUS_OPEN and self.assigned_to_id:
            raise ValidationError({"assigned_to": "Open tickets cannot be assigned."})
        if self.status != self.STATUS_OPEN and self.assigned_to_id is None:
            raise ValidationError({"assigned_to": "Non-open tickets require an assignee."})
        if self.status == self.STATUS_COMPLETED and self.completed_at is None:
            raise ValidationError({"completed_at": "Completed tickets require a completion time."})
        if self.status == self.STATUS_CLOSED and self.closed_at is None:
            raise ValidationError({"closed_at": "Closed tickets require a closure time."})

    def transition_to(self, new_status):
        if new_status not in dict(self.STATUS_CHOICES):
            raise ValidationError({"status": "This maintenance status is not supported."})
        if new_status == self.status:
            return
        if new_status not in self.ALLOWED_TRANSITIONS[self.status]:
            raise ValidationError(
                {"status": f"Tickets cannot transition from {self.status} to {new_status}."}
            )
        if new_status != self.STATUS_OPEN and self.assigned_to_id is None:
            raise ValidationError({"assigned_to": "Assign the ticket before changing its status."})
        self.status = new_status
        now = timezone.now()
        if new_status == self.STATUS_COMPLETED:
            self.completed_at = now
        if new_status == self.STATUS_CLOSED:
            self.closed_at = now

    def __str__(self):
        return f"{self.property} - {self.title} ({self.status})"


class MaintenanceTicketComment(models.Model):
    ticket = models.ForeignKey(MaintenanceTicket, on_delete=models.PROTECT, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="maintenance_ticket_comments",
    )
    body = models.TextField()
    photos = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def clean(self):
        super().clean()
        if not self.body.strip():
            raise ValidationError({"body": "A comment is required."})
        if not isinstance(self.photos, list):
            raise ValidationError({"photos": "Photos must be an array."})

    def __str__(self):
        return f"Comment on {self.ticket_id} by {self.author_id}"


class MaintenanceQuotation(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    )

    ticket = models.ForeignKey(MaintenanceTicket, on_delete=models.PROTECT, related_name="quotations")
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="maintenance_quotations")
    description = models.TextField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    valid_until = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_maintenance_quotations",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_maintenance_quotations",
        blank=True,
        null=True,
    )
    approved_at = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="users_maintenancequotation_amount_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if self.amount <= 0:
            raise ValidationError({"amount": "Quotation amount must be positive."})
        if self.vendor.branch_id != self.ticket.property.branch_id:
            raise ValidationError({"vendor": "The vendor must belong to the ticket property branch."})
        if self.status == self.STATUS_APPROVED and (
            self.approved_by_id is None or self.approved_at is None
        ):
            raise ValidationError({"approved_by": "Approved quotations require an approver and approval time."})
        if self.status == self.STATUS_REJECTED and not self.rejection_reason.strip():
            raise ValidationError({"rejection_reason": "Rejected quotations require a reason."})

    def __str__(self):
        return f"{self.ticket_id} - {self.vendor} - {self.amount}"


class MaintenanceCostAllocation(models.Model):
    TARGET_LANDLORD = "landlord"
    TARGET_TENANT = "tenant"
    TARGET_CHOICES = (
        (TARGET_LANDLORD, "Landlord"),
        (TARGET_TENANT, "Tenant"),
    )

    quotation = models.ForeignKey(
        MaintenanceQuotation,
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    target = models.CharField(max_length=20, choices=TARGET_CHOICES)
    landlord = models.ForeignKey(
        Landlord,
        on_delete=models.PROTECT,
        related_name="maintenance_cost_allocations",
        blank=True,
        null=True,
    )
    tenant = models.ForeignKey(
        "Tenant",
        on_delete=models.PROTECT,
        related_name="maintenance_cost_allocations",
        blank=True,
        null=True,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField(blank=True)
    allocated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_maintenance_cost_allocations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="users_maintenanceallocation_amount_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if self.amount <= 0:
            raise ValidationError({"amount": "Allocated amount must be positive."})
        if self.target == self.TARGET_LANDLORD:
            if self.landlord_id is None or self.tenant_id is not None:
                raise ValidationError({"landlord": "Landlord allocations require only a landlord."})
            if self.landlord_id != self.quotation.ticket.property.landlord_id:
                raise ValidationError(
                    {"landlord": "The landlord must own the ticket property."}
                )
        elif self.target == self.TARGET_TENANT:
            if self.tenant_id is None or self.landlord_id is not None:
                raise ValidationError({"tenant": "Tenant allocations require only a tenant."})
            if self.tenant.branch_id != self.quotation.ticket.property.branch_id:
                raise ValidationError({"tenant": "The tenant must belong to the ticket branch."})
        else:
            raise ValidationError({"target": "This allocation target is not supported."})

    def __str__(self):
        return f"{self.quotation_id} - {self.target} - {self.amount}"


class PropertyExpense(models.Model):
    RECURRENCE_ONE_OFF = "one_off"
    RECURRENCE_MONTHLY = "monthly"
    RECURRENCE_QUARTERLY = "quarterly"
    RECURRENCE_ANNUAL = "annual"
    RECURRENCE_CHOICES = (
        (RECURRENCE_ONE_OFF, "One-off"),
        (RECURRENCE_MONTHLY, "Monthly"),
        (RECURRENCE_QUARTERLY, "Quarterly"),
        (RECURRENCE_ANNUAL, "Annual"),
    )
    STATUS_SUBMITTED = "submitted"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = (
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    )

    property = models.ForeignKey(
        Property,
        on_delete=models.PROTECT,
        related_name="expenses",
    )
    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.PROTECT,
        related_name="expenses",
        blank=True,
        null=True,
    )
    category = models.CharField(max_length=100)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    recurrence = models.CharField(max_length=20, choices=RECURRENCE_CHOICES, default=RECURRENCE_ONE_OFF)
    effective_from = models.DateField()
    effective_to = models.DateField(blank=True, null=True)
    next_due_date = models.DateField(blank=True, null=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_property_expenses",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_property_expenses",
        blank=True,
        null=True,
    )
    approved_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SUBMITTED)
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-effective_from", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="users_propertyexpense_amount_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if self.amount <= 0:
            raise ValidationError({"amount": "Expense amount must be positive."})
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({"effective_to": "The expense end date cannot be before its start date."})
        if self.recurrence == self.RECURRENCE_ONE_OFF and self.next_due_date is not None:
            raise ValidationError({"next_due_date": "One-off expenses cannot have a next due date."})
        if self.recurrence != self.RECURRENCE_ONE_OFF and self.next_due_date is None:
            raise ValidationError({"next_due_date": "Recurring expenses require a next due date."})
        if self.vendor_id and self.vendor.branch_id != self.property.branch_id:
            raise ValidationError({"vendor": "The vendor must belong to the property branch."})
        if self.status == self.STATUS_APPROVED and (
            self.approved_by_id is None or self.approved_at is None
        ):
            raise ValidationError({"approved_by": "Approved expenses require an approver and approval time."})
        if self.status == self.STATUS_REJECTED and not self.rejection_reason.strip():
            raise ValidationError({"rejection_reason": "Rejected expenses require a reason."})

    def __str__(self):
        return f"{self.property} - {self.description} - {self.amount}"


class Announcement(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_PUBLISHED, "Published"),
        (STATUS_ARCHIVED, "Archived"),
    )

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="announcements")
    property = models.ForeignKey(
        Property,
        on_delete=models.PROTECT,
        related_name="announcements",
        blank=True,
        null=True,
    )
    title = models.CharField(max_length=255)
    body = models.TextField()
    publish_at = models.DateTimeField(blank=True, null=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_announcements",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="published_announcements",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-publish_at", "-created_at", "-id"]
        indexes = [
            models.Index(fields=["branch", "status", "publish_at"]),
            models.Index(fields=["property", "status", "publish_at"]),
        ]

    def clean(self):
        super().clean()
        if self.property_id and self.property.branch_id != self.branch_id:
            raise ValidationError({"property": "The property must belong to the announcement branch."})
        if self.expires_at and self.publish_at and self.expires_at <= self.publish_at:
            raise ValidationError({"expires_at": "The expiry must be after publication."})
        if self.status == self.STATUS_PUBLISHED and (
            self.publish_at is None or self.published_by_id is None
        ):
            raise ValidationError({"published_by": "Published announcements require publication details."})

    def __str__(self):
        return self.title


class Block(models.Model):
    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="blocks")
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["property", "name"], name="users_block_property_name_unique")
        ]

    def __str__(self):
        return f"{self.property} - {self.name}"


class Floor(models.Model):
    block = models.ForeignKey(Block, on_delete=models.PROTECT, related_name="floors")
    name = models.CharField(max_length=100)
    level = models.PositiveIntegerField()

    class Meta:
        ordering = ["level", "id"]
        constraints = [
            models.UniqueConstraint(fields=["block", "level"], name="users_floor_block_level_unique")
        ]

    def __str__(self):
        return f"{self.block} - {self.name}"


class PropertyDocument(models.Model):
    property = models.ForeignKey(
        Property,
        on_delete=models.PROTECT,
        related_name="documents",
    )
    document_type = models.CharField(max_length=100)
    name = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=500)
    metadata = models.JSONField(default=dict, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_property_documents",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.name


class PropertyInspection(models.Model):
    STATUS_SCHEDULED = "scheduled"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_IN_PROGRESS, "In progress"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    )

    property = models.ForeignKey(
        Property,
        on_delete=models.PROTECT,
        related_name="inspections",
    )
    unit = models.ForeignKey(
        "Unit",
        on_delete=models.PROTECT,
        related_name="inspections",
        blank=True,
        null=True,
    )
    move_out_notice = models.ForeignKey(
        "MoveOutNotice",
        on_delete=models.PROTECT,
        related_name="inspections",
        blank=True,
        null=True,
    )
    title = models.CharField(max_length=255)
    inspection_type = models.CharField(max_length=100)
    scheduled_for = models.DateTimeField()
    responsible_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="responsible_property_inspections",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_property_inspections",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SCHEDULED)
    checklist = models.JSONField(default=list, blank=True)
    photos = models.JSONField(default=list, blank=True)
    actual_move_out_date = models.DateField(blank=True, null=True)
    final_readings = models.JSONField(default=list, blank=True)
    closing_utility_invoice = models.ForeignKey(
        "Invoice",
        on_delete=models.PROTECT,
        related_name="move_out_inspections",
        blank=True,
        null=True,
    )
    damage_items = models.JSONField(default=list, blank=True)
    maintenance_required = models.BooleanField(default=False)
    findings = models.TextField(blank=True)
    recommendations = models.TextField(blank=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    share_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    shared_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheduled_for", "id"]
        indexes = [
            models.Index(fields=["property", "scheduled_for"]),
            models.Index(fields=["responsible_staff", "status"]),
        ]

    def clean(self):
        super().clean()
        if self.unit_id and self.unit.property_id != self.property_id:
            raise ValidationError({"unit": "The unit must belong to the inspected property."})
        if not isinstance(self.checklist, list):
            raise ValidationError({"checklist": "Checklist must be an array."})
        if not isinstance(self.photos, list):
            raise ValidationError({"photos": "Photos must be an array."})
        if self.status == self.STATUS_COMPLETED and self.completed_at is None:
            raise ValidationError({"completed_at": "Completed inspections require a completion time."})

    def __str__(self):
        return f"{self.property} - {self.title} ({self.scheduled_for:%Y-%m-%d})"


class Unit(models.Model):
    STATUS_DRAFT = "Draft"
    STATUS_VACANT = "Vacant"
    STATUS_BOOKED = "Booked"
    STATUS_OCCUPIED = "Occupied"
    STATUS_NOTICE_GIVEN = "Notice Given"
    STATUS_DEPOSIT_PENDING = "Deposit Pending"
    STATUS_MAINTENANCE = "Maintenance"
    STATUS_ARCHIVED = "Archived"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_VACANT, "Vacant"),
        (STATUS_BOOKED, "Booked"),
        (STATUS_OCCUPIED, "Occupied"),
        (STATUS_NOTICE_GIVEN, "Notice Given"),
        (STATUS_DEPOSIT_PENDING, "Deposit Pending"),
        (STATUS_MAINTENANCE, "Maintenance"),
        (STATUS_ARCHIVED, "Archived"),
    )
    ALLOWED_STATUS_TRANSITIONS = {
        STATUS_DRAFT: {STATUS_VACANT, STATUS_ARCHIVED},
        STATUS_VACANT: {STATUS_BOOKED, STATUS_OCCUPIED, STATUS_NOTICE_GIVEN, STATUS_MAINTENANCE, STATUS_ARCHIVED},
        STATUS_BOOKED: {STATUS_VACANT, STATUS_OCCUPIED, STATUS_MAINTENANCE, STATUS_ARCHIVED},
        STATUS_OCCUPIED: {STATUS_NOTICE_GIVEN, STATUS_MAINTENANCE, STATUS_ARCHIVED},
        STATUS_NOTICE_GIVEN: {
            STATUS_VACANT,
            STATUS_OCCUPIED,
            STATUS_DEPOSIT_PENDING,
            STATUS_MAINTENANCE,
            STATUS_ARCHIVED,
        },
        STATUS_DEPOSIT_PENDING: {STATUS_VACANT, STATUS_MAINTENANCE, STATUS_ARCHIVED},
        STATUS_MAINTENANCE: {STATUS_VACANT, STATUS_OCCUPIED, STATUS_ARCHIVED},
        STATUS_ARCHIVED: set(),
    }

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="units")
    unit_type_definition = models.ForeignKey(
        PropertyUnitType,
        on_delete=models.PROTECT,
        related_name="units",
        blank=True,
        null=True,
    )
    floor = models.ForeignKey(
        Floor,
        on_delete=models.PROTECT,
        related_name="units",
        blank=True,
        null=True,
    )
    code = models.CharField(max_length=100)
    unit_type = models.CharField(max_length=100)
    bedrooms = models.PositiveSmallIntegerField(default=0)
    characteristics = models.JSONField(default=dict, blank=True)
    meters = models.JSONField(default=dict, blank=True)
    listing_enabled = models.BooleanField(default=False)
    is_migrated = models.BooleanField(default=False)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "code"],
                name="users_unit_property_code_unique",
            )
        ]

    def clean(self):
        super().clean()
        if (
            self.unit_type_definition_id
            and self.unit_type_definition.property_id != self.property_id
        ):
            raise ValidationError(
                {"unit_type_definition": "The unit type definition must belong to the unit's property."}
            )
        if self.floor_id and self.floor.block.property_id != self.property_id:
            raise ValidationError({"floor": "The floor must belong to the unit's property."})
        if self.listing_enabled and self.status not in {
            self.STATUS_VACANT,
            self.STATUS_NOTICE_GIVEN,
        }:
            raise ValidationError(
                {"listing_enabled": "Only vacant or notice-given units can be listed."}
            )

    def transition_to(self, new_status):
        if new_status not in dict(self.STATUS_CHOICES):
            raise ValidationError({"status": "This unit status is not supported."})
        if new_status == self.status:
            return
        if new_status not in self.ALLOWED_STATUS_TRANSITIONS[self.status]:
            raise ValidationError(
                {"status": f"Units cannot transition from {self.status} to {new_status}."}
            )
        self.status = new_status
        if new_status not in {self.STATUS_VACANT, self.STATUS_NOTICE_GIVEN}:
            self.listing_enabled = False

    def __str__(self):
        return f"{self.property} - {self.code}"


class UnitCharge(models.Model):
    CHARGE_RENT = "rent"
    CHARGE_DEPOSIT = "deposit"
    CHARGE_RECURRING = "recurring"
    CHARGE_TYPE_CHOICES = (
        (CHARGE_RENT, "Rent"),
        (CHARGE_DEPOSIT, "Deposit"),
        (CHARGE_RECURRING, "Recurring"),
    )

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="charges")
    charge_type = models.CharField(max_length=20, choices=CHARGE_TYPE_CHOICES)
    charge_code = models.CharField(max_length=50, default="default")
    name = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    effective_from = models.DateField()
    effective_to = models.DateField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["effective_from", "charge_type", "name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="users_unitcharge_amount_nonnegative",
            ),
        ]

    def clean(self):
        super().clean()
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError(
                {"effective_to": "The charge end date cannot be before its start date."}
            )
        if self.effective_to is not None:
            overlapping = UnitCharge.objects.filter(
                unit=self.unit,
                charge_type=self.charge_type,
                charge_code=self.charge_code,
                effective_from__lte=self.effective_to,
            ).filter(
                models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=self.effective_from)
            ).exclude(pk=self.pk)
        else:
            overlapping = UnitCharge.objects.filter(
                unit=self.unit,
                charge_type=self.charge_type,
                charge_code=self.charge_code,
                effective_from__lte=self.effective_from,
            ).filter(
                models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=self.effective_from)
            ).exclude(pk=self.pk)
        if overlapping.exists():
            raise ValidationError(
                {"effective_from": "This charge period overlaps an existing charge of the same type."}
            )

    def __str__(self):
        return f"{self.unit} - {self.name} ({self.effective_from})"


class UtilityConfiguration(models.Model):
    UTILITY_WATER = "water"
    UTILITY_ELECTRICITY = "electricity"
    UTILITY_OTHER = "other"
    UTILITY_CHOICES = (
        (UTILITY_WATER, "Water"),
        (UTILITY_ELECTRICITY, "Electricity"),
        (UTILITY_OTHER, "Other"),
    )
    METHOD_FIXED = "fixed"
    METHOD_METERED = "metered"
    BILLING_METHOD_CHOICES = (
        (METHOD_FIXED, "Fixed"),
        (METHOD_METERED, "Metered"),
    )

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="utilities")
    utility_type = models.CharField(max_length=30, choices=UTILITY_CHOICES)
    name = models.CharField(max_length=100)
    billing_method = models.CharField(max_length=20, choices=BILLING_METHOD_CHOICES)
    fixed_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    rate_per_unit = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    minimum_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["effective_from", "utility_type", "id"]

    def clean(self):
        super().clean()
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({"effective_to": "The utility period cannot end before it starts."})
        if self.minimum_charge < 0:
            raise ValidationError({"minimum_charge": "The minimum charge cannot be negative."})
        if self.billing_method == self.METHOD_FIXED:
            if self.fixed_amount is None or self.fixed_amount < 0:
                raise ValidationError({"fixed_amount": "Fixed utilities require a non-negative fixed amount."})
            if self.rate_per_unit is not None:
                raise ValidationError({"rate_per_unit": "Fixed utilities cannot have a per-unit rate."})
        elif self.billing_method == self.METHOD_METERED:
            if self.rate_per_unit is None or self.rate_per_unit < 0:
                raise ValidationError({"rate_per_unit": "Metered utilities require a non-negative per-unit rate."})
            if self.fixed_amount is not None:
                raise ValidationError({"fixed_amount": "Metered utilities cannot have a fixed amount."})
        overlapping = UtilityConfiguration.objects.filter(
            unit=self.unit,
            utility_type=self.utility_type,
            effective_from__lte=self.effective_to or date.max,
        ).filter(
            models.Q(effective_to__isnull=True)
            | models.Q(effective_to__gte=self.effective_from)
        ).exclude(pk=self.pk)
        if overlapping.exists():
            raise ValidationError({"effective_from": "This utility period overlaps an existing configuration."})

    def __str__(self):
        return f"{self.unit} - {self.name} ({self.effective_from})"


class MeterReading(models.Model):
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="meter_readings")
    meter = models.CharField(max_length=100)
    period = models.DateField()
    previous_value = models.DecimalField(max_digits=14, decimal_places=4)
    current_value = models.DecimalField(max_digits=14, decimal_places=4)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_meter_readings",
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    photo_key = models.CharField(max_length=500, blank=True)
    photo_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-period", "-submitted_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["unit", "meter", "period"],
                name="users_meterreading_unit_meter_period_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(previous_value__gte=0)
                & models.Q(current_value__gte=0)
                & models.Q(current_value__gte=models.F("previous_value")),
                name="users_meterreading_values_valid",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.meter.strip():
            raise ValidationError({"meter": "A meter identifier is required."})
        if not self.previous_value.is_finite() or not self.current_value.is_finite():
            raise ValidationError({"current_value": "Meter values must be finite numbers."})
        if self.previous_value < 0 or self.current_value < 0:
            raise ValidationError({"current_value": "Meter values cannot be negative."})
        if self.current_value < self.previous_value:
            raise ValidationError(
                {"current_value": "The current reading cannot be lower than the previous reading."}
            )
        if self.photo_key:
            content_type = self.photo_metadata.get("content_type")
            if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                raise ValidationError(
                    {"photo_metadata": "Meter evidence must be a JPEG, PNG, or WebP image."}
                )
            size_bytes = self.photo_metadata.get("size_bytes")
            if (
                not isinstance(size_bytes, int)
                or isinstance(size_bytes, bool)
                or size_bytes < 0
                or size_bytes > 10 * 1024 * 1024
            ):
                raise ValidationError(
                    {"photo_metadata": "Meter evidence size must be an integer from 0 to 10 MB."}
                )

    def __str__(self):
        return f"{self.unit} - {self.meter} ({self.period})"


class UnitReservation(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_RELEASED = "released"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = (
        (STATUS_ACTIVE, "Active"),
        (STATUS_RELEASED, "Released"),
        (STATUS_EXPIRED, "Expired"),
    )

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="reservations")
    applicant = models.ForeignKey(
        "Tenant",
        on_delete=models.PROTECT,
        related_name="unit_reservations",
    )
    expires_at = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
    )
    listing_enabled_before_reservation = models.BooleanField(default=False)
    reserved_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_unit_reservations",
    )

    class Meta:
        ordering = ["-reserved_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["unit"],
                condition=models.Q(status="active"),
                name="users_unitreservation_one_active_per_unit",
            ),
        ]

    def clean(self):
        super().clean()
        if self.expires_at <= (self.reserved_at or timezone.now()):
            raise ValidationError({"expires_at": "The reservation must expire in the future."})
        if self.status == self.STATUS_ACTIVE and self.released_at is not None:
            raise ValidationError({"released_at": "Active reservations cannot have a release time."})
        if self.applicant_id and self.unit_id:
            if self.applicant.branch_id != self.unit.property.branch_id:
                raise ValidationError(
                    {"applicant": "The applicant must belong to the unit's branch."}
                )
        if self.status != self.STATUS_ACTIVE and self.released_at is None:
            raise ValidationError({"released_at": "Released and expired reservations need a release time."})

    def release(self, status=STATUS_RELEASED, released_at=None):
        if status not in {self.STATUS_RELEASED, self.STATUS_EXPIRED}:
            raise ValidationError({"status": "A reservation can only be released or expired."})
        if self.status != self.STATUS_ACTIVE:
            return
        self.status = status
        self.released_at = released_at or timezone.now()

    def __str__(self):
        return f"{self.unit} reserved for {self.applicant} ({self.status})"


class MeterReadingDraft(models.Model):
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="meter_reading_drafts")
    meter = models.CharField(max_length=100)
    period = models.DateField()
    previous_value = models.DecimalField(max_digits=14, decimal_places=4)
    current_value = models.DecimalField(max_digits=14, decimal_places=4)
    photo_key = models.CharField(max_length=500, blank=True)
    photo_metadata = models.JSONField(default=dict, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="meter_reading_drafts",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["unit__property_id", "unit__code", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["unit", "meter", "period"],
                name="users_meterreadingdraft_unit_meter_period_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(previous_value__gte=0)
                & models.Q(current_value__gte=models.F("previous_value")),
                name="users_meterreadingdraft_values_valid",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.meter.strip():
            raise ValidationError({"meter": "A meter identifier is required."})
        if not self.previous_value.is_finite() or not self.current_value.is_finite():
            raise ValidationError({"current_value": "Meter values must be finite numbers."})
        if self.previous_value < 0 or self.current_value < self.previous_value:
            raise ValidationError(
                {"current_value": "The current reading cannot be lower than the previous reading."}
            )
        if self.photo_key:
            content_type = self.photo_metadata.get("content_type")
            if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                raise ValidationError(
                    {"photo_metadata": "Meter evidence must be a JPEG, PNG, or WebP image."}
                )
            size_bytes = self.photo_metadata.get("size_bytes")
            if (
                not isinstance(size_bytes, int)
                or isinstance(size_bytes, bool)
                or size_bytes < 1
                or size_bytes > 5 * 1024 * 1024
            ):
                raise ValidationError(
                    {"photo_metadata": "Meter evidence size must be an integer from 1 byte to 5 MB."}
                )


class Tenancy(models.Model):
    TYPE_FIXED_TERM = "fixed_term"
    TYPE_MONTH_TO_MONTH = "month_to_month"
    TENANCY_TYPE_CHOICES = (
        (TYPE_FIXED_TERM, "Fixed term"),
        (TYPE_MONTH_TO_MONTH, "Month to month"),
    )
    STATUS_ACTIVE = "active"
    STATUS_ENDED = "ended"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_ACTIVE, "Active"),
        (STATUS_ENDED, "Ended"),
        (STATUS_CANCELLED, "Cancelled"),
    )

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="tenancies")
    tenant = models.ForeignKey("Tenant", on_delete=models.PROTECT, related_name="tenancies")
    tenancy_type = models.CharField(max_length=30, choices=TENANCY_TYPE_CHOICES)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    base_water_meter_reading = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        blank=True,
        null=True,
    )
    due_day = models.PositiveSmallIntegerField(default=1)
    grace_period_days = models.PositiveSmallIntegerField(default=0)
    notice_period_days = models.PositiveSmallIntegerField(default=30)
    rent_escalation_terms = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(base_water_meter_reading__isnull=True)
                    | models.Q(base_water_meter_reading__gte=0)
                ),
                name="users_tenancy_base_water_reading_nonnegative",
            ),
        ]

    def clean(self):
        super().clean()
        if self.tenancy_type not in dict(self.TENANCY_TYPE_CHOICES):
            raise ValidationError({"tenancy_type": "This tenancy type is not supported."})
        if self.tenancy_type == self.TYPE_FIXED_TERM and self.end_date is None:
            raise ValidationError({"end_date": "Fixed-term tenancies require an end date."})
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "The tenancy end date cannot be before its start date."})
        if self.base_water_meter_reading is not None and (
            not self.base_water_meter_reading.is_finite()
            or self.base_water_meter_reading < 0
        ):
            raise ValidationError(
                {"base_water_meter_reading": "The base water reading must be a finite, non-negative value."}
            )
        if not 1 <= self.due_day <= 31:
            raise ValidationError({"due_day": "The due day must be between 1 and 31."})
        if self.tenant_id and self.unit_id:
            if self.tenant.branch_id != self.unit.property.branch_id:
                raise ValidationError({"tenant": "The tenant must belong to the unit's branch."})
        if not isinstance(self.rent_escalation_terms, dict):
            raise ValidationError({"rent_escalation_terms": "Escalation terms must be an object."})
        if self.status == self.STATUS_ACTIVE:
            periods = Tenancy.objects.filter(
                unit=self.unit,
                status=self.STATUS_ACTIVE,
                start_date__lte=self.end_date or date.max,
            ).filter(
                models.Q(end_date__isnull=True) | models.Q(end_date__gte=self.start_date)
            ).exclude(pk=self.pk)
            if periods.exists():
                raise ValidationError({"unit": "The unit already has an overlapping active tenancy."})

    def __str__(self):
        return f"{self.unit} - {self.tenant} ({self.start_date})"


class ViewingLead(models.Model):
    STATUS_NEW = "new"
    STATUS_VIEWING_BOOKED = "viewing_booked"
    STATUS_APPLIED = "applied"
    STATUS_CONVERTED = "converted"
    STATUS_LOST = "lost"
    STATUS_CHOICES = (
        (STATUS_NEW, "New inquiry"),
        (STATUS_VIEWING_BOOKED, "Viewing booked"),
        (STATUS_APPLIED, "Applied"),
        (STATUS_CONVERTED, "Converted"),
        (STATUS_LOST, "Lost"),
    )

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="viewing_leads")
    property = models.ForeignKey(
        Property,
        on_delete=models.PROTECT,
        related_name="viewing_leads",
        blank=True,
        null=True,
    )
    unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        related_name="viewing_leads",
        blank=True,
        null=True,
    )
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=30)
    email = models.EmailField(blank=True)
    preference = models.TextField(blank=True)
    budget = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_NEW)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_viewing_leads",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(budget__isnull=True) | models.Q(budget__gte=0),
                name="users_viewinglead_budget_nonnegative",
            ),
        ]

    def clean(self):
        super().clean()
        if self.property_id and self.property.branch_id != self.branch_id:
            raise ValidationError({"property": "The property must belong to the lead branch."})
        if self.unit_id and (
            self.unit.property.branch_id != self.branch_id
            or (self.property_id and self.unit.property_id != self.property_id)
        ):
            raise ValidationError({"unit": "The unit must belong to the lead property and branch."})


class PropertyViewing(models.Model):
    STATUS_SCHEDULED = "scheduled"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    )

    lead = models.ForeignKey(ViewingLead, on_delete=models.PROTECT, related_name="viewings")
    scheduled_for = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SCHEDULED)
    notes = models.TextField(blank=True)
    scheduled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="scheduled_property_viewings",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assigned_property_viewings",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheduled_for", "id"]

    def clean(self):
        super().clean()
        if self.status not in dict(self.STATUS_CHOICES):
            raise ValidationError({"status": "This viewing status is not supported."})
        if self.assigned_to_id:
            profile = UserProfile.objects.filter(user_id=self.assigned_to_id).first()
            if profile is None or not profile.accessible_branches().filter(pk=self.lead.branch_id).exists():
                raise ValidationError({"assigned_to": "The assignee must have access to the lead branch."})


class InvoiceNumberSequence(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="invoice_sequences")
    year = models.PositiveSmallIntegerField()
    next_number = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "year"],
                name="users_invoice_sequence_branch_year_unique",
            ),
        ]


class Invoice(models.Model):
    TYPE_STANDARD = "standard"
    TYPE_MOVE_OUT_UTILITY = "move_out_utility"
    TYPE_CHOICES = (
        (TYPE_STANDARD, "Standard"),
        (TYPE_MOVE_OUT_UTILITY, "Move-out utility"),
    )
    STATUS_DRAFT = "draft"
    STATUS_ISSUED = "issued"
    STATUS_VOID = "void"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_ISSUED, "Issued"),
        (STATUS_VOID, "Void"),
    )

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="invoices")
    tenancy = models.ForeignKey(Tenancy, on_delete=models.PROTECT, related_name="invoices")
    number = models.CharField(max_length=50, unique=True)
    period_start = models.DateField()
    period_end = models.DateField()
    due_date = models.DateField()
    invoice_type = models.CharField(
        max_length=30,
        choices=TYPE_CHOICES,
        default=TYPE_STANDARD,
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ISSUED)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    issued_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_start", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenancy", "period_start", "period_end", "invoice_type"],
                name="users_invoice_tenancy_period_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(total__gte=0),
                name="users_invoice_total_nonnegative",
            ),
        ]

    def clean(self):
        super().clean()
        if self.period_end < self.period_start:
            raise ValidationError({"period_end": "The invoice period cannot end before it starts."})
        if self.tenancy_id and self.branch_id != self.tenancy.unit.property.branch_id:
            raise ValidationError({"branch": "The invoice branch must match the tenancy branch."})

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.status == self.STATUS_ISSUED and self.issued_at is None:
                self.issued_at = timezone.now()
            return super().save(*args, **kwargs)
        existing_status = type(self).objects.values_list("status", flat=True).get(pk=self.pk)
        if existing_status != self.STATUS_DRAFT:
            raise ValidationError("Issued invoices are immutable; use a credit note workflow.")
        if self.status == self.STATUS_ISSUED and self.issued_at is None:
            self.issued_at = timezone.now()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self._state.adding and self.status != self.STATUS_DRAFT:
            raise ValidationError("Issued invoices cannot be deleted.")
        return super().delete(*args, **kwargs)


class InvoiceWaiverRequest(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending Director approval"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    )

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name="waiver_requests",
        blank=True,
        null=True,
    )
    unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        related_name="invoice_waiver_requests",
    )
    period_start = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_invoice_waivers",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="decided_invoice_waivers",
        blank=True,
        null=True,
    )
    decided_at = models.DateTimeField(blank=True, null=True)
    decision_reason = models.TextField(blank=True)
    director_notified_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="users_invoice_waiver_amount_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="pending", decided_by__isnull=True, decided_at__isnull=True)
                    | models.Q(status__in=("approved", "rejected"), decided_by__isnull=False, decided_at__isnull=False)
                ),
                name="users_invoice_waiver_decision_consistent",
            ),
        ]
        indexes = [models.Index(fields=["unit", "period_start"], name="users_waiver_unit_period_idx")]

    def clean(self):
        super().clean()
        if not self.reason.strip():
            raise ValidationError({"reason": "A waiver reason is required."})
        if self.invoice_id and self.unit_id and self.invoice.tenancy.unit_id != self.unit_id:
            raise ValidationError({"invoice": "The waiver invoice must belong to the selected unit."})
        if self.invoice_id and self.invoice.period_start != self.period_start:
            raise ValidationError({"invoice": "The waiver invoice must match the selected billing period."})
        if self.invoice_id and self.invoice.status not in {
            Invoice.STATUS_DRAFT,
            Invoice.STATUS_ISSUED,
        }:
            raise ValidationError({"invoice": "Waivers can only be requested for draft or issued invoices."})
        if not self.unit_id:
            raise ValidationError({"unit": "A unit is required for a waiver request."})
        if self.amount <= 0:
            raise ValidationError({"amount": "Waiver amount must be positive."})

    def save(self, *args, **kwargs):
        if self.invoice_id:
            self.unit_id = self.invoice.tenancy.unit_id
            self.period_start = self.invoice.period_start
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"unit", "period_start"}
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Unit {self.unit_id} waiver for {self.period_start} ({self.amount}, {self.status})"


class BillingNotification(models.Model):
    TYPE_CARETAKER_READINGS = "caretaker_readings"
    TYPE_AGENT_DRAFTS = "agent_drafts"
    TYPE_CHOICES = (
        (TYPE_CARETAKER_READINGS, "Caretaker water readings"),
        (TYPE_AGENT_DRAFTS, "Property officer invoice drafts"),
    )

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="billing_notifications")
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="billing_notifications",
    )
    period_start = models.DateField()
    notification_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["property", "recipient", "period_start", "notification_type"],
                name="users_billingnotification_recipient_unique",
            ),
        ]


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="lines")
    charge_type = models.CharField(max_length=20)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="users_invoice_line_amount_nonnegative",
            ),
        ]


class Payment(models.Model):
    SOURCE_MANUAL = "manual"
    SOURCE_MPESA = "mpesa"
    SOURCE_BANK = "bank"
    SOURCE_CHEQUE = "cheque"
    SOURCE_CASH = "cash"
    SOURCE_CHOICES = (
        (SOURCE_MANUAL, "Manual"),
        (SOURCE_MPESA, "M-Pesa"),
        (SOURCE_BANK, "Bank"),
        (SOURCE_CHEQUE, "Cheque"),
        (SOURCE_CASH, "Cash"),
    )
    INSTRUMENT_NOT_APPLICABLE = "not_applicable"
    INSTRUMENT_PENDING = "pending"
    INSTRUMENT_CLEARED = "cleared"
    INSTRUMENT_BOUNCED = "bounced"
    INSTRUMENT_COLLECTED = "collected"
    INSTRUMENT_STATUS_CHOICES = (
        (INSTRUMENT_NOT_APPLICABLE, "Not applicable"),
        (INSTRUMENT_PENDING, "Pending clearance"),
        (INSTRUMENT_CLEARED, "Cleared"),
        (INSTRUMENT_BOUNCED, "Bounced"),
        (INSTRUMENT_COLLECTED, "Collected"),
    )
    STATUS_UNMATCHED = "unmatched"
    STATUS_PARTIALLY_ALLOCATED = "partially_allocated"
    STATUS_ALLOCATED = "allocated"
    STATUS_CREDIT = "credit"
    STATUS_CHOICES = (
        (STATUS_UNMATCHED, "Unmatched"),
        (STATUS_PARTIALLY_ALLOCATED, "Partially allocated"),
        (STATUS_ALLOCATED, "Allocated"),
        (STATUS_CREDIT, "Credit"),
    )

    tenant = models.ForeignKey(
        "users.Tenant",
        on_delete=models.PROTECT,
        related_name="payments",
        blank=True,
        null=True,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=100, unique=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    account_reference = models.CharField(max_length=100, blank=True)
    payer_phone = models.CharField(max_length=30, blank=True)
    instrument_status = models.CharField(
        max_length=30,
        choices=INSTRUMENT_STATUS_CHOICES,
        default=INSTRUMENT_NOT_APPLICABLE,
    )
    cheque_number = models.CharField(max_length=100, blank=True)
    cheque_bank = models.CharField(max_length=150, blank=True)
    clearing_date = models.DateField(blank=True, null=True)
    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="collected_cash_payments",
        blank=True,
        null=True,
    )
    notes = models.TextField(blank=True)
    received_at = models.DateTimeField()
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_UNMATCHED)
    allocated_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="users_payment_amount_positive"),
            models.CheckConstraint(
                condition=models.Q(allocated_amount__gte=0),
                name="users_payment_allocated_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(credit_amount__gte=0),
                name="users_payment_credit_nonnegative",
            ),
        ]


class PaymentAllocation(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payment_allocations")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["invoice__due_date", "invoice_id", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="users_payment_allocation_amount_positive",
            ),
        ]


class TenantCredit(models.Model):
    tenant = models.ForeignKey("users.Tenant", on_delete=models.PROTECT, related_name="credits")
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="credit")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    remaining_amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="users_tenant_credit_amount_positive"),
            models.CheckConstraint(
                condition=models.Q(remaining_amount__gte=0),
                name="users_tenant_credit_remaining_nonnegative",
            ),
        ]


class Receipt(models.Model):
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="receipt")
    number = models.CharField(max_length=50, unique=True)
    tenant = models.ForeignKey("users.Tenant", on_delete=models.PROTECT, related_name="receipts")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_at", "-id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Receipts are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Receipts cannot be deleted.")


class DepositAccount(models.Model):
    tenancy = models.OneToOneField(Tenancy, on_delete=models.PROTECT, related_name="deposit_account")
    target_amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(target_amount__gte=0),
                name="users_deposit_account_target_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(balance__gte=0),
                name="users_deposit_account_balance_nonnegative",
            ),
        ]


class DepositEntry(models.Model):
    TYPE_RECEIPT = "receipt"
    TYPE_TOP_UP = "top_up"
    TYPE_REFUND = "refund"
    TYPE_DEDUCTION = "deduction"
    TYPE_ADJUSTMENT = "adjustment"
    TYPE_CHOICES = (
        (TYPE_RECEIPT, "Receipt"),
        (TYPE_TOP_UP, "Top-up"),
        (TYPE_REFUND, "Refund"),
        (TYPE_DEDUCTION, "Deduction"),
        (TYPE_ADJUSTMENT, "Adjustment"),
    )
    account = models.ForeignKey(DepositAccount, on_delete=models.PROTECT, related_name="entries")
    entry_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="deposit_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="users_deposit_entry_amount_positive",
            ),
        ]


class MoveOutNotice(models.Model):
    STATUS_SUBMITTED = "submitted"
    STATUS_ACCEPTED = "accepted"
    STATUS_REJECTED = "rejected"
    STATUS_WITHDRAWN = "withdrawn"
    STATUS_CHOICES = (
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_WITHDRAWN, "Withdrawn"),
    )

    tenancy = models.ForeignKey(Tenancy, on_delete=models.PROTECT, related_name="move_out_notices")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="move_out_notices",
    )
    proposed_move_out_date = models.DateField()
    previous_tenancy_end_date = models.DateField(blank=True, null=True)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SUBMITTED)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reviewed_move_out_notices",
        blank=True,
        null=True,
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    review_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenancy"],
                condition=models.Q(status__in=["submitted", "accepted"]),
                name="users_moveoutnotice_one_active_per_tenancy",
            ),
        ]

    def clean(self):
        super().clean()
        if self.proposed_move_out_date < self.tenancy.start_date:
            raise ValidationError(
                {"proposed_move_out_date": "The move-out date cannot precede the tenancy start."}
            )
        if self.status in {self.STATUS_ACCEPTED, self.STATUS_REJECTED} and (
            self.reviewed_by_id is None or self.reviewed_at is None
        ):
            raise ValidationError({"reviewed_by": "Reviewed notices require review details."})


class MoveOutSettlement(models.Model):
    STATUS_AWAITING_APPROVAL = "awaiting_approval"
    STATUS_REJECTED = "rejected"
    STATUS_RECALCULATION_REQUIRED = "recalculation_required"
    STATUS_APPROVED = "approved"
    STATUS_PAID = "paid"
    STATUS_CHOICES = (
        (STATUS_AWAITING_APPROVAL, "Awaiting approval"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_RECALCULATION_REQUIRED, "Recalculation required"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_PAID, "Paid"),
    )

    tenancy = models.OneToOneField(Tenancy, on_delete=models.PROTECT, related_name="move_out_settlement")
    notice = models.ForeignKey(MoveOutNotice, on_delete=models.PROTECT, related_name="settlements")
    exit_inspection = models.ForeignKey(
        PropertyInspection,
        on_delete=models.PROTECT,
        related_name="move_out_settlements",
    )
    actual_move_out_date = models.DateField()
    final_readings = models.JSONField(default=list, blank=True)
    damage_items = models.JSONField(default=list, blank=True)
    maintenance_required = models.BooleanField(default=False)
    deposit_balance = models.DecimalField(max_digits=12, decimal_places=2)
    arrears_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    utility_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    damages_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deduction_from_deposit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    shortfall_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    invoice_balance_snapshot = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_AWAITING_APPROVAL,
    )
    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="prepared_move_out_settlements",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reviewed_move_out_settlements",
        blank=True,
        null=True,
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    approval_hash = models.CharField(max_length=64, blank=True)
    rejection_reason = models.TextField(blank=True)
    payout_reference = models.CharField(max_length=100, blank=True, null=True, unique=True)
    payout_date = models.DateField(blank=True, null=True)
    payout_recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_move_out_payouts",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(deposit_balance__gte=0)
                    & models.Q(arrears_amount__gte=0)
                    & models.Q(utility_amount__gte=0)
                    & models.Q(damages_amount__gte=0)
                    & models.Q(deduction_from_deposit__gte=0)
                    & models.Q(refund_amount__gte=0)
                    & models.Q(shortfall_amount__gte=0)
                ),
                name="users_moveoutsettlement_amounts_nonnegative",
            ),
        ]

    def clean(self):
        super().clean()
        if self.notice_id and (
            self.notice.tenancy_id != self.tenancy_id
            or self.notice.status != MoveOutNotice.STATUS_ACCEPTED
        ):
            raise ValidationError({"notice": "The settlement requires an accepted notice for its tenancy."})
        if self.exit_inspection_id:
            inspection = self.exit_inspection
            if (
                inspection.unit_id != self.tenancy.unit_id
                or inspection.status != PropertyInspection.STATUS_COMPLETED
                or inspection.inspection_type.lower() not in {"move_out", "exit"}
            ):
                raise ValidationError(
                    {"exit_inspection": "A completed move-out inspection for the tenancy unit is required."}
                )
        if self.actual_move_out_date < self.tenancy.start_date:
            raise ValidationError(
                {"actual_move_out_date": "The move-out date cannot precede the tenancy start."}
            )
        if not isinstance(self.final_readings, list) or not isinstance(self.damage_items, list):
            raise ValidationError(
                {"final_readings": "Settlement readings and damage items must be arrays."}
            )
        if self.status in {self.STATUS_APPROVED, self.STATUS_PAID} and (
            self.reviewed_by_id is None or self.reviewed_at is None
        ):
            raise ValidationError({"reviewed_by": "Approved settlements require review details."})
        if self.status in {self.STATUS_APPROVED, self.STATUS_PAID} and not self.approval_hash:
            raise ValidationError({"approval_hash": "Approved settlements require a verified approval hash."})
        if self.status == self.STATUS_PAID and (
            self.payout_date is None
            or self.payout_recorded_by_id is None
            or (self.refund_amount > 0 and not self.payout_reference)
        ):
            raise ValidationError({"payout_reference": "Paid settlements require a payout date and recorder; refunds also require a reference."})


class BankStatementImport(models.Model):
    STATUS_IMPORTED = "imported"
    STATUS_REVIEWED = "reviewed"
    STATUS_CHOICES = (
        (STATUS_IMPORTED, "Imported"),
        (STATUS_REVIEWED, "Reviewed"),
    )

    filename = models.CharField(max_length=255)
    file_format = models.CharField(max_length=10)
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="bank_statement_imports",
    )
    imported_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_IMPORTED)
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    invalid_rows = models.PositiveIntegerField(default=0)


class BankStatementRow(models.Model):
    STATUS_IMPORTED = "imported"
    STATUS_INVALID = "invalid"
    STATUS_MATCHED = "matched"
    STATUS_REVIEW = "review"
    STATUS_CHOICES = (
        (STATUS_IMPORTED, "Imported"),
        (STATUS_INVALID, "Invalid"),
        (STATUS_MATCHED, "Matched"),
        (STATUS_REVIEW, "Review"),
    )

    statement = models.ForeignKey(
        BankStatementImport,
        on_delete=models.PROTECT,
        related_name="rows",
    )
    row_number = models.PositiveIntegerField()
    transaction_date = models.DateField(blank=True, null=True)
    reference = models.CharField(max_length=100, blank=True)
    account_reference = models.CharField(max_length=100, blank=True)
    payer_phone = models.CharField(max_length=30, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    raw_data = models.JSONField(default=dict)
    validation_error = models.TextField(blank=True)
    suggestions = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_IMPORTED)
    payment = models.OneToOneField(
        Payment,
        on_delete=models.PROTECT,
        related_name="bank_statement_row",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ["row_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["statement", "row_number"],
                name="users_bank_statement_row_number_unique",
            ),
        ]


class MpesaCallback(models.Model):
    STATUS_RECEIVED = "received"
    STATUS_PROCESSED = "processed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_RECEIVED, "Received"),
        (STATUS_PROCESSED, "Processed"),
        (STATUS_FAILED, "Failed"),
    )

    transaction_code = models.CharField(max_length=100, unique=True, null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    payer_phone = models.CharField(max_length=30, blank=True)
    account_reference = models.CharField(max_length=100, blank=True)
    transaction_time = models.CharField(max_length=30, blank=True)
    raw_payload = models.JSONField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_RECEIVED)
    failure_reason = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-received_at", "-id"]

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount < 0:
            raise ValidationError({"amount": "Callback amount cannot be negative."})


class ArrearsPolicy(models.Model):
    branch = models.OneToOneField(Branch, on_delete=models.PROTECT, related_name="arrears_policy")
    grace_days = models.PositiveSmallIntegerField(default=0)
    penalty_type = models.CharField(
        max_length=12,
        choices=(("percentage", "Percentage"), ("flat", "Flat fee")),
        default="percentage",
    )
    penalty_rate = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    penalty_flat_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reminder_after_days = models.PositiveSmallIntegerField(default=7)
    demand_after_days = models.PositiveSmallIntegerField(default=30)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if self.penalty_type not in {"percentage", "flat"}:
            raise ValidationError({"penalty_type": "Choose percentage or flat fee."})
        if self.penalty_rate < 0 or self.penalty_rate > 100:
            raise ValidationError({"penalty_rate": "Penalty rate must be between 0 and 100."})
        if self.penalty_flat_fee < 0:
            raise ValidationError({"penalty_flat_fee": "Penalty flat fee cannot be negative."})


class ArrearsWaiver(models.Model):
    tenancy = models.ForeignKey(Tenancy, on_delete=models.PROTECT, related_name="arrears_waivers")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="arrears_waivers")
    invoice_waiver_request = models.OneToOneField(
        "InvoiceWaiverRequest",
        on_delete=models.PROTECT,
        related_name="ledger_waiver",
        blank=True,
        null=True,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField()
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_arrears_waivers",
    )
    approved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="users_waiver_amount_positive"),
        ]

    def clean(self):
        super().clean()
        if self.invoice_id and self.tenancy_id != self.invoice.tenancy_id:
            raise ValidationError({"invoice": "The waiver invoice must belong to the tenancy."})
        if not self.reason.strip():
            raise ValidationError({"reason": "A waiver reason is required."})


class ArrearsReminder(models.Model):
    CHANNEL_SMS = "sms"
    CHANNEL_WHATSAPP = "whatsapp"
    CHANNEL_CHOICES = (
        (CHANNEL_SMS, "SMS"),
        (CHANNEL_WHATSAPP, "WhatsApp"),
    )
    STAGE_BEFORE_DUE = "before_due"
    STAGE_DUE_TODAY = "due_today"
    STAGE_OVERDUE_1_30 = "overdue_1_30"
    STAGE_OVERDUE_31_60 = "overdue_31_60"
    STAGE_OVERDUE_61_90 = "overdue_61_90"
    STAGE_OVERDUE_90_PLUS = "overdue_90_plus"
    STAGE_CHOICES = (
        (STAGE_BEFORE_DUE, "Three days before due"),
        (STAGE_DUE_TODAY, "Due today"),
        (STAGE_OVERDUE_1_30, "Overdue 1-30 days"),
        (STAGE_OVERDUE_31_60, "Overdue 31-60 days"),
        (STAGE_OVERDUE_61_90, "Overdue 61-90 days"),
        (STAGE_OVERDUE_90_PLUS, "Overdue 90+ days"),
    )
    STATUS_PLANNED = "planned"
    STATUS_SUPERSEDED = "superseded"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_PLANNED, "Planned; provider not dispatched"),
        (STATUS_SUPERSEDED, "Superseded by a later occurrence"),
        (STATUS_CANCELLED, "Cancelled because balance is no longer due"),
    )

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name="reminders",
    )
    tenancy = models.ForeignKey(
        Tenancy,
        on_delete=models.PROTECT,
        related_name="arrears_reminders",
    )
    channel = models.CharField(max_length=16, choices=CHANNEL_CHOICES)
    stage = models.CharField(
        max_length=20,
        choices=STAGE_CHOICES,
        default=STAGE_OVERDUE_1_30,
    )
    scheduled_for = models.DateField()
    amount_due = models.DecimalField(max_digits=12, decimal_places=2)
    message = models.TextField(blank=True)
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PLANNED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheduled_for", "invoice_id", "channel"]
        constraints = [
            models.UniqueConstraint(
                fields=["invoice", "channel", "scheduled_for"],
                name="users_arrears_reminder_invoice_channel_date_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_due__gte=0),
                name="users_arrears_reminder_amount_nonnegative",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "scheduled_for"]),
            models.Index(fields=["tenancy", "scheduled_for"]),
        ]

    def clean(self):
        super().clean()
        if self.invoice_id and self.tenancy_id != self.invoice.tenancy_id:
            raise ValidationError({"invoice": "The reminder invoice must belong to the tenancy."})


class DemandLetter(models.Model):
    tenancy = models.ForeignKey(Tenancy, on_delete=models.PROTECT, related_name="demand_letters")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="demand_letters")
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="issued_demand_letters",
    )
    issued_on = models.DateField()
    due_on = models.DateField()
    arrears_amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=80, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_on", "-id"]

    def clean(self):
        super().clean()
        if self.due_on < self.issued_on:
            raise ValidationError({"due_on": "The demand due date cannot precede its issue date."})
        if self.invoice_id and self.tenancy_id != self.invoice.tenancy_id:
            raise ValidationError({"invoice": "The demand invoice must belong to the tenancy."})


class ArrearsPaymentPlan(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_COMPLETED = "completed"
    STATUS_DEFAULTED = "defaulted"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_ACTIVE, "Active"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_DEFAULTED, "Defaulted"),
        (STATUS_CANCELLED, "Cancelled"),
    )

    tenancy = models.ForeignKey(Tenancy, on_delete=models.PROTECT, related_name="payment_plans")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    instalment_amount = models.DecimalField(max_digits=12, decimal_places=2)
    start_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_payment_plans",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if self.total_amount <= 0:
            raise ValidationError({"total_amount": "The plan total must be positive."})
        if self.instalment_amount <= 0:
            raise ValidationError({"instalment_amount": "The instalment amount must be positive."})
        if not self.reason.strip():
            raise ValidationError({"reason": "A payment plan reason is required."})


class ArrearsPaymentPlanInstalment(models.Model):
    STATUS_DUE = "due"
    STATUS_PAID = "paid"
    STATUS_MISSED = "missed"
    STATUS_CHOICES = (
        (STATUS_DUE, "Due"),
        (STATUS_PAID, "Paid"),
        (STATUS_MISSED, "Missed"),
    )

    plan = models.ForeignKey(
        ArrearsPaymentPlan,
        on_delete=models.PROTECT,
        related_name="instalments",
    )
    sequence = models.PositiveIntegerField()
    due_date = models.DateField()
    amount_due = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DUE)
    missed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "sequence"], name="users_plan_instalment_sequence_unique"),
            models.CheckConstraint(condition=models.Q(amount_due__gt=0), name="users_plan_instalment_due_positive"),
            models.CheckConstraint(
                condition=models.Q(amount_paid__gte=0) & models.Q(amount_paid__lte=models.F("amount_due")),
                name="users_plan_instalment_paid_valid",
            ),
        ]

    def clean(self):
        super().clean()
        if self.amount_due <= 0:
            raise ValidationError({"amount_due": "The instalment amount must be positive."})
        if self.amount_paid < 0 or self.amount_paid > self.amount_due:
            raise ValidationError({"amount_paid": "Paid amount must be between zero and the amount due."})


class MoveInPackage(models.Model):
    HANDOVER_STATUS_PENDING = "pending"
    HANDOVER_STATUS_SUBMITTED = "submitted"
    HANDOVER_STATUS_CONFIRMED = "confirmed"
    HANDOVER_STATUS_LEGACY = "legacy"
    HANDOVER_STATUS_CHOICES = (
        (HANDOVER_STATUS_PENDING, "Pending caretaker handover"),
        (HANDOVER_STATUS_SUBMITTED, "Awaiting Property Officer confirmation"),
        (HANDOVER_STATUS_CONFIRMED, "Confirmed"),
        (HANDOVER_STATUS_LEGACY, "Legacy package"),
    )

    tenancy = models.OneToOneField(
        Tenancy,
        on_delete=models.PROTECT,
        related_name="move_in_package",
    )
    assigned_caretaker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="assigned_move_in_handovers",
        blank=True,
        null=True,
    )
    caretaker_notification_queued_at = models.DateTimeField(blank=True, null=True)
    caretaker_notification_error = models.CharField(max_length=500, blank=True)
    agreement_reference = models.CharField(max_length=100, unique=True)
    handover_status = models.CharField(
        max_length=20,
        choices=HANDOVER_STATUS_CHOICES,
        default=HANDOVER_STATUS_PENDING,
    )
    keys_issued = models.PositiveSmallIntegerField(blank=True, null=True)
    handover_inventory = models.JSONField(default=list, blank=True)
    condition_checklist = models.JSONField(default=list, blank=True)
    initial_meter_readings = models.JSONField(default=list, blank=True)
    handover_photos = models.JSONField(default=list, blank=True)
    handover_submitted_at = models.DateTimeField(blank=True, null=True)
    handover_submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_move_in_handovers",
        blank=True,
        null=True,
    )
    handover_confirmed_at = models.DateTimeField(blank=True, null=True)
    handover_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmed_move_in_handovers",
        blank=True,
        null=True,
    )
    generated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.agreement_reference


class MoveInCharge(models.Model):
    CHARGE_RENT = "rent"
    CHARGE_DEPOSIT = "deposit"
    CHARGE_RECURRING = "recurring"
    CHARGE_FEE = "fee"
    CHARGE_TYPE_CHOICES = (
        (CHARGE_RENT, "Rent"),
        (CHARGE_DEPOSIT, "Deposit"),
        (CHARGE_RECURRING, "Recurring"),
        (CHARGE_FEE, "Fee"),
    )

    package = models.ForeignKey(
        MoveInPackage,
        on_delete=models.PROTECT,
        related_name="charges",
    )
    charge_type = models.CharField(max_length=20, choices=CHARGE_TYPE_CHOICES)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    calculation = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["charge_type", "description", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="users_moveincharge_amount_nonnegative",
            ),
        ]

    def __str__(self):
        return f"{self.package} - {self.description}"


class MoveInChecklistItem(models.Model):
    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"
    STATUS_WAIVED = "waived"
    CARETAKER_HANDOVER_ITEM_TITLES = (
        "Record opening meter readings",
        "Inspect and document unit condition",
        "Hand over keys and access devices",
    )
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_WAIVED, "Waived"),
    )

    package = models.ForeignKey(
        MoveInPackage,
        on_delete=models.PROTECT,
        related_name="checklist_items",
    )
    title = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.package} - {self.title}"


class TenantActivationChallenge(models.Model):
    tenant = models.ForeignKey(
        "Tenant",
        on_delete=models.PROTECT,
        related_name="activation_challenges",
    )
    token_hash = models.CharField(max_length=128, unique=True)
    code_hash = models.CharField(max_length=64, unique=True, blank=True, null=True)
    phone_verification_required = models.BooleanField(default=False)
    unit = models.ForeignKey(
        "Unit",
        on_delete=models.PROTECT,
        related_name="activation_challenges",
        blank=True,
        null=True,
    )
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(blank=True, null=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    email_sent_at = models.DateTimeField(blank=True, null=True)
    email_delivery_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_usable(self):
        return self.used_at is None and self.expires_at > timezone.now()


class MoveInSettlement(models.Model):
    STATUS_PAYMENT_PENDING = "payment_pending"
    STATUS_PARTIAL = "partial"
    STATUS_BOOKED = "booked"
    STATUS_PAID = "paid"
    STATUS_MOVE_IN_PENDING = "move_in_pending"
    STATUS_ACTIVE = "active"
    STATUS_RESERVED = "reserved"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_PAYMENT_PENDING, "Payment pending"),
        (STATUS_PARTIAL, "Partial"),
        (STATUS_BOOKED, "Booked"),
        (STATUS_PAID, "Paid"),
        (STATUS_MOVE_IN_PENDING, "Move-in pending"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_RESERVED, "Reserved"),
        (STATUS_CANCELLED, "Cancelled"),
    )
    OPEN_STATUSES = (
        STATUS_PAYMENT_PENDING,
        STATUS_PARTIAL,
        STATUS_BOOKED,
        STATUS_PAID,
        STATUS_MOVE_IN_PENDING,
        STATUS_RESERVED,
    )

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="move_in_settlements")
    tenant = models.ForeignKey(
        "Tenant",
        on_delete=models.PROTECT,
        related_name="move_in_settlements",
        blank=True,
        null=True,
    )
    tenant_name = models.CharField(max_length=255)
    tenant_email = models.EmailField()
    tenant_phone = models.CharField(max_length=12)
    tenant_type = models.CharField(
        max_length=20,
        choices=(("individual", "Individual"), ("company", "Company")),
        default="individual",
    )
    national_id_or_passport = models.CharField(max_length=100, blank=True)
    company_registration_number = models.CharField(max_length=100, blank=True)
    contact_person = models.CharField(max_length=255, blank=True)
    kra_pin = models.CharField(max_length=20, blank=True)
    occupation = models.CharField(max_length=150, blank=True)
    employer = models.CharField(max_length=255, blank=True)
    next_of_kin_name = models.CharField(max_length=255, blank=True)
    next_of_kin_phone = models.CharField(max_length=12, blank=True)
    next_of_kin_relationship = models.CharField(max_length=100, blank=True)
    move_in_letting_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    whatsapp_opt_in = models.BooleanField(default=False)
    tenancy_type = models.CharField(
        max_length=30,
        choices=Tenancy.TENANCY_TYPE_CHOICES,
        default=Tenancy.TYPE_MONTH_TO_MONTH,
    )
    tenancy_end_date = models.DateField(blank=True, null=True)
    tenancy_due_day = models.PositiveSmallIntegerField(blank=True, null=True)
    deposit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    rent_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    garbage_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    water_deposit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    utility_deposit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    utility_deposits = models.JSONField(default=list, blank=True)
    utility_charges = models.JSONField(default=list, blank=True)
    total_required = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deposit_paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    outstanding_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    expected_move_in_date = models.DateField()
    status = models.CharField(
        max_length=24,
        choices=STATUS_CHOICES,
        default=STATUS_PAYMENT_PENDING,
    )
    activation_override = models.BooleanField(default=False)
    cancellation_reason = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    booked_at = models.DateTimeField(blank=True, null=True)
    activated_at = models.DateTimeField(blank=True, null=True)
    activation_email_sent_at = models.DateTimeField(blank=True, null=True)
    activation_email_error = models.TextField(blank=True)
    activation_sms_queued_at = models.DateTimeField(blank=True, null=True)
    activation_sms_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["unit", "status"])]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(deposit_amount__gte=0)
                    & models.Q(rent_amount__gte=0)
                    & models.Q(garbage_amount__gte=0)
                    & models.Q(water_deposit_amount__gte=0)
                    & models.Q(utility_deposit_amount__gte=0)
                    & models.Q(total_required__gte=0)
                    & models.Q(total_paid__gte=0)
                    & models.Q(deposit_paid_amount__gte=0)
                    & models.Q(outstanding_balance__gte=0)
                ),
                name="users_moveinsettlement_amounts_nonnegative",
            ),
            models.UniqueConstraint(
                fields=["unit"],
                condition=models.Q(
                    status__in=(
                        "payment_pending",
                        "partial",
                        "booked",
                        "paid",
                        "move_in_pending",
                        "reserved",
                    )
                ),
                name="users_moveinsettlement_one_open_per_unit",
            ),
        ]

    def clean(self):
        super().clean()
        if self.tenant_type not in dict(Tenant.TENANT_TYPE_CHOICES):
            raise ValidationError({"tenant_type": "This tenant type is not supported."})
        if self.tenancy_type not in dict(Tenancy.TENANCY_TYPE_CHOICES):
            raise ValidationError({"tenancy_type": "This tenancy type is not supported."})
        if self.tenancy_type == Tenancy.TYPE_FIXED_TERM and self.tenancy_end_date is None:
            raise ValidationError({"tenancy_end_date": "Fixed-term tenancies require an end date."})
        if self.tenancy_end_date and self.tenancy_end_date < self.expected_move_in_date:
            raise ValidationError({"tenancy_end_date": "The tenancy end date cannot be before its start date."})
        if self.tenancy_due_day is not None and not 1 <= self.tenancy_due_day <= 31:
            raise ValidationError({"tenancy_due_day": "The rent due day must be between 1 and 31."})
        if not isinstance(self.utility_deposits, list) or any(
            not isinstance(deposit, dict)
            or not isinstance(deposit.get("name"), str)
            or not deposit["name"].strip()
            or not isinstance(deposit.get("amount"), (int, float, str))
            for deposit in self.utility_deposits
        ):
            raise ValidationError({"utility_deposits": "Utility deposits must be a list of named amounts."})
        if not isinstance(self.utility_charges, list) or any(
            not isinstance(charge, dict)
            or not isinstance(charge.get("name"), str)
            or not charge["name"].strip()
            or not isinstance(charge.get("amount"), (int, float, str))
            for charge in self.utility_charges
        ):
            raise ValidationError({"utility_charges": "Utility charges must be a list of named amounts."})
        if self.unit_id and self.tenant_id and self.unit.property.branch_id != self.tenant.branch_id:
            raise ValidationError({"tenant": "The tenant and unit must belong to the same branch."})
        if self.total_paid > self.total_required:
            raise ValidationError({"total_paid": "Payments cannot exceed the move-in total."})
        if self.deposit_paid_amount > self.deposit_total:
            raise ValidationError({"deposit_paid_amount": "Deposit payments cannot exceed the configured deposits."})

    @property
    def deposit_total(self):
        return self.deposit_amount + self.water_deposit_amount + self.utility_deposit_amount


class MoveInPayment(models.Model):
    METHOD_MANUAL = "manual"
    METHOD_STK_PUSH = "stk_push"
    METHOD_OFFICER_CONFIRMATION = "officer_confirmation"
    METHOD_CHOICES = (
        (METHOD_MANUAL, "Manual"),
        (METHOD_STK_PUSH, "STK push"),
        (METHOD_OFFICER_CONFIRMATION, "Property officer payment confirmation"),
    )
    CHANNEL_MPESA = "mpesa"
    CHANNEL_BANK_TRANSFER = "bank_transfer"
    CHANNEL_CASH = "cash"
    CHANNEL_CHOICES = (
        (CHANNEL_MPESA, "M-Pesa"),
        (CHANNEL_BANK_TRANSFER, "Bank transfer"),
        (CHANNEL_CASH, "Cash"),
    )
    STATUS_PENDING = "pending"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_NEEDS_REVIEW = "needs_review"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
        (STATUS_NEEDS_REVIEW, "Needs review"),
    )

    settlement = models.ForeignKey(
        MoveInSettlement,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    payment_channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, blank=True)
    transaction_reference = models.CharField(max_length=100, blank=True)
    payment_date = models.DateField(blank=True, null=True)
    mpesa_reference = models.CharField(max_length=100, blank=True)
    checkout_request_id = models.CharField(max_length=100, blank=True, null=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="move_in_payments_recorded",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SUCCESS)
    result_description = models.TextField(blank=True)
    processed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["settlement", "created_at"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="users_moveinpayment_amount_positive",
            ),
            models.UniqueConstraint(
                fields=["checkout_request_id"],
                condition=models.Q(checkout_request_id__isnull=False),
                name="users_moveinpayment_checkout_unique",
            ),
        ]


class Tenant(models.Model):
    INDIVIDUAL = "individual"
    COMPANY = "company"
    TENANT_TYPE_CHOICES = (
        (INDIVIDUAL, "Individual"),
        (COMPANY, "Company"),
    )

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="tenants")
    tenant_type = models.CharField(max_length=20, choices=TENANT_TYPE_CHOICES)
    name = models.CharField(max_length=255)
    company_registration_number = models.CharField(max_length=100, blank=True)
    contact_person = models.CharField(max_length=255, blank=True)
    national_id_or_passport = models.CharField(max_length=100, blank=True)
    is_provisional = models.BooleanField(default=False)
    kra_pin = models.CharField(max_length=20, blank=True)
    occupation = models.CharField(max_length=150, blank=True)
    employer = models.CharField(max_length=255, blank=True)
    next_of_kin_name = models.CharField(max_length=255, blank=True)
    next_of_kin_phone = models.CharField(max_length=12, blank=True)
    next_of_kin_relationship = models.CharField(max_length=100, blank=True)
    phone = models.CharField(
        max_length=12,
        validators=[
            RegexValidator(
                r"^254[17]\d{8}$",
                "Use international format 2547XXXXXXXX or 2541XXXXXXXX.",
            )
        ],
    )
    phone_verified_at = models.DateTimeField(blank=True, null=True)
    whatsapp_opted_in_at = models.DateTimeField(blank=True, null=True)
    email = models.EmailField(blank=True)
    is_blacklisted = models.BooleanField(default=False)
    blacklist_reason = models.TextField(blank=True)
    portal_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="tenant_record",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "tenant_type", "national_id_or_passport"],
                condition=~models.Q(national_id_or_passport=""),
                name="users_tenant_branch_identity_unique",
            ),
            models.UniqueConstraint(
                fields=["branch", "company_registration_number"],
                condition=~models.Q(company_registration_number=""),
                name="users_tenant_branch_company_registration_unique",
            ),
        ]

    def clean(self):
        super().clean()
        if self.tenant_type == self.COMPANY:
            if not self.company_registration_number:
                raise ValidationError(
                    {"company_registration_number": "Company tenants require a registration number."}
                )
            if not self.contact_person:
                raise ValidationError(
                    {"contact_person": "Company tenants require a contact person."}
                )
        elif (
            self.tenant_type == self.INDIVIDUAL
            and not self.national_id_or_passport
            and not self.is_provisional
        ):
            raise ValidationError(
                {"national_id_or_passport": "Individual tenants require an ID or passport number."}
            )
        if self.is_blacklisted and not self.blacklist_reason:
            raise ValidationError(
                {"blacklist_reason": "A reason is required when a tenant is blacklisted."}
            )

    def __str__(self):
        return self.name


class TenantDocument(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="documents")
    document_type = models.CharField(max_length=100)
    name = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=500)
    metadata = models.JSONField(default=dict, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_tenant_documents",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

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
    id_or_registration_number = models.CharField(max_length=100, blank=True)
    kra_pin = models.CharField(max_length=20, blank=True)
    branches = models.ManyToManyField(Branch, related_name="user_profiles", blank=True)
    active_caretaker_property = models.ForeignKey(
        Property,
        on_delete=models.SET_NULL,
        related_name="active_caretaker_profiles",
        blank=True,
        null=True,
    )
    active_property = models.ForeignKey(
        Property,
        on_delete=models.SET_NULL,
        related_name="active_user_profiles",
        blank=True,
        null=True,
    )
    totp_secret_encrypted = models.BinaryField(blank=True, null=True)
    totp_enabled = models.BooleanField(default=False)
    totp_last_used_counter = models.BigIntegerField(blank=True, null=True)
    totp_recovery_codes = models.JSONField(default=list, blank=True)
    failed_login_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(blank=True, null=True)
    onboarding_completed = models.BooleanField(default=False)
    tutorial_completed = models.BooleanField(default=False)

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

    def accessible_branches(self, role_names=None):
        if self.user.is_superuser:
            return Branch.objects.filter(agency__is_active=True)
        memberships = self.agency_memberships.filter(
            agency__is_active=True,
            is_active=True,
        )
        director_agencies = memberships.filter(
            roles__name=SUPER_ADMIN_ROLE
        ).values("agency_id")
        role_memberships = memberships
        if role_names is not None:
            role_memberships = role_memberships.filter(roles__name__in=role_names)
        member_branches = role_memberships.filter(
            branches__agency_id=F("agency_id")
        ).values("branches__pk")
        return Branch.objects.filter(
            Q(agency_id__in=director_agencies)
            | Q(pk__in=member_branches)
        ).distinct()


class AgencyMembership(models.Model):
    profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="agency_memberships",
    )
    agency = models.ForeignKey(
        Agency,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    roles = models.ManyToManyField(
        "auth.Group",
        related_name="agency_memberships",
        blank=True,
    )
    branches = models.ManyToManyField(
        Branch,
        related_name="agency_memberships",
        blank=True,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["agency__name", "profile__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "agency"],
                name="users_agencymembership_profile_agency_unique",
            )
        ]

    def clean(self):
        super().clean()
        if self.pk and self.branches.exclude(agency_id=self.agency_id).exists():
            raise ValidationError(
                {"branches": "Assigned branches must belong to this membership's agency."}
            )

    def __str__(self):
        return f"{self.profile} at {self.agency}"


class DirectorActivationChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="director_activation_challenges",
    )
    code_hash = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["user", "expires_at"])]

    def is_expired(self):
        return timezone.now() >= self.expires_at


class StaffActivationChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    membership = models.ForeignKey(
        AgencyMembership,
        on_delete=models.CASCADE,
        related_name="activation_challenges",
    )
    code_hash = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["membership", "expires_at"])]

    def is_expired(self):
        return timezone.now() >= self.expires_at


class LandlordActivationChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    landlord = models.ForeignKey(
        Landlord,
        on_delete=models.CASCADE,
        related_name="activation_challenges",
    )
    code_hash = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["landlord", "expires_at"])]

    def is_expired(self):
        return timezone.now() >= self.expires_at


class TOTPLoginChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="totp_login_challenges",
    )
    setup_secret_encrypted = models.BinaryField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["user", "expires_at"])]

    def is_expired(self):
        return timezone.now() >= self.expires_at


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


class SMSDelivery(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_SENDING = "sending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = (
        (STATUS_QUEUED, "Queued"),
        (STATUS_SENDING, "Sending"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
        (STATUS_EXPIRED, "Expired"),
    )

    template_name = models.CharField(max_length=100)
    recipient_encrypted = models.BinaryField()
    message_encrypted = models.BinaryField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(blank=True, null=True)
    locked_at = models.DateTimeField(blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    provider_message_id = models.CharField(max_length=255, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="queued_sms_deliveries",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["status", "available_at"]),
            models.Index(fields=["created_by", "created_at"]),
        ]

    def __str__(self):
        return f"{self.template_name} SMS ({self.status})"


class WhatsAppContact(models.Model):
    """A WhatsApp number linked to platform identities after ID verification.

    Created by the Phase 2 conversational onboarding flow; Phase 1 only reads
    it to decide whether a recipient has opted into the WhatsApp channel.
    """

    wa_id = models.CharField(max_length=20, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="whatsapp_contacts",
        blank=True,
        null=True,
    )
    tenant = models.ForeignKey(
        "users.Tenant",
        on_delete=models.CASCADE,
        related_name="whatsapp_contacts",
        blank=True,
        null=True,
    )
    id_number_hash = models.CharField(max_length=64, blank=True)
    verified_at = models.DateTimeField(blank=True, null=True)
    failed_verifications = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"WhatsApp contact {self.wa_id}"


class ConversationState(models.Model):
    """Where a verified WhatsApp contact is in a conversational flow."""

    contact = models.OneToOneField(
        WhatsAppContact,
        on_delete=models.CASCADE,
        related_name="conversation_state",
    )
    active_role = models.CharField(max_length=20, blank=True)
    active_property = models.ForeignKey(
        Property,
        on_delete=models.SET_NULL,
        related_name="active_whatsapp_conversations",
        blank=True,
        null=True,
    )
    state = models.JSONField(default=dict, blank=True)
    last_message_id = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"Conversation state for {self.contact.wa_id}"


class RentStkRequest(models.Model):
    """A tenant STK push against a specific invoice."""

    STATUS_PENDING = "pending"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
    )

    invoice = models.ForeignKey(
        "users.Invoice",
        on_delete=models.PROTECT,
        related_name="rent_stk_requests",
    )
    tenant = models.ForeignKey(
        "users.Tenant",
        on_delete=models.PROTECT,
        related_name="rent_stk_requests",
    )
    contact = models.ForeignKey(
        WhatsAppContact,
        on_delete=models.SET_NULL,
        related_name="rent_stk_requests",
        blank=True,
        null=True,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    phone = models.CharField(max_length=30)
    checkout_request_id = models.CharField(max_length=100, unique=True)
    merchant_request_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    result_code = models.CharField(max_length=20, blank=True)
    result_description = models.CharField(max_length=500, blank=True)
    payment = models.ForeignKey(
        "users.Payment",
        on_delete=models.SET_NULL,
        related_name="rent_stk_requests",
        blank=True,
        null=True,
    )
    processed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self):
        return f"Rent STK {self.checkout_request_id} ({self.status})"


class WhatsAppDelivery(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_SENDING = "sending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = (
        (STATUS_QUEUED, "Queued"),
        (STATUS_SENDING, "Sending"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
        (STATUS_EXPIRED, "Expired"),
    )

    template_name = models.CharField(max_length=100)
    recipient_encrypted = models.BinaryField()
    payload_encrypted = models.BinaryField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
    )
    # Meta's post-send lifecycle reported by webhook status events
    # (sent/delivered/read/failed); distinct from the outbox status above.
    channel_status = models.CharField(max_length=20, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(blank=True, null=True)
    locked_at = models.DateTimeField(blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    provider_message_id = models.CharField(max_length=255, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="queued_whatsapp_deliveries",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["status", "available_at"]),
            models.Index(fields=["provider_message_id"]),
            models.Index(fields=["created_by", "created_at"]),
        ]

    def __str__(self):
        return f"{self.template_name} WhatsApp ({self.status})"


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
    agency = models.ForeignKey(
        Agency,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        blank=True,
        null=True,
    )
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
        indexes = [models.Index(fields=["agency", "timestamp"])]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Audit log entries are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit log entries are immutable.")

    def __str__(self):
        return f"{self.action} {self.entity} at {self.timestamp}"
