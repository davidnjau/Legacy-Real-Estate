import { createClient } from './supabase/server';
import { redirect } from 'next/navigation';

export type UserRole = 'landlord' | 'caretaker' | 'tenant';

export async function getCurrentUser() {
  const supabase = await createClient();
  const {
    data: { user },
    error,
  } = await supabase.auth.getUser();

  if (error || !user) {
    return null;
  }

  const { data: profile, error: profileError } = await supabase
    .from('users')
    .select('*')
    .eq('id', user.id)
    .single();

  if (profileError || !profile) {
    return null;
  }

  return {
    ...user,
    profile,
    role: profile.role as UserRole,
  };
}

export async function requireRole(allowedRoles: UserRole[]) {
  const user = await getCurrentUser();

  if (!user) {
    redirect('/login');
  }

  if (!allowedRoles.includes(user.role)) {
    redirect('/login');
  }

  return user;
}

export async function getCurrentUserOrNull() {
  return getCurrentUser();
}
