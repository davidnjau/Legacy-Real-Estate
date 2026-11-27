export function isPropertyOfficerRole(role: string) {
  const normalizedRole = role.trim().toLowerCase();
  return normalizedRole === 'property officer / agent' || normalizedRole === 'property officer';
}

export function getDjangoWorkspaceOptions(roles: string[]) {
  const normalizedRoles = new Set(roles.map((role) => role.trim().toLowerCase()));
  const options = [
    {
      label: 'Director',
      path: '/admin/dashboard',
      roles: ['super admin (director)', 'super admin', 'director'],
    },
    { label: 'Branch Manager', path: '/branch-manager/dashboard', roles: ['branch manager'] },
    { label: 'Accountant', path: '/accountant/dashboard', roles: ['accountant'] },
    {
      label: 'Property Officer',
      path: '/property-officer/dashboard',
      roles: ['property officer / agent', 'property officer'],
    },
    {
      label: 'Land Sales Agent',
      path: '/land-sales/dashboard',
      roles: ['land sales agent', 'land sales'],
    },
    { label: 'Caretaker', path: '/caretaker/dashboard', roles: ['caretaker'] },
    {
      label: 'Landlord',
      path: '/landlord/dashboard',
      roles: ['landlord (portal)', 'landlord'],
    },
    { label: 'Tenant', path: '/tenant/dashboard', roles: ['tenant (portal)', 'tenant'] },
    {
      label: 'Land Buyer',
      path: '/buyer/dashboard',
      roles: ['land buyer (portal)', 'land buyer', 'buyer'],
    },
  ].filter((option) => option.roles.some((role) => normalizedRoles.has(role)));

  if (
    options.length === 0
    && ['staff', 'agent', 'manager'].some((role) =>
      [...normalizedRoles].some((item) => item.includes(role)),
    )
  ) {
    options.push({ label: 'Staff', path: '/staff/dashboard', roles: ['staff'] });
  }
  return options.map(({ label, path }) => ({ label, path }));
}

export function getDjangoHomePath(roles: string[]): string {
  const normalizedRoles = new Set(roles.map((role) => role.trim().toLowerCase()));

  // 1. Super Admin (Director)
  if (normalizedRoles.has('super admin (director)') || normalizedRoles.has('super admin') || normalizedRoles.has('director')) {
    return '/admin/dashboard';
  }

  // 2. Branch Manager
  if (normalizedRoles.has('branch manager')) {
    return '/branch-manager/dashboard';
  }

  // 3. Accountant
  if (normalizedRoles.has('accountant')) {
    return '/accountant/dashboard';
  }

  // 4. Property Officer / Agent
  if ([...normalizedRoles].some(isPropertyOfficerRole)) {
    return '/property-officer/dashboard';
  }

  // 5. Land Sales Agent
  if (normalizedRoles.has('land sales agent') || normalizedRoles.has('land sales')) {
    return '/land-sales/dashboard';
  }

  // 6. Caretaker
  if (normalizedRoles.has('caretaker')) {
    return '/caretaker/dashboard';
  }

  // 7. Landlord (portal)
  if (normalizedRoles.has('landlord (portal)') || normalizedRoles.has('landlord')) {
    return '/landlord/dashboard';
  }

  // 8. Tenant (portal)
  if (normalizedRoles.has('tenant (portal)') || normalizedRoles.has('tenant')) {
    return '/tenant/dashboard';
  }

  // 9. Land Buyer (portal)
  if (normalizedRoles.has('land buyer (portal)') || normalizedRoles.has('land buyer') || normalizedRoles.has('buyer')) {
    return '/buyer/dashboard';
  }

  // Fallback for any other agency staff role
  if (
    [
      'staff',
      'agent',
      'manager',
    ].some((role) => [...normalizedRoles].some((r) => r.includes(role)))
  ) {
    return '/staff/dashboard';
  }

  return '/login';
}
