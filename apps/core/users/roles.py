SUPER_ADMIN_ROLE = "Super Admin (Director)"
BRANCH_MANAGER_ROLE = "Branch Manager"
CARETAKER_ROLE = "Caretaker"
LANDLORD_PORTAL_ROLE = "Landlord (portal)"
TENANT_PORTAL_ROLE = "Tenant (portal)"
ROLE_NAMES = (
    SUPER_ADMIN_ROLE,
    BRANCH_MANAGER_ROLE,
    "Accountant",
    "Property Officer / Agent",
    "Land Sales Agent",
    CARETAKER_ROLE,
    LANDLORD_PORTAL_ROLE,
    "Tenant (portal)",
    "Land Buyer (portal)",
)
FINANCE_ROLE_NAMES = ROLE_NAMES[:3]
LANDLORD_MANAGEMENT_ROLE_NAMES = ROLE_NAMES[:4]
AGENCY_STAFF_ROLE_NAMES = ROLE_NAMES[1:5]
AGENCY_CARETAKER_ROLE_NAMES = (*AGENCY_STAFF_ROLE_NAMES, CARETAKER_ROLE)


def requires_finance_mfa(user):
    if user.is_superuser:
        return True
    return any(has_role(user, role_name) for role_name in FINANCE_ROLE_NAMES)


def has_role(user, role_name):
    profile = getattr(user, "profile", None)
    if profile is not None and profile.agency_memberships.filter(
        roles__isnull=False
    ).exists():
        return profile.agency_memberships.filter(
            agency__is_active=True,
            is_active=True,
            roles__name=role_name,
        ).exists()
    return user.groups.filter(name=role_name).exists()
