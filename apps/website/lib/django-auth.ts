import { cookies } from 'next/headers';

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000/api/v1';

export type DjangoUser = {
  id: number;
  name: string;
  email: string | null;
  phone: string | null;
  roles: string[];
  branches: { id: number; name: string; town: string }[];
};

export async function getDjangoUser(): Promise<DjangoUser | null> {
  const cookieStore = await cookies();
  const response = await fetch(`${apiBaseUrl}/auth/me`, {
    headers: { Cookie: cookieStore.toString() },
    cache: 'no-store',
  });

  if (!response.ok) return null;
  const payload = await response.json();
  return payload.user || null;
}

export function hasDjangoRole(user: DjangoUser, role: string) {
  return user.roles.some((item) => item.trim().toLowerCase() === role.trim().toLowerCase());
}
