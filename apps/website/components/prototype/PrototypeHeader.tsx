'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Building2, Home, ShieldCheck, BriefcaseBusiness, LogOut, Sparkles, LayoutGrid } from 'lucide-react';
import type { DjangoUser } from '../../lib/django-auth';

interface PrototypeHeaderProps {
  activeRole: string;
  user?: DjangoUser | null;
}

export function PrototypeHeader({ activeRole, user }: PrototypeHeaderProps) {
  const router = useRouter();

  const handleLogout = async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch {
      // ignore
    } finally {
      window.location.href = '/api/auth/logout';
    }
  };

  const portalShortcuts = [
    { key: 'director', label: 'Director', href: '/admin/dashboard' },
    { key: 'branch-manager', label: 'Manager', href: '/branch-manager/dashboard' },
    { key: 'accountant', label: 'Finance', href: '/accountant/dashboard' },
    { key: 'property-officer', label: 'Leasing', href: '/property-officer/dashboard' },
    { key: 'land-sales', label: 'Land', href: '/land-sales/dashboard' },
    { key: 'caretaker', label: 'Caretaker', href: '/caretaker/dashboard' },
    { key: 'landlord', label: 'Landlord', href: '/landlord/dashboard' },
    { key: 'tenant', label: 'Resident', href: '/tenant/dashboard' },
    { key: 'buyer', label: 'Buyer', href: '/buyer/dashboard' },
  ];

  return (
    <header className="sticky top-0 z-40 border-b border-emerald-950/60 bg-[#0d231a] px-4 py-2 text-white shadow-md">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-2 sm:gap-3">
        {/* Left: Brand & Active Persona */}
        <div className="flex min-w-0 flex-1 items-center gap-2 sm:flex-none sm:gap-3">
          <Link
            href="/login"
            className="flex shrink-0 items-center gap-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1 text-xs font-bold uppercase tracking-wider text-emerald-300 transition hover:bg-emerald-500/20"
            title="Return to Portals Switcher"
          >
            <LayoutGrid size={13} />
            <span>Portals</span>
          </Link>

          <div className="min-w-0 truncate text-[11px] text-emerald-100/90 sm:text-xs">
            <span className="hidden text-slate-400 sm:inline">Account:{' '}</span>
            <strong className="inline-block max-w-[44vw] truncate align-bottom text-white sm:max-w-none">
              {user ? user.name : 'Simulated Persona'}
            </strong>{' '}
            {user?.roles?.[0] && (
              <span className="hidden font-semibold text-emerald-300 sm:inline">({user.roles[0]})</span>
            )}
          </div>
        </div>

        {/* Right: Exit / Sign Out */}
        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          <button
            type="button"
            onClick={handleLogout}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-red-400/40 bg-red-500/10 px-2.5 py-1 text-[11px] font-semibold text-red-200 transition hover:bg-red-500/20 hover:text-white sm:px-3 sm:text-xs"
            title="Sign out & return to portals page"
          >
            <LogOut size={13} />
            <span>Sign out</span>
          </button>
        </div>
      </div>
    </header>
  );
}
