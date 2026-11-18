'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { createContext, useContext, useEffect, useState } from 'react';
import {
  Activity,
  Building2,
  ClipboardList,
  LayoutDashboard,
  LockKeyhole,
  Receipt,
  Settings,
  Users,
} from 'lucide-react';
import type { DjangoUser } from '../../lib/django-auth';
import SignOutButton from '../auth/SignOutButton';
import { ResponsivePortalSidebar } from '../ui/ResponsivePortalSidebar';

type Agency = NonNullable<DjangoUser['agencies']>[number];

type DirectorPortalContextValue = {
  agency: Agency | null;
  branchId: number | 'all';
  userName: string;
};

const DirectorPortalContext = createContext<DirectorPortalContextValue | null>(null);

export function useDirectorPortal() {
  const context = useContext(DirectorPortalContext);
  if (!context) throw new Error('Director portal context is unavailable.');
  return context;
}

const NAV_ITEMS = [
  { href: '/admin/dashboard', label: 'Executive overview', icon: LayoutDashboard },
  { href: '/admin/staff', label: 'Staff & access', icon: Users },
  { href: '/admin/properties', label: 'Properties', icon: Building2 },
  { href: '/admin/financials', label: 'Financials', icon: Receipt },
  { href: '/admin/audit', label: 'Audit trail', icon: ClipboardList },
  { href: '/admin/settings', label: 'Policy & settings', icon: Settings },
];

export function DirectorPortalShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [user, setUser] = useState<DjangoUser | null>(null);
  const [agencyId, setAgencyId] = useState<number | null>(null);
  const [branchId, setBranchId] = useState<number | 'all'>('all');
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    void fetch('/api/auth/me', { cache: 'no-store' })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok || !payload.user) throw new Error(payload.detail || 'Unable to load your account.');
        if (!active) return;
        const currentUser = payload.user as DjangoUser;
        setUser(currentUser);
        const agencies = (currentUser.agencies ?? []).filter((item) =>
          item.roles.some((role) => role.trim().toLowerCase() === 'super admin (director)'),
        );
        setAgencyId(agencies[0]?.id ?? null);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : 'Unable to load your account.');
      });
    return () => {
      active = false;
    };
  }, []);

  const agencies = (user?.agencies ?? []).filter((item) =>
    item.roles.some((role) => role.trim().toLowerCase() === 'super admin (director)'),
  );
  const agency = agencies.find((item) => item.id === agencyId) ?? null;
  const branches = agency?.branches ?? [];

  return (
    <DirectorPortalContext.Provider value={{ agency, branchId, userName: user?.name || 'Director' }}>
      <div className="min-h-screen bg-[#f5f6f4] text-[#172820]">
        <header className="sticky top-0 z-30 flex min-h-[76px] flex-wrap items-center justify-between gap-3 border-b border-[#e6e9e5] bg-white/95 px-4 py-3 shadow-[0_3px_16px_rgba(20,40,31,0.04)] backdrop-blur sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-4">
            <Link href="/admin/dashboard" className="flex shrink-0 items-center gap-3">
              <span className="grid h-11 w-11 place-items-center rounded-2xl bg-[#163a2e] text-[#c5f06e] shadow-[0_5px_14px_rgba(22,58,46,0.18)]">
                <Building2 size={21} strokeWidth={1.8} />
              </span>
              <span>
                <span className="block text-[10px] font-bold uppercase tracking-[0.2em] text-[#698074]">Legacy Real Estate</span>
                <span className="block truncate text-sm font-bold text-[#18372b]">{user?.name || 'Director portal'}</span>
              </span>
            </Link>
            <span className="hidden h-8 w-px bg-slate-200 lg:block" />
            <span className="hidden items-center gap-2 rounded-full bg-[#f3f6f2] px-3 py-1.5 text-xs font-semibold text-[#466253] lg:inline-flex">
              <Activity size={14} className="text-[#668d56]" />
              Executive workspace
            </span>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {agencies.length > 1 && (
              <select aria-label="Agency" value={agency?.id ?? ''} onChange={(event) => {
                setAgencyId(Number(event.target.value));
                setBranchId('all');
              }} className="max-w-[44vw] rounded-xl border border-[#dfe5df] bg-white px-2 py-2.5 text-xs font-semibold text-[#294536] outline-none transition focus:border-[#89a66b] focus:ring-4 focus:ring-[#eaf1e4] sm:max-w-56 sm:px-3">
                {agencies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            )}
            {branches.length > 1 && (
              <select aria-label="Branch" value={branchId} onChange={(event) => setBranchId(event.target.value === 'all' ? 'all' : Number(event.target.value))}
                className="max-w-[44vw] rounded-xl border border-[#dfe5df] bg-white px-2 py-2.5 text-xs font-semibold text-[#294536] outline-none transition focus:border-[#89a66b] focus:ring-4 focus:ring-[#eaf1e4] sm:max-w-56 sm:px-3">
                <option value="all">All agency branches</option>
                {branches.map((branch) => <option key={branch.id} value={branch.id}>{branch.name}</option>)}
              </select>
            )}
            {branches.length === 1 && <span className="rounded-xl border border-[#dfe5df] bg-[#f8faf7] px-3 py-2.5 text-xs font-semibold text-[#45604f]">{branches[0].name}</span>}
            <SignOutButton />
          </div>
        </header>
        <div className="mx-auto flex min-h-[calc(100dvh-4.75rem)] max-w-[1760px] flex-col md:flex-row">
          <ResponsivePortalSidebar
            label="Director navigation"
            desktopWidthClass="md:w-[260px]"
            className="border-r border-[#e6e9e5] bg-white px-4 py-7"
          >
            <div className="mb-5 px-3">
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#91a095]">Workspace</p>
              <p className="mt-1 text-xs text-[#8a978e]">{agency?.name || 'Agency administration'}</p>
            </div>
            <nav className="space-y-1.5">
              {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
                const active = pathname === href || (href === '/admin/dashboard' && pathname === '/admin');
                return <Link key={href} href={href} aria-current={active ? 'page' : undefined}
                  className={`group flex items-center gap-3 rounded-xl border px-3 py-3 text-[13px] transition ${active ? 'border-[#e1ebd8] bg-[#f1f6ed] font-bold text-[#234630] shadow-[0_2px_8px_rgba(30,60,40,0.04)]' : 'border-transparent font-medium text-[#68776d] hover:bg-[#f7f9f6] hover:text-[#234630]'}`}>
                  <Icon size={17} strokeWidth={active ? 2.2 : 1.8} className={active ? 'text-[#668a4c]' : 'text-[#91a095] transition group-hover:text-[#668a4c]'} />
                  <span className="flex-1">{label}</span>
                  {active && <span className="h-1.5 w-1.5 rounded-full bg-[#83a95d]" />}
                </Link>;
              })}
            </nav>
            <div className="mt-auto rounded-2xl border border-[#e7ece5] bg-[#f8faf7] p-4">
              <span className="grid h-8 w-8 place-items-center rounded-xl bg-white text-[#638256] shadow-sm"><LockKeyhole size={15} /></span>
              <p className="mt-3 text-xs font-bold text-[#314b39]">Director access</p>
              <p className="mt-1 text-[11px] leading-relaxed text-[#839087]">Your agency workspace is secured and access-controlled.</p>
            </div>
          </ResponsivePortalSidebar>
          <main className="min-w-0 flex-1 overflow-x-hidden">
            {error ? <div role="alert" className="m-6 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</div> : children}
          </main>
        </div>
      </div>
    </DirectorPortalContext.Provider>
  );
}
