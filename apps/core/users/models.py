from datetime import date

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


class Landlord(models.Model):
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="landlords",
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


class Property(models.Model):
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

    def clean(self):
        super().clean()
        if self.landlord_id and self.landlord.branch_id != self.branch_id:
            raise ValidationError({"landlord": "The landlord must belong to the property's branch."})

    def __str__(self):
        return self.name


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


class Unit(models.Model):
    STATUS_DRAFT = "Draft"
    STATUS_VACANT = "Vacant"
    STATUS_OCCUPIED = "Occupied"
    STATUS_NOTICE_GIVEN = "Notice Given"
    STATUS_MAINTENANCE = "Maintenance"
    STATUS_ARCHIVED = "Archived"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_VACANT, "Vacant"),
        (STATUS_OCCUPIED, "Occupied"),
        (STATUS_NOTICE_GIVEN, "Notice Given"),
        (STATUS_MAINTENANCE, "Maintenance"),
        (STATUS_ARCHIVED, "Archived"),
    )
    ALLOWED_STATUS_TRANSITIONS = {
        STATUS_DRAFT: {STATUS_VACANT, STATUS_ARCHIVED},
        STATUS_VACANT: {STATUS_OCCUPIED, STATUS_NOTICE_GIVEN, STATUS_MAINTENANCE, STATUS_ARCHIVED},
        STATUS_OCCUPIED: {STATUS_NOTICE_GIVEN, STATUS_MAINTENANCE, STATUS_ARCHIVED},
        STATUS_NOTICE_GIVEN: {STATUS_VACANT, STATUS_OCCUPIED, STATUS_MAINTENANCE, STATUS_ARCHIVED},
        STATUS_MAINTENANCE: {STATUS_VACANT, STATUS_OCCUPIED, STATUS_ARCHIVED},
        STATUS_ARCHIVED: set(),
    }

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="units")
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
    due_day = models.PositiveSmallIntegerField(default=1)
    grace_period_days = models.PositiveSmallIntegerField(default=0)
    notice_period_days = models.PositiveSmallIntegerField(default=30)
    rent_escalation_terms = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date", "-id"]

    def clean(self):
        super().clean()
        if self.tenancy_type not in dict(self.TENANCY_TYPE_CHOICES):
            raise ValidationError({"tenancy_type": "This tenancy type is not supported."})
        if self.tenancy_type == self.TYPE_FIXED_TERM and self.end_date is None:
            raise ValidationError({"end_date": "Fixed-term tenancies require an end date."})
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "The tenancy end date cannot be before its start date."})
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
    STATUS_ISSUED = "issued"
    STATUS_VOID = "void"
    STATUS_CHOICES = (
        (STATUS_ISSUED, "Issued"),
        (STATUS_VOID, "Void"),
    )

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="invoices")
    tenancy = models.ForeignKey(Tenancy, on_delete=models.PROTECT, related_name="invoices")
    number = models.CharField(max_length=50, unique=True)
    period_start = models.DateField()
    period_end = models.DateField()
    due_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ISSUED)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    issued_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_start", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenancy", "period_start", "period_end"],
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
        if not self._state.adding:
            raise ValidationError("Issued invoices are immutable; use a credit note workflow.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Issued invoices cannot be deleted.")


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
    TYPE_ADJUSTMENT = "adjustment"
    TYPE_CHOICES = (
        (TYPE_RECEIPT, "Receipt"),
        (TYPE_TOP_UP, "Top-up"),
        (TYPE_REFUND, "Refund"),
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
    penalty_rate = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    reminder_after_days = models.PositiveSmallIntegerField(default=7)
    demand_after_days = models.PositiveSmallIntegerField(default=30)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if self.penalty_rate < 0:
            raise ValidationError({"penalty_rate": "Penalty rate cannot be negative."})


class ArrearsWaiver(models.Model):
    tenancy = models.ForeignKey(Tenancy, on_delete=models.PROTECT, related_name="arrears_waivers")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="arrears_waivers")
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
    tenancy = models.OneToOneField(
        Tenancy,
        on_delete=models.PROTECT,
        related_name="move_in_package",
    )
    agreement_reference = models.CharField(max_length=100, unique=True)
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
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_usable(self):
        return self.used_at is None and self.expires_at > timezone.now()


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
        elif self.tenant_type == self.INDIVIDUAL and not self.national_id_or_passport:
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
