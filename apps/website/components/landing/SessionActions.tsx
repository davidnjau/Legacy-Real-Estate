'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowRight, LogOut } from 'lucide-react';

type User = { name?: string; roles?: string[] };

function dashboardPath(user: User) {
  const roles = user.roles || [];
  if (roles.includes('Tenant (portal)')) return '/tenant/dashboard';
  if (roles.includes('Landlord (portal)')) return '/landlord/dashboard';
  if (roles.includes('Caretaker')) return '/caretaker/dashboard';
  return '/staff/dashboard';
}

export default function SessionActions() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    fetch('/api/auth/me', { cache: 'no-store' })
      .then(async (response) => {
        if (!response.ok) return;
        const result = await response.json();
        if (result.user) setUser(result.user);
      })
      .catch((error: unknown) => console.error('Unable to check the current session:', error));
  }, []);

  async function signOut() {
    try {
      const csrfResponse = await fetch('/api/auth/csrf', { cache: 'no-store' });
      if (!csrfResponse.ok) throw new Error(`CSRF request failed with status ${csrfResponse.status}.`);
      const csrf = await csrfResponse.json();
      const response = await fetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'X-CSRFToken': csrf.csrfToken },
      });
      if (!response.ok) throw new Error(`Django logout failed with status ${response.status}.`);
      setUser(null);
      router.refresh();
    } catch (error) {
      console.error('Unable to sign out:', error);
    }
  }

  if (!user) {
    return (
      <Link href="/login" className="inline-flex items-center gap-2 rounded-xl bg-[#123b2c] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-emerald-950">
        Sign in <ArrowRight size={15} />
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <Link href={dashboardPath(user)} className="hidden max-w-40 truncate text-sm font-semibold text-slate-700 sm:block">
        {user.name || 'My workspace'}
      </Link>
      <button onClick={signOut} className="inline-flex items-center gap-2 rounded-xl border border-slate-300 px-3 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50">
        <LogOut size={15} /><span className="hidden sm:inline">Sign out</span>
      </button>
    </div>
  );
}
