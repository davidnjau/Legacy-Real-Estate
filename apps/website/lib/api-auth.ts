import { NextResponse } from 'next/server';
import { createClient } from './supabase/server';

export type ApiRole = 'landlord' | 'caretaker' | 'tenant';

export async function getApiUser() {
  const supabase = await createClient();
  const {
    data: { user },
    error: authError,
  } = await supabase.auth.getUser();

  if (authError || !user) return null;

  const { data: profile, error: profileError } = await supabase
    .from('users')
    .select('id, role, status')
    .eq('id', user.id)
    .maybeSingle();

  if (profileError || !profile) return null;

  return {
    user,
    profile,
    role: String(profile.role || '').trim().toLowerCase() as ApiRole,
  };
}

export async function requireApiRole(allowedRoles: ApiRole[]) {
  const currentUser = await getApiUser();

  if (!currentUser) {
    return {
      currentUser: null,
      response: NextResponse.json({ error: 'Unauthorized' }, { status: 401 }),
    };
  }

  if (!allowedRoles.includes(currentUser.role)) {
    return {
      currentUser: null,
      response: NextResponse.json({ error: 'Forbidden' }, { status: 403 }),
    };
  }

  return { currentUser, response: null };
}