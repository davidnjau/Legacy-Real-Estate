SUPER_ADMIN_ROLE = "Super Admin (Director)"
ROLE_NAMES = (
    SUPER_ADMIN_ROLE,
    "Branch Manager",
    "Accountant",
    "Property Officer / Agent",
    "Land Sales Agent",
    "Caretaker",
    "Landlord (portal)",
    "Tenant (portal)",
    "Land Buyer (portal)",
)
FINANCE_ROLE_NAMES = ROLE_NAMES[:3]


def requires_finance_mfa(user):
    return user.is_superuser or user.groups.filter(name__in=FINANCE_ROLE_NAMES).exists()
