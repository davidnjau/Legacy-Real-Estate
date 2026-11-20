'use client';

import Link from 'next/link';
import { Building2, LayoutGrid, LogOut } from 'lucide-react';
import type { DjangoUser } from '../../lib/django-auth';

interface PrototypeHeaderProps {
  activeRole: string;
  user?: DjangoUser | null;
}

export function PrototypeHeader({ activeRole, user }: PrototypeHeaderProps) {
  const handleLogout = async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch {
      // ignore
    } finally {
      window.location.href = '/api/auth/logout';
    }
  };

  const roleLabels: Record<string, string> = {
    staff: 'Agency workspace',
    landlord: 'Property owner workspace',
    caretaker: 'Field operations',
    tenant: 'Resident workspace',
  };
  const roleHomePaths: Record<string, string> = {
    staff: '/staff/dashboard',
    landlord: '/landlord/dashboard',
    caretaker: '/caretaker/dashboard',
    tenant: '/tenant/dashboard',
  };

  return (
    <header className="sticky top-0 z-40 flex min-h-[72px] shrink-0 flex-wrap items-center justify-between gap-3 border-b border-[#e6e9e5] bg-white/95 px-4 py-3 text-[#172820] shadow-[0_3px_16px_rgba(20,40,31,0.04)] backdrop-blur sm:px-6 lg:px-8">
      <div className="flex min-w-0 items-center gap-3 sm:gap-4">
        <Link
          href={roleHomePaths[activeRole] || '/login'}
          aria-label="Legacy Real Estate home"
          className="flex shrink-0 items-center gap-3"
          title="Go to workspace home"
        >
          <span className="grid h-10 w-10 place-items-center rounded-2xl bg-[#163a2e] text-[#c5f06e] shadow-[0_5px_14px_rgba(22,58,46,0.18)]">
            <Building2 size={20} strokeWidth={1.8} />
          </span>
          <span className="hidden sm:block">
            <span className="block text-[10px] font-bold uppercase tracking-[0.18em] text-[#829087]">Legacy Real Estate</span>
            <span className="block max-w-[28vw] truncate text-sm font-bold text-[#18372b]">{user?.name || 'Portal workspace'}</span>
          </span>
        </Link>
        <span className="hidden h-8 w-px bg-slate-200 sm:block" />
        <span className="inline-flex items-center gap-2 rounded-full bg-[#f3f6f2] px-3 py-1.5 text-[11px] font-semibold text-[#466253] sm:text-xs">
          <span className="h-1.5 w-1.5 rounded-full bg-[#83a95d]" aria-hidden="true" />
          {roleLabels[activeRole] || activeRole}
        </span>
        <div className="min-w-0 sm:hidden">
          <span className="block max-w-[35vw] truncate text-xs font-bold text-[#18372b]">{user?.name || 'Portal workspace'}</span>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2">
        <Link
          href="/login"
          className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-[#dfe5df] bg-white px-3 py-2 text-xs font-semibold text-[#385343] transition hover:border-[#cbd8c8] hover:bg-[#f5f8f3]"
          title="Return to portals"
        >
          <LayoutGrid size={15} />
          <span>Portals</span>
        </Link>
        <button
          type="button"
          onClick={handleLogout}
          className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-[#f1d8d5] bg-[#fff9f8] px-3 py-2 text-xs font-semibold text-[#9c4d45] transition hover:border-[#e9c2bd] hover:bg-[#fff1ef]"
          title="Sign out"
        >
          <LogOut size={15} />
          <span>Sign out</span>
        </button>
      </div>
    </header>
  );
}
