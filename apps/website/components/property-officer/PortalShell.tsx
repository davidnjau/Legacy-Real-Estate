'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  Building2,
  CalendarDays,
  CalendarRange,
  ClipboardCheck,
  FileText,
  LayoutDashboard,
  LockKeyhole,
  Map,
  Receipt,
  Users,
  UserPlus,
  UserRoundCog,
  Upload,
  Wrench,
  WalletCards,
} from 'lucide-react';
import type { DjangoUser } from '../../lib/django-auth';
import { isPropertyOfficerRole } from '../../lib/django-routing';
import SignOutButton from '../auth/SignOutButton';

type Agency = NonNullable<DjangoUser['agencies']>[number];

type PortalContextValue = {
  user: DjangoUser | null;
  agency: Agency | null;
  branchIds: Set<number>;
  branchTownKeys: Set<string>;
};

const PortalContext = createContext<PortalContextValue | null>(null);

export function usePropertyOfficerPortal() {
  const context = useContext(PortalContext);
  if (!context) throw new Error('Property Officer portal context is unavailable.');
  return context;
}

const NAV_GROUPS = [
  {
    label: 'Portfolio',
    items: [
      { href: '/property-officer/dashboard', label: 'Overview', icon: LayoutDashboard },
      { href: '/property-officer/properties', label: 'Properties', icon: Building2 },
      { href: '/property-officer/units', label: 'Units & vacancies', icon: Map },
    ],
  },
  {
    label: 'People',
    items: [
      { href: '/property-officer/tenants', label: 'Tenants & onboarding', icon: Users },
      { href: '/property-officer/landlords', label: 'Landlords & onboarding', icon: UserPlus },
      { href: '/property-officer/caretakers', label: 'Caretakers & onboarding', icon: UserRoundCog },
    ],
  },
  {
    label: 'Operations',
    items: [
      { href: '/property-officer/handovers', label: 'Move-in handovers', icon: ClipboardCheck },
      { href: '/property-officer/leases', label: 'Leases', icon: FileText },
      { href: '/property-officer/viewings', label: 'Leads & viewings', icon: CalendarDays },
      { href: '/property-officer/maintenance', label: 'Maintenance', icon: Wrench },
      { href: '/property-officer/billing', label: 'Billing wizard', icon: Receipt },
    ],
  },
  {
    label: 'Records',
    items: [
      { href: '/property-officer/imports', label: 'Legacy data import', icon: Upload },
      { href: '/property-officer/ledger', label: 'Migration ledger', icon: WalletCards },
      { href: '/property-officer/monthly-ledger', label: 'Monthly property ledger', icon: CalendarRange },
    ],
  },
];
const NAV_ITEMS = NAV_GROUPS.flatMap((group) => group.items);

function townKey(branch: { name: string; town: string }) {
  return (branch.town || branch.name.replace(/\s+(branch|office)$/i, '')).trim().toLowerCase();
}

export function PropertyOfficerPortalShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [user, setUser] = useState<DjangoUser | null>(null);
  const [selectedAgencyId, setSelectedAgencyId] = useState<number | null>(null);
  const [selectedBranchId, setSelectedBranchId] = useState<number | 'all'>('all');
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
        const agencies = (currentUser.agencies ?? []).filter((agency) =>
          agency.roles.some(isPropertyOfficerRole),
        );
        setSelectedAgencyId(agencies[0]?.id ?? null);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : 'Unable to load your account.');
      });
    return () => {
      active = false;
    };
  }, []);

  const agencies = (user?.agencies ?? []).filter((item) =>
    item.roles.some(isPropertyOfficerRole),
  );
  const agency = agencies.find((item) => item.id === selectedAgencyId) ?? null;
  const branches = agency?.branches ?? [];
  const branchIds = useMemo(
    () => new Set(branches.filter((branch) => selectedBranchId === 'all' || branch.id === selectedBranchId).map((branch) => branch.id)),
    [branches, selectedBranchId],
  );
  const branchTownKeys = useMemo(
    () => new Set(branches.filter((branch) => branchIds.has(branch.id)).map(townKey)),
    [branches, branchIds],
  );

  function changeAgency(agencyId: number) {
    setSelectedAgencyId(agencyId);
    setSelectedBranchId('all');
  }

  return (
    <PortalContext.Provider value={{ user, agency, branchIds, branchTownKeys }}>
      <div className="min-h-screen bg-[#f5f6f4] text-[#172820]">
        <header className="sticky top-0 z-30 flex min-h-[76px] flex-wrap items-center justify-between gap-3 border-b border-[#e6e9e5] bg-white/95 px-4 py-3 shadow-[0_3px_16px_rgba(20,40,31,0.04)] backdrop-blur sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-4">
            <Link href="/property-officer/dashboard" className="flex shrink-0 items-center gap-3">
              <span className="grid h-11 w-11 place-items-center rounded-2xl bg-[#163a2e] text-[#c5f06e] shadow-[0_5px_14px_rgba(22,58,46,0.18)]">
                <Building2 size={21} strokeWidth={1.8} />
              </span>
              <span>
                <span className="block text-[10px] font-bold uppercase tracking-[0.2em] text-[#698074]">Legacy Real Estate</span>
                <span className="block truncate text-sm font-bold text-[#18372b]">{user?.name || 'Property operations'}</span>
              </span>
            </Link>
            <span className="hidden h-8 w-px bg-slate-200 lg:block" />
            <span className="hidden items-center gap-2 rounded-full bg-[#f3f6f2] px-3 py-1.5 text-xs font-semibold text-[#466253] lg:inline-flex">
              <Activity size={14} className="text-[#668d56]" />
              Agency operations
            </span>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {agencies.length > 1 && (
              <select
                aria-label="Agency"
                value={agency?.id ?? ''}
                onChange={(event) => changeAgency(Number(event.target.value))}
                className="max-w-52 rounded-xl border border-[#dfe5df] bg-white px-3 py-2.5 text-xs font-semibold text-[#294536] outline-none transition focus:border-[#89a66b] focus:ring-4 focus:ring-[#eaf1e4]"
              >
                {agencies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            )}
            {branches.length > 1 && (
              <select
                aria-label="Branch"
                value={selectedBranchId}
                onChange={(event) => setSelectedBranchId(event.target.value === 'all' ? 'all' : Number(event.target.value))}
                className="max-w-56 rounded-xl border border-[#dfe5df] bg-white px-3 py-2.5 text-xs font-semibold text-[#294536] outline-none transition focus:border-[#89a66b] focus:ring-4 focus:ring-[#eaf1e4]"
              >
                <option value="all">All assigned branches</option>
                {branches.map((branch) => <option key={branch.id} value={branch.id}>{branch.name}</option>)}
              </select>
            )}
            {branches.length === 1 && (
              <span className="rounded-xl border border-[#dfe5df] bg-[#f8faf7] px-3 py-2.5 text-xs font-semibold text-[#45604f]">{branches[0].name}</span>
            )}
            <SignOutButton />
          </div>
        </header>
        <div className="mx-auto flex min-h-[calc(100vh-4.75rem)] max-w-[1760px]">
          <aside className="hidden w-[260px] shrink-0 border-r border-[#e6e9e5] bg-white px-4 py-7 md:flex md:flex-col">
            <div className="mb-5 px-3">
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#91a095]">Workspace</p>
              <p className="mt-1 truncate text-xs text-[#8a978e]">{agency?.name || 'Property operations'}</p>
            </div>
            <nav className="space-y-5">
              {NAV_GROUPS.map(({ label: groupLabel, items }) => (
                <div key={groupLabel}>
                  <p className="px-3 pb-2 text-[9px] font-bold uppercase tracking-[0.18em] text-[#a0aaa2]">{groupLabel}</p>
                  <div className="space-y-1">
                    {items.map(({ href, label, icon: Icon }) => {
                      const active = pathname === href;
                      return (
                        <Link key={href} href={href} aria-current={active ? 'page' : undefined}
                          className={`group flex items-center gap-3 rounded-xl border px-3 py-2.5 text-[12px] transition ${active ? 'border-[#e1ebd8] bg-[#f1f6ed] font-bold text-[#234630] shadow-[0_2px_8px_rgba(30,60,40,0.04)]' : 'border-transparent font-medium text-[#68776d] hover:bg-[#f7f9f6] hover:text-[#234630]'}`}>
                          <Icon size={16} strokeWidth={active ? 2.2 : 1.8} className={active ? 'text-[#668a4c]' : 'text-[#91a095] transition group-hover:text-[#668a4c]'} />
                          <span className="flex-1">{label}</span>
                          {active && <span className="h-1.5 w-1.5 rounded-full bg-[#83a95d]" />}
                        </Link>
                      );
                    })}
                  </div>
                </div>
              ))}
            </nav>
            <div className="mt-auto pt-6">
              <div className="rounded-2xl border border-[#e7ece5] bg-[#f8faf7] p-4">
                <span className="grid h-8 w-8 place-items-center rounded-xl bg-white text-[#638256] shadow-sm"><LockKeyhole size={15} /></span>
                <p className="mt-3 text-xs font-bold text-[#314b39]">Branch-scoped access</p>
                <p className="mt-1 text-[11px] leading-relaxed text-[#839087]">Operational records reflect your assigned agency and branches.</p>
              </div>
            </div>
          </aside>
          <div className="min-w-0 flex-1">
            <div className="border-b border-[#e6e9e5] bg-white px-4 py-2.5 md:hidden">
              <nav className="flex gap-2 overflow-x-auto">
                {NAV_ITEMS.map(({ href, label }) => (
                  <Link key={href} href={href} aria-current={pathname === href ? 'page' : undefined} className={`shrink-0 rounded-xl px-3 py-2 text-xs font-semibold transition ${pathname === href ? 'bg-[#edf4e9] text-[#294d35]' : 'text-[#6b7b70] hover:bg-[#f6f8f5]'}`}>
                    {label}
                  </Link>
                ))}
              </nav>
            </div>
            {error ? (
              <div role="alert" className="m-6 rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</div>
            ) : children}
          </div>
        </div>
      </div>
    </PortalContext.Provider>
  );
}
