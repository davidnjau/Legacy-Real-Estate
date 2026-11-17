import { cookies } from 'next/headers';

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000/api/v1';

export type DjangoUser = {
  id: number;
  name: string;
  email: string | null;
  phone: string | null;
  roles: string[];
  branches: { id: number; name: string; town: string }[];
  agencies?: {
    id: number;
    name: string;
    slug: string;
    roles: string[];
    branches: { id: number; name: string; town: string }[];
  }[];
};

export const DEMO_USERS: Record<string, DjangoUser> = {
  // 1. Super Admin (Director)
  director: {
    id: 990,
    name: 'Arch. Luhya Dollar (Director)',
    email: 'director@legacyre.co.ke',
    phone: '+254700000001',
    roles: ['Super Admin (Director)'],
    branches: [
      { id: 1, name: 'Nairobi Branch', town: 'Nairobi' },
      { id: 2, name: 'Nakuru Branch', town: 'Nakuru' },
    ],
  },
  'super-admin': {
    id: 990,
    name: 'Arch. Luhya Dollar (Director)',
    email: 'director@legacyre.co.ke',
    phone: '+254700000001',
    roles: ['Super Admin (Director)'],
    branches: [
      { id: 1, name: 'Nairobi Branch', town: 'Nairobi' },
      { id: 2, name: 'Nakuru Branch', town: 'Nakuru' },
    ],
  },

  // 2. Branch Manager
  'branch-manager': {
    id: 991,
    name: 'Joseph Kariuki (Nairobi Branch Manager)',
    email: 'j.kariuki@legacyre.co.ke',
    phone: '+254722000010',
    roles: ['Branch Manager'],
    branches: [{ id: 1, name: 'Nairobi Branch', town: 'Nairobi' }],
  },

  // 3. Accountant
  accountant: {
    id: 992,
    name: 'Grace Wambui, CPA (Lead Accountant)',
    email: 'finance@legacyre.co.ke',
    phone: '+254733000020',
    roles: ['Accountant'],
    branches: [
      { id: 1, name: 'Nairobi Branch', town: 'Nairobi' },
      { id: 2, name: 'Nakuru Branch', town: 'Nakuru' },
    ],
  },

  // 4. Property Officer / Agent
  'property-officer': {
    id: 993,
    name: 'Jane Njeri (Property Officer)',
    email: 'j.njeri@legacyre.co.ke',
    phone: '+254711000030',
    roles: ['Property Officer / Agent'],
    branches: [{ id: 1, name: 'Nairobi Branch', town: 'Nairobi' }],
  },

  // 5. Land Sales Agent
  'land-sales': {
    id: 994,
    name: 'Peter Kiprono (Land Sales Executive)',
    email: 'p.kiprono@legacyre.co.ke',
    phone: '+254777000040',
    roles: ['Land Sales Agent'],
    branches: [{ id: 2, name: 'Nakuru Branch', town: 'Nakuru' }],
  },

  // 6. Caretaker
  caretaker: {
    id: 995,
    name: 'Samuel Otieno (Caretaker - Kahawa Court)',
    email: 'samuel.otieno@example.com',
    phone: '+254722000002',
    roles: ['Caretaker'],
    branches: [{ id: 1, name: 'Nairobi Branch', town: 'Nairobi' }],
  },

  // 7. Landlord (portal)
  landlord: {
    id: 996,
    name: 'Dr. James Mwangi (Property Owner)',
    email: 'james.mwangi@example.com',
    phone: '+254711000001',
    roles: ['Landlord (portal)'],
    branches: [{ id: 1, name: 'Nairobi Branch', town: 'Nairobi' }],
  },

  // 8. Tenant (portal)
  tenant: {
    id: 997,
    name: 'Brian Omondi (Tenant - Kahawa Court B04)',
    email: 'brian.omondi@example.com',
    phone: '+254733000003',
    roles: ['Tenant (portal)'],
    branches: [{ id: 1, name: 'Nairobi Branch', town: 'Nairobi' }],
  },

  // 9. Land Buyer (portal)
  buyer: {
    id: 998,
    name: 'Mary Achieng (Land Buyer - Bahati Gardens)',
    email: 'mary.achieng@example.com',
    phone: '+254755000050',
    roles: ['Land Buyer (portal)'],
    branches: [{ id: 2, name: 'Nakuru Branch', town: 'Nakuru' }],
  },

  // Generic staff demo
  staff: {
    id: 999,
    name: 'David Njau (Agency Staff)',
    email: 'david.njau@legacyre.co.ke',
    phone: '+254744000004',
    roles: ['Super Admin (Director)', 'Branch Manager', 'Accountant'],
    branches: [
      { id: 1, name: 'Nairobi Branch', town: 'Nairobi' },
      { id: 2, name: 'Nakuru Branch', town: 'Nakuru' },
    ],
  },
};

export async function getDjangoUser(): Promise<DjangoUser | null> {
  const cookieStore = await cookies();

  // 1. First, check if there is a real authenticated Django session
  try {
    const response = await fetch(`${apiBaseUrl}/auth/me`, {
      headers: { Cookie: cookieStore.toString() },
      cache: 'no-store',
    });

    if (response.ok) {
      const payload = await response.json();
      if (payload.user) {
        return payload.user;
      }
    }
  } catch {
    // Backend unreachable or network error
  }

  // 2. If no real session, check demo_role cookie for prototype persona
  const demoRole = cookieStore.get('demo_role')?.value;
  if (demoRole && DEMO_USERS[demoRole]) {
    return DEMO_USERS[demoRole];
  }

  return null;
}

export async function getAuthenticatedDjangoUser(): Promise<DjangoUser | null> {
  const cookieStore = await cookies();

  try {
    const response = await fetch(`${apiBaseUrl}/auth/me`, {
      headers: { Cookie: cookieStore.toString() },
      cache: 'no-store',
    });

    if (!response.ok) return null;
    const payload = await response.json();
    return payload.user ?? null;
  } catch {
    return null;
  }
}

export function hasDjangoRole(user: DjangoUser, role: string) {
  return user.roles.some((item) => item.trim().toLowerCase() === role.trim().toLowerCase());
}
