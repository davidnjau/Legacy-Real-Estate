SUPER_ADMIN_ROLE = "Super Admin (Director)"
CARETAKER_ROLE = "Caretaker"
LANDLORD_PORTAL_ROLE = "Landlord (portal)"
TENANT_PORTAL_ROLE = "Tenant (portal)"
ROLE_NAMES = (
    SUPER_ADMIN_ROLE,
    "Branch Manager",
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


def requires_finance_mfa(user):
    return user.is_superuser or user.groups.filter(name__in=FINANCE_ROLE_NAMES).exists()
