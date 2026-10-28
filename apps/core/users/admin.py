from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group

from users.admin_authentication import FinanceMFAAdminAuthenticationForm
from users.audit import record_audit_event
from users.models import AuditLog, Branch, SMSLoginChallenge, UserProfile
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
