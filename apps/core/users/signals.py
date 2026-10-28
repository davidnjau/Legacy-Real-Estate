from django.contrib.auth.models import Group, Permission
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from users.roles import SUPER_ADMIN_ROLE


@receiver(post_migrate)
def configure_super_admin_permissions(sender, using, **kwargs):
    if sender.label != "users":
        return

    group, _ = Group.objects.using(using).get_or_create(name=SUPER_ADMIN_ROLE)
    permissions = Permission.objects.using(using).filter(
        content_type__app_label__in={"auth", "users"},
        codename__in={
            "add_branch",
            "change_branch",
            "delete_branch",
            "view_branch",
            "add_group",
            "change_group",
            "delete_group",
            "view_group",
            "view_permission",
            "add_user",
            "change_user",
            "delete_user",
            "view_user",
            "add_userprofile",
            "change_userprofile",
            "delete_userprofile",
            "view_userprofile",
            "view_auditlog",
            "manage_branch_assignments",
        },
    )
    group.permissions.add(*permissions)
