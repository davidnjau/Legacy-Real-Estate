from django.contrib.auth.models import Group, Permission
from django.db.models.signals import m2m_changed, post_migrate
from django.dispatch import receiver

from users.models import AgencyMembership, Branch, UserProfile
from users.roles import ROLE_NAMES, SUPER_ADMIN_ROLE


@receiver(m2m_changed, sender=UserProfile.branches.through)
def create_agency_memberships_for_branch_assignments(
    sender,
    instance,
    action,
    reverse,
    model,
    pk_set,
    using,
    **kwargs,
):
    if reverse:
        return

    if action == "post_clear":
        for membership in instance.agency_memberships.using(using).all():
            membership.branches.clear()
        return
    if action not in {"post_add", "post_remove"} or not pk_set:
        return

    branches = Branch.objects.using(using).filter(pk__in=pk_set).only("pk", "agency_id")
    existing_membership_agency_ids = set(
        instance.agency_memberships.using(using).values_list("agency_id", flat=True)
    )
    for branch in branches:
        if action == "post_remove":
            membership = instance.agency_memberships.using(using).filter(
                agency_id=branch.agency_id
            ).first()
            if membership:
                membership.branches.remove(branch)
            continue

        membership, created = AgencyMembership.objects.using(using).get_or_create(
            profile=instance,
            agency_id=branch.agency_id,
        )
        if created and not existing_membership_agency_ids:
            membership.roles.add(*instance.user.groups.using(using).all())
        membership.branches.add(branch)
        existing_membership_agency_ids.add(branch.agency_id)


@receiver(post_migrate)
def configure_super_admin_permissions(sender, using, **kwargs):
    if sender.label != "users":
        return

    groups = {
        role_name: Group.objects.using(using).get_or_create(name=role_name)[0]
        for role_name in ROLE_NAMES
    }
    group = groups[SUPER_ADMIN_ROLE]
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
            "add_landlord",
            "change_landlord",
            "delete_landlord",
            "view_landlord",
            "add_managementagreement",
            "change_managementagreement",
            "delete_managementagreement",
            "view_managementagreement",
            "add_property",
            "change_property",
            "delete_property",
            "view_property",
            "add_block",
            "change_block",
            "delete_block",
            "view_block",
            "add_floor",
            "change_floor",
            "delete_floor",
            "view_floor",
            "add_propertydocument",
            "change_propertydocument",
            "delete_propertydocument",
            "view_propertydocument",
            "add_unit",
            "change_unit",
            "delete_unit",
            "view_unit",
            "add_unitcharge",
            "change_unitcharge",
            "delete_unitcharge",
            "view_unitcharge",
            "add_unitreservation",
            "change_unitreservation",
            "delete_unitreservation",
            "view_unitreservation",
            "add_tenant",
            "change_tenant",
            "delete_tenant",
            "view_tenant",
            "add_tenantdocument",
            "change_tenantdocument",
            "delete_tenantdocument",
            "view_tenantdocument",
            "add_tenancy",
            "change_tenancy",
            "delete_tenancy",
            "view_tenancy",
            "add_moveinpackage",
            "change_moveinpackage",
            "delete_moveinpackage",
            "view_moveinpackage",
            "add_moveincharge",
            "change_moveincharge",
            "delete_moveincharge",
            "view_moveincharge",
            "add_moveinchecklistitem",
            "change_moveinchecklistitem",
            "delete_moveinchecklistitem",
            "view_moveinchecklistitem",
            "add_tenantactivationchallenge",
            "change_tenantactivationchallenge",
            "delete_tenantactivationchallenge",
            "view_tenantactivationchallenge",
            "add_invoicenumbersequence",
            "change_invoicenumbersequence",
            "delete_invoicenumbersequence",
            "view_invoicenumbersequence",
            "add_invoice",
            "change_invoice",
            "delete_invoice",
            "view_invoice",
            "add_invoiceline",
            "change_invoiceline",
            "delete_invoiceline",
            "view_invoiceline",
            "add_utilityconfiguration",
            "change_utilityconfiguration",
            "delete_utilityconfiguration",
            "view_utilityconfiguration",
            "add_meterreading",
            "change_meterreading",
            "delete_meterreading",
            "view_meterreading",
            "add_mpesacallback",
            "change_mpesacallback",
            "delete_mpesacallback",
            "view_mpesacallback",
            "add_arrearspolicy",
            "change_arrearspolicy",
            "delete_arrearspolicy",
            "view_arrearspolicy",
            "add_arrearswaiver",
            "change_arrearswaiver",
            "delete_arrearswaiver",
            "view_arrearswaiver",
            "add_demandletter",
            "change_demandletter",
            "delete_demandletter",
            "view_demandletter",
            "add_arrearspaymentplan",
            "change_arrearspaymentplan",
            "delete_arrearspaymentplan",
            "view_arrearspaymentplan",
            "add_arrearspaymentplaninstalment",
            "change_arrearspaymentplaninstalment",
            "delete_arrearspaymentplaninstalment",
            "view_arrearspaymentplaninstalment",
            "manage_branch_assignments",
        },
    )
    group.permissions.add(*permissions)
