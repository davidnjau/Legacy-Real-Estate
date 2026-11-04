from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group

from users.admin_authentication import FinanceMFAAdminAuthenticationForm
from users.audit import record_audit_event
from users.models import (
    AuditLog,
    Announcement,
    ArrearsPolicy,
    ArrearsPaymentPlan,
    ArrearsPaymentPlanInstalment,
    ArrearsWaiver,
    Block,
    Branch,
    CaretakerInvitation,
    Floor,
    Invoice,
    InvoiceLine,
    InvoiceNumberSequence,
    MeterReading,
    MaintenanceTicket,
    MaintenanceTicketComment,
    MaintenanceCostAllocation,
    MaintenanceQuotation,
    MpesaCallback,
    Payment,
    PaymentAllocation,
    TenantCredit,
    Receipt,
    BankStatementImport,
    BankStatementRow,
    DepositAccount,
    DepositEntry,
    DemandLetter,
    Landlord,
    ManagementAgreement,
    MoveInCharge,
    MoveInChecklistItem,
    MoveInPackage,
    TenantActivationChallenge,
    Property,
    PropertyCaretakerAssignment,
    PropertyDocument,
    PropertyExpense,
    PropertyInspection,
    SMSLoginChallenge,
    Tenancy,
    Tenant,
    TenantDocument,
    Unit,
    UnitCharge,
    UnitReservation,
    UserProfile,
    UtilityConfiguration,
    Vendor,
)
from users.roles import SUPER_ADMIN_ROLE

User = get_user_model()
admin.site.login_form = FinanceMFAAdminAuthenticationForm


def _user_audit_values(user):
    profile = UserProfile.objects.filter(user_id=user.pk).first()
    return {
        "id": user.pk,
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "is_active": user.is_active,
        "is_staff": user.is_staff,
        "roles": list(user.groups.order_by("name").values_list("name", flat=True)),
        "profile": (
            {
                "name": profile.name,
                "phone": profile.phone,
                "branches": list(
                    profile.branches.order_by("name").values_list("name", flat=True)
                ),
            }
            if profile
            else None
        ),
    }


def _branch_audit_values(branch):
    return {
        "id": branch.pk,
        "name": branch.name,
        "town": branch.town,
        "paybill_numbers": branch.paybill_numbers,
    }


def _group_audit_values(group):
    return {
        "id": group.pk,
        "name": group.name,
        "permissions": list(
            group.permissions.order_by("content_type__app_label", "codename").values_list(
                "content_type__app_label", "codename"
            )
        ),
    }


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    extra = 0
    fields = ("name", "phone", "branches")
    filter_horizontal = ("branches",)


admin.site.unregister(User)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    inlines = (UserProfileInline,)
    list_display = ("username", "email", "is_active", "is_staff")
    list_filter = ("is_active", "is_staff", "groups")

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        user = form.instance
        is_staff = user.is_superuser or user.groups.filter(
            name=SUPER_ADMIN_ROLE
        ).exists()
        if user.is_staff != is_staff:
            user.is_staff = is_staff
            user.save(update_fields=["is_staff"])
        record_audit_event(
            request,
            "user.updated" if change else "user.created",
            "User",
            old_values=getattr(request, "_audit_previous_user_values", None),
            new_values=_user_audit_values(user),
        )

    def save_model(self, request, obj, form, change):
        previous = None
        if change:
            previous_user = User.objects.get(pk=obj.pk)
            previous = _user_audit_values(previous_user)
        request._audit_previous_user_values = previous
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        record_audit_event(
            request,
            "user.deleted",
            "User",
            old_values=_user_audit_values(obj),
        )
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        previous_values = [_user_audit_values(user) for user in queryset]
        for values in previous_values:
            record_audit_event(request, "user.deleted", "User", old_values=values)
        super().delete_queryset(request, queryset)

    def has_module_permission(self, request):
        return request.user.is_superuser or request.user.groups.filter(
            name=SUPER_ADMIN_ROLE
        ).exists()


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "town")
    search_fields = ("name", "town")

    def save_model(self, request, obj, form, change):
        previous = _branch_audit_values(Branch.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        record_audit_event(
            request,
            "branch.updated" if change else "branch.created",
            "Branch",
            old_values=previous,
            new_values=_branch_audit_values(obj),
        )

    def delete_model(self, request, obj):
        previous = _branch_audit_values(obj)
        super().delete_model(request, obj)
        record_audit_event(request, "branch.deleted", "Branch", old_values=previous)

    def delete_queryset(self, request, queryset):
        previous_values = [_branch_audit_values(branch) for branch in queryset]
        super().delete_queryset(request, queryset)
        for values in previous_values:
            record_audit_event(request, "branch.deleted", "Branch", old_values=values)


def _landlord_audit_values(landlord):
    return {
        "id": landlord.pk,
        "branch_id": landlord.branch_id,
        "portal_user_id": landlord.portal_user_id,
        "name": landlord.name,
        "id_or_registration_number": landlord.id_or_registration_number,
        "kra_pin": "[redacted]" if landlord.kra_pin else None,
        "phone": landlord.phone,
        "email": landlord.email,
        "payout_details": {key: "[redacted]" for key in landlord.payout_details},
    }


def _agreement_audit_values(agreement):
    return {
        "id": agreement.pk,
        "landlord_id": agreement.landlord_id,
        "effective_from": agreement.effective_from.isoformat(),
        "effective_to": (
            agreement.effective_to.isoformat() if agreement.effective_to else None
        ),
        "commission_model": agreement.commission_model,
        "commission_rate": (
            str(agreement.commission_rate)
            if agreement.commission_rate is not None
            else None
        ),
        "fixed_fee": str(agreement.fixed_fee) if agreement.fixed_fee is not None else None,
        "payout_day": agreement.payout_day,
        "mri_enabled": agreement.mri_enabled,
        "terms": agreement.terms,
    }


def _property_audit_values(property_obj):
    return {
        "id": property_obj.pk,
        "branch_id": property_obj.branch_id,
        "landlord_id": property_obj.landlord_id,
        "name": property_obj.name,
        "property_type": property_obj.property_type,
        "location": property_obj.location,
        "gps_coordinates": property_obj.gps_coordinates,
        "amenities": property_obj.amenities,
        "house_rules": property_obj.house_rules,
        "photos": property_obj.photos,
        "caretaker_ids": list(
            property_obj.caretakers.order_by("pk").values_list("pk", flat=True)
        ),
    }


def _unit_audit_values(unit):
    return {
        "id": unit.pk,
        "property_id": unit.property_id,
        "floor_id": unit.floor_id,
        "code": unit.code,
        "unit_type": unit.unit_type,
        "bedrooms": unit.bedrooms,
        "characteristics": unit.characteristics,
        "meters": unit.meters,
        "listing_enabled": unit.listing_enabled,
        "status": unit.status,
    }


def _charge_audit_values(charge):
    return {
        "id": charge.pk,
        "unit_id": charge.unit_id,
        "charge_type": charge.charge_type,
        "name": charge.name,
        "amount": str(charge.amount),
        "effective_from": charge.effective_from.isoformat(),
        "effective_to": (
            charge.effective_to.isoformat() if charge.effective_to else None
        ),
        "metadata": charge.metadata,
    }


def _reservation_audit_values(reservation):
    return {
        "id": reservation.pk,
        "unit_id": reservation.unit_id,
        "applicant_id": reservation.applicant_id,
        "expires_at": reservation.expires_at.isoformat(),
        "status": reservation.status,
        "released_at": reservation.released_at.isoformat() if reservation.released_at else None,
    }


def _tenant_audit_values(tenant):
    return {
        "id": tenant.pk,
        "branch_id": tenant.branch_id,
        "tenant_type": tenant.tenant_type,
        "name": tenant.name,
        "company_registration_number": tenant.company_registration_number,
        "contact_person": tenant.contact_person,
        "national_id_or_passport": "[redacted]" if tenant.national_id_or_passport else None,
        "kra_pin": "[redacted]" if tenant.kra_pin else None,
        "phone": tenant.phone,
        "email": tenant.email,
        "is_blacklisted": tenant.is_blacklisted,
        "blacklist_reason": tenant.blacklist_reason,
        "portal_user_id": tenant.portal_user_id,
    }


def _tenancy_audit_values(tenancy):
    return {
        "id": tenancy.pk,
        "unit_id": tenancy.unit_id,
        "tenant_id": tenancy.tenant_id,
        "tenancy_type": tenancy.tenancy_type,
        "start_date": tenancy.start_date.isoformat(),
        "end_date": tenancy.end_date.isoformat() if tenancy.end_date else None,
        "due_day": tenancy.due_day,
        "grace_period_days": tenancy.grace_period_days,
        "notice_period_days": tenancy.notice_period_days,
        "rent_escalation_terms": tenancy.rent_escalation_terms,
        "status": tenancy.status,
    }


def _move_in_package_audit_values(package):
    return {
        "id": package.pk,
        "tenancy_id": package.tenancy_id,
        "agreement_reference": package.agreement_reference,
        "generated_at": package.generated_at.isoformat(),
    }


@admin.register(Landlord)
class LandlordAdmin(admin.ModelAdmin):
    list_display = ("name", "branch", "phone", "email")
    list_filter = ("branch",)
    search_fields = ("name", "id_or_registration_number", "kra_pin", "phone", "email")

    def save_model(self, request, obj, form, change):
        previous = (
            _landlord_audit_values(Landlord.objects.get(pk=obj.pk))
            if change
            else None
        )
        super().save_model(request, obj, form, change)
        record_audit_event(
            request,
            "landlord.updated" if change else "landlord.created",
            "Landlord",
            old_values=previous,
            new_values=_landlord_audit_values(obj),
        )

    def delete_model(self, request, obj):
        previous = _landlord_audit_values(obj)
        super().delete_model(request, obj)
        record_audit_event(request, "landlord.deleted", "Landlord", old_values=previous)

    def delete_queryset(self, request, queryset):
        previous_values = [_landlord_audit_values(item) for item in queryset]
        super().delete_queryset(request, queryset)
        for values in previous_values:
            record_audit_event(request, "landlord.deleted", "Landlord", old_values=values)


@admin.register(ManagementAgreement)
class ManagementAgreementAdmin(admin.ModelAdmin):
    list_display = ("landlord", "effective_from", "effective_to", "commission_model")
    list_filter = ("commission_model", "mri_enabled")
    search_fields = ("landlord__name",)

    def save_model(self, request, obj, form, change):
        previous = (
            _agreement_audit_values(ManagementAgreement.objects.get(pk=obj.pk))
            if change
            else None
        )
        super().save_model(request, obj, form, change)
        record_audit_event(
            request,
            "management_agreement.updated" if change else "management_agreement.created",
            "ManagementAgreement",
            old_values=previous,
            new_values=_agreement_audit_values(obj),
        )

    def delete_model(self, request, obj):
        previous = _agreement_audit_values(obj)
        super().delete_model(request, obj)
        record_audit_event(
            request,
            "management_agreement.deleted",
            "ManagementAgreement",
            old_values=previous,
        )

    def delete_queryset(self, request, queryset):
        previous_values = [_agreement_audit_values(item) for item in queryset]
        super().delete_queryset(request, queryset)
        for values in previous_values:
            record_audit_event(
                request,
                "management_agreement.deleted",
                "ManagementAgreement",
                old_values=values,
            )


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("name", "branch", "landlord", "property_type", "location")
    list_filter = ("branch", "property_type")
    search_fields = ("name", "location", "landlord__name")
    filter_horizontal = ("caretakers",)


@admin.register(PropertyCaretakerAssignment)
class PropertyCaretakerAssignmentAdmin(admin.ModelAdmin):
    list_display = ("property", "caretaker", "assigned_by", "assigned_at", "ended_at")
    list_filter = ("ended_at",)
    search_fields = ("property__name", "caretaker__email", "caretaker__profile__name")
    readonly_fields = ("assigned_at",)

    def save_model(self, request, obj, form, change):
        previous = (
            _property_audit_values(Property.objects.get(pk=obj.pk)) if change else None
        )
        super().save_model(request, obj, form, change)
        record_audit_event(
            request,
            "property.updated" if change else "property.created",
            "Property",
            old_values=previous,
            new_values=_property_audit_values(obj),
        )


@admin.register(Block)
class BlockAdmin(admin.ModelAdmin):
    list_display = ("name", "property")
    search_fields = ("name", "property__name")


@admin.register(Floor)
class FloorAdmin(admin.ModelAdmin):
    list_display = ("name", "block", "level")
    search_fields = ("name", "block__name", "block__property__name")


@admin.register(PropertyDocument)
class PropertyDocumentAdmin(admin.ModelAdmin):
    list_display = ("name", "document_type", "property", "uploaded_by", "created_at")
    list_filter = ("document_type",)
    search_fields = ("name", "property__name", "storage_key")
    readonly_fields = ("uploaded_by", "created_at")


@admin.register(PropertyInspection)
class PropertyInspectionAdmin(admin.ModelAdmin):
    list_display = ("title", "property", "unit", "scheduled_for", "responsible_staff", "status", "shared_at")
    list_filter = ("status", "inspection_type")
    search_fields = ("title", "property__name", "unit__code", "responsible_staff__email")
    readonly_fields = ("created_by", "completed_at", "share_token", "shared_at", "created_at", "updated_at")


@admin.register(PropertyExpense)
class PropertyExpenseAdmin(admin.ModelAdmin):
    list_display = ("description", "property", "category", "amount", "recurrence", "status", "effective_from")
    list_filter = ("status", "recurrence", "category")
    search_fields = ("description", "property__name", "vendor__name")
    readonly_fields = ("submitted_by", "approved_by", "approved_at", "created_at", "updated_at")


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "branch", "property", "status", "publish_at", "expires_at", "published_by")
    list_filter = ("status", "branch")
    search_fields = ("title", "body", "property__name")
    readonly_fields = ("created_by", "published_by", "created_at", "updated_at")


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("code", "property", "unit_type", "status", "listing_enabled")
    list_filter = ("status", "listing_enabled", "unit_type")
    search_fields = ("code", "property__name")

    def save_model(self, request, obj, form, change):
        previous = _unit_audit_values(Unit.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        record_audit_event(
            request,
            "unit.updated" if change else "unit.created",
            "Unit",
            old_values=previous,
            new_values=_unit_audit_values(obj),
        )


@admin.register(UnitCharge)
class UnitChargeAdmin(admin.ModelAdmin):
    list_display = ("unit", "charge_type", "charge_code", "name", "amount", "effective_from", "effective_to")
    list_filter = ("charge_type",)
    search_fields = ("unit__code", "unit__property__name", "name")

    def save_model(self, request, obj, form, change):
        previous = _charge_audit_values(UnitCharge.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        record_audit_event(
            request,
            "unit_charge.updated" if change else "unit_charge.created",
            "UnitCharge",
            old_values=previous,
            new_values=_charge_audit_values(obj),
        )


@admin.register(UtilityConfiguration)
class UtilityConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "unit",
        "utility_type",
        "billing_method",
        "name",
        "effective_from",
        "effective_to",
    )
    list_filter = ("utility_type", "billing_method")
    search_fields = ("unit__code", "unit__property__name", "name")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        record_audit_event(
            request,
            "utility.updated" if change else "utility.created",
            "UtilityConfiguration",
            new_values={
                "id": obj.pk,
                "unit_id": obj.unit_id,
                "utility_type": obj.utility_type,
                "billing_method": obj.billing_method,
                "effective_from": obj.effective_from.isoformat(),
            },
        )


@admin.register(MeterReading)
class MeterReadingAdmin(admin.ModelAdmin):
    list_display = (
        "unit",
        "meter",
        "period",
        "previous_value",
        "current_value",
        "submitted_by",
        "submitted_at",
    )
    list_filter = ("meter", "period")
    search_fields = ("unit__code", "unit__property__name", "meter")
    readonly_fields = ("submitted_by", "submitted_at")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.submitted_by = request.user
        super().save_model(request, obj, form, change)
        record_audit_event(
            request,
            "meter_reading.updated" if change else "meter_reading.created",
            "MeterReading",
            new_values={
                "id": obj.pk,
                "unit_id": obj.unit_id,
                "meter": obj.meter,
                "period": obj.period.isoformat(),
                "current_value": str(obj.current_value),
            },
        )


@admin.register(MaintenanceTicket)
class MaintenanceTicketAdmin(admin.ModelAdmin):
    list_display = ("title", "property", "unit", "category", "priority", "status", "assigned_to", "created_at")
    list_filter = ("category", "priority", "status")
    search_fields = ("title", "description", "property__name", "unit__code")
    readonly_fields = ("reported_by", "created_at", "updated_at", "completed_at", "closed_at")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.reported_by = request.user
        super().save_model(request, obj, form, change)
        record_audit_event(
            request,
            "maintenance_ticket.updated" if change else "maintenance_ticket.created",
            "MaintenanceTicket",
            new_values={"id": obj.pk, "status": obj.status, "assigned_to": obj.assigned_to_id},
        )


@admin.register(MaintenanceTicketComment)
class MaintenanceTicketCommentAdmin(admin.ModelAdmin):
    list_display = ("ticket", "author", "created_at")
    search_fields = ("ticket__title", "body", "author__email")
    readonly_fields = ("author", "created_at")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.author = request.user
        super().save_model(request, obj, form, change)
        record_audit_event(
            request,
            "maintenance_ticket.comment_added",
            "MaintenanceTicketComment",
            new_values={"id": obj.pk, "ticket_id": obj.ticket_id},
        )


@admin.register(MaintenanceQuotation)
class MaintenanceQuotationAdmin(admin.ModelAdmin):
    list_display = ("ticket", "vendor", "amount", "status", "submitted_by", "approved_by", "created_at")
    list_filter = ("status", "vendor")
    search_fields = ("ticket__title", "vendor__name", "description")
    readonly_fields = ("submitted_by", "approved_by", "approved_at", "created_at", "updated_at")


@admin.register(MaintenanceCostAllocation)
class MaintenanceCostAllocationAdmin(admin.ModelAdmin):
    list_display = ("quotation", "target", "amount", "landlord", "tenant", "allocated_by", "created_at")
    list_filter = ("target",)
    search_fields = ("quotation__ticket__title", "landlord__name", "tenant__name")
    readonly_fields = ("allocated_by", "created_at")


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "trade", "branch", "phone", "rating", "is_active", "updated_at")
    list_filter = ("branch", "trade", "is_active")
    search_fields = ("name", "trade", "phone", "email", "mpesa_number")
    readonly_fields = ("created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        record_audit_event(
            request,
            "vendor.updated" if change else "vendor.created",
            "Vendor",
            new_values={"id": obj.pk, "branch_id": obj.branch_id, "name": obj.name, "is_active": obj.is_active},
        )


@admin.register(MpesaCallback)
class MpesaCallbackAdmin(admin.ModelAdmin):
    list_display = ("transaction_code", "amount", "account_reference", "status", "received_at")
    list_filter = ("status",)
    search_fields = ("transaction_code", "account_reference", "payer_phone")
    readonly_fields = (
        "transaction_code",
        "amount",
        "payer_phone",
        "account_reference",
        "transaction_time",
        "raw_payload",
        "status",
        "failure_reason",
        "received_at",
        "processed_at",
    )


@admin.register(UnitReservation)
class UnitReservationAdmin(admin.ModelAdmin):
    list_display = ("unit", "applicant", "status", "expires_at", "reserved_at")
    list_filter = ("status",)
    search_fields = ("unit__code", "unit__property__name", "applicant__name")
    readonly_fields = ("reserved_at", "released_at", "created_by")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        previous = _reservation_audit_values(UnitReservation.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        record_audit_event(
            request,
            "unit_reservation.updated" if change else "unit_reservation.created",
            "UnitReservation",
            old_values=previous,
            new_values=_reservation_audit_values(obj),
        )


@admin.register(InvoiceNumberSequence)
class InvoiceNumberSequenceAdmin(admin.ModelAdmin):
    list_display = ("branch", "year", "next_number")
    readonly_fields = ("branch", "year", "next_number")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "branch", "tenancy", "period_start", "total", "status")
    list_filter = ("branch", "status")
    search_fields = ("number", "tenancy__tenant__name", "tenancy__unit__code")
    readonly_fields = (
        "branch",
        "tenancy",
        "number",
        "period_start",
        "period_end",
        "due_date",
        "status",
        "total",
        "issued_at",
        "updated_at",
    )


@admin.register(InvoiceLine)
class InvoiceLineAdmin(admin.ModelAdmin):
    list_display = ("invoice", "charge_type", "description", "amount")
    readonly_fields = ("invoice", "charge_type", "description", "amount", "metadata")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("reference", "tenant", "amount", "allocated_amount", "credit_amount", "source", "instrument_status", "received_at")
    list_filter = ("source", "instrument_status")
    search_fields = ("reference", "tenant__name")
    readonly_fields = (
        "tenant",
        "amount",
        "reference",
        "source",
        "instrument_status",
        "cheque_number",
        "cheque_bank",
        "clearing_date",
        "collected_by",
        "notes",
        "received_at",
        "allocated_amount",
        "credit_amount",
        "created_at",
    )


@admin.register(PaymentAllocation)
class PaymentAllocationAdmin(admin.ModelAdmin):
    list_display = ("payment", "invoice", "amount", "created_at")
    readonly_fields = ("payment", "invoice", "amount", "created_at")


@admin.register(TenantCredit)
class TenantCreditAdmin(admin.ModelAdmin):
    list_display = ("tenant", "payment", "amount", "remaining_amount", "created_at")
    readonly_fields = ("tenant", "payment", "amount", "remaining_amount", "created_at")


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("number", "tenant", "payment", "amount", "balance_after", "issued_at")
    search_fields = ("number", "tenant__name", "payment__reference")
    readonly_fields = ("payment", "number", "tenant", "amount", "balance_after", "issued_at")


@admin.register(BankStatementImport)
class BankStatementImportAdmin(admin.ModelAdmin):
    list_display = ("filename", "file_format", "status", "total_rows", "valid_rows", "invalid_rows", "imported_at")
    list_filter = ("file_format", "status")
    readonly_fields = (
        "filename",
        "file_format",
        "imported_by",
        "imported_at",
        "status",
        "total_rows",
        "valid_rows",
        "invalid_rows",
    )


@admin.register(BankStatementRow)
class BankStatementRowAdmin(admin.ModelAdmin):
    list_display = ("statement", "row_number", "reference", "amount", "status", "payment")
    list_filter = ("status",)
    search_fields = ("reference", "account_reference", "payer_phone")
    readonly_fields = (
        "statement",
        "row_number",
        "transaction_date",
        "reference",
        "account_reference",
        "payer_phone",
        "amount",
        "raw_data",
        "validation_error",
        "suggestions",
        "status",
        "payment",
    )


@admin.register(DepositAccount)
class DepositAccountAdmin(admin.ModelAdmin):
    list_display = ("tenancy", "target_amount", "balance", "updated_at")
    search_fields = ("tenancy__tenant__name", "tenancy__unit__code")
    readonly_fields = ("tenancy", "target_amount", "balance", "created_at", "updated_at")


@admin.register(DepositEntry)
class DepositEntryAdmin(admin.ModelAdmin):
    list_display = ("reference", "account", "entry_type", "amount", "created_by", "created_at")
    list_filter = ("entry_type",)
    search_fields = ("reference", "account__tenancy__tenant__name")
    readonly_fields = (
        "account",
        "entry_type",
        "amount",
        "reference",
        "description",
        "created_by",
        "created_at",
    )


@admin.register(ArrearsPolicy)
class ArrearsPolicyAdmin(admin.ModelAdmin):
    list_display = ("branch", "grace_days", "penalty_rate", "reminder_after_days", "demand_after_days")


@admin.register(ArrearsWaiver)
class ArrearsWaiverAdmin(admin.ModelAdmin):
    list_display = ("invoice", "amount", "approved_by", "approved_at")
    readonly_fields = ("tenancy", "invoice", "amount", "reason", "approved_by", "approved_at")


@admin.register(DemandLetter)
class DemandLetterAdmin(admin.ModelAdmin):
    list_display = ("reference", "invoice", "issued_on", "due_on", "arrears_amount")
    readonly_fields = ("tenancy", "invoice", "issued_by", "issued_on", "due_on", "arrears_amount", "reference", "created_at")


@admin.register(ArrearsPaymentPlan)
class ArrearsPaymentPlanAdmin(admin.ModelAdmin):
    list_display = ("tenancy", "total_amount", "instalment_amount", "start_date", "status")
    list_filter = ("status",)
    readonly_fields = ("tenancy", "total_amount", "instalment_amount", "start_date", "reason", "created_by", "created_at", "updated_at")


@admin.register(ArrearsPaymentPlanInstalment)
class ArrearsPaymentPlanInstalmentAdmin(admin.ModelAdmin):
    list_display = ("plan", "sequence", "due_date", "amount_due", "amount_paid", "status")
    list_filter = ("status",)
    readonly_fields = ("plan", "sequence", "due_date", "amount_due", "amount_paid", "paid_at", "status", "missed_at")


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant_type", "branch", "phone", "is_blacklisted")
    list_filter = ("branch", "tenant_type", "is_blacklisted")
    search_fields = ("name", "company_registration_number", "national_id_or_passport", "phone", "email")

    def save_model(self, request, obj, form, change):
        previous = _tenant_audit_values(Tenant.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        record_audit_event(
            request,
            "tenant.updated" if change else "tenant.created",
            "Tenant",
            old_values=previous,
            new_values=_tenant_audit_values(obj),
        )


@admin.register(Tenancy)
class TenancyAdmin(admin.ModelAdmin):
    list_display = ("unit", "tenant", "tenancy_type", "start_date", "end_date", "status")
    list_filter = ("tenancy_type", "status")
    search_fields = ("unit__code", "unit__property__name", "tenant__name")
    readonly_fields = ("created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        previous = _tenancy_audit_values(Tenancy.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        record_audit_event(
            request,
            "tenancy.updated" if change else "tenancy.created",
            "Tenancy",
            old_values=previous,
            new_values=_tenancy_audit_values(obj),
        )


@admin.register(MoveInPackage)
class MoveInPackageAdmin(admin.ModelAdmin):
    list_display = ("agreement_reference", "tenancy", "generated_at")
    search_fields = ("agreement_reference", "tenancy__tenant__name", "tenancy__unit__code")
    readonly_fields = ("generated_at", "updated_at")

    def save_model(self, request, obj, form, change):
        previous = _move_in_package_audit_values(MoveInPackage.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        record_audit_event(
            request,
            "move_in_package.updated" if change else "move_in_package.created",
            "MoveInPackage",
            old_values=previous,
            new_values=_move_in_package_audit_values(obj),
        )


@admin.register(MoveInCharge)
class MoveInChargeAdmin(admin.ModelAdmin):
    list_display = ("package", "charge_type", "description", "amount")
    list_filter = ("charge_type",)
    search_fields = ("package__agreement_reference", "description")


@admin.register(MoveInChecklistItem)
class MoveInChecklistItemAdmin(admin.ModelAdmin):
    list_display = ("package", "title", "status", "completed_at")
    list_filter = ("status",)
    search_fields = ("package__agreement_reference", "title")


@admin.register(TenantActivationChallenge)
class TenantActivationChallengeAdmin(admin.ModelAdmin):
    list_display = ("tenant", "unit", "expires_at", "used_at", "created_at")
    search_fields = ("tenant__name", "tenant__email", "tenant__phone", "unit__code")
    readonly_fields = ("token_hash", "code_hash", "created_at", "used_at")


@admin.register(CaretakerInvitation)
class CaretakerInvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "property", "created_by", "expires_at", "used_at", "revoked_at")
    list_filter = ("property__branch",)
    search_fields = ("email", "property__name")
    readonly_fields = ("token_hash", "created_at", "used_at", "used_by", "revoked_at")


@admin.register(TenantDocument)
class TenantDocumentAdmin(admin.ModelAdmin):
    list_display = ("name", "document_type", "tenant", "uploaded_by", "created_at")
    list_filter = ("document_type",)
    search_fields = ("name", "tenant__name", "storage_key")
    readonly_fields = ("uploaded_by", "created_at")


admin.site.unregister(Group)


@admin.register(Group)
class AuditedGroupAdmin(DjangoGroupAdmin):
    def save_model(self, request, obj, form, change):
        previous = _group_audit_values(Group.objects.get(pk=obj.pk)) if change else None
        request._audit_previous_group_values = previous
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        record_audit_event(
            request,
            "role.updated" if change else "role.created",
            "Role",
            old_values=getattr(request, "_audit_previous_group_values", None),
            new_values=_group_audit_values(form.instance),
        )

    def delete_model(self, request, obj):
        record_audit_event(
            request,
            "role.deleted",
            "Role",
            old_values=_group_audit_values(obj),
        )
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        previous_values = [_group_audit_values(group) for group in queryset]
        for values in previous_values:
            record_audit_event(request, "role.deleted", "Role", old_values=values)
        super().delete_queryset(request, queryset)


@admin.register(SMSLoginChallenge)
class SMSLoginChallengeAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "expires_at", "attempts")
    readonly_fields = ("user", "code_hash", "created_at", "expires_at", "attempts")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "user", "action", "entity", "ip_address")
    list_filter = ("action", "entity")
    search_fields = ("user__username", "user__email", "action", "entity")
    readonly_fields = (
        "user",
        "action",
        "entity",
        "old_values",
        "new_values",
        "ip_address",
        "timestamp",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
