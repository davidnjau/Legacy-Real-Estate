'use client';

import { usePathname } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  ArrowUpRight,
  Building2,
  CalendarDays,
  ClipboardCheck,
  FileText,
  Map as MapIcon,
  TrendingUp,
  Users,
  WalletCards,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import { usePropertyOfficerPortal } from './PortalShell';
import PropertyUnitManager from './PropertyUnitManager';
import CaretakerManager from './CaretakerManager';
import TenantOnboarding from './TenantOnboarding';
import MaintenanceBoard from '../maintenance/MaintenanceBoard';
import LeadViewingManager from './LeadViewingManager';
import LegacyImportManager from './LegacyImportManager';
import MigrationLedger from './MigrationLedger';
import MonthlyPropertyLedger from './MonthlyPropertyLedger';

type PropertyRecord = {
  id: number;
  branch_id: number;
  name: string;
};

type UnitRecord = {
  id: number;
  property_id: number;
  status: string;
};

type TenancyRecord = {
  id: number;
  unit_id: number;
  unit_code: string;
  property_name: string;
  tenant_name: string;
  start_date: string;
  end_date: string | null;
  status: string;
};

type LeadRecord = {
  id: number;
  branch_id: number;
};

type ViewingRecord = {
  id: number;
  lead_id: number;
};

type TicketRecord = { id: string };

type DashboardData = {
  properties: PropertyRecord[];
  units: UnitRecord[];
  tenancies: TenancyRecord[];
  leads: LeadRecord[];
  viewings: ViewingRecord[];
  openMaintenance: number;
};

type ApiPayload<T> = T & { detail?: string; error?: string };

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: 'no-store' });
  const payload = await response.json() as ApiPayload<T>;
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `Request failed with status ${response.status}.`);
  }
  return payload;
}

function TenancyRegister() {
  const { agency, branchIds } = usePropertyOfficerPortal();
  const [records, setRecords] = useState<TenancyRecord[]>([]);
  const [properties, setProperties] = useState<PropertyRecord[]>([]);
  const [units, setUnits] = useState<UnitRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!agency) return;
    let active = true;
    setLoading(true);
    setError('');
    void Promise.all([
      getJson<{ tenancies: TenancyRecord[] }>('/api/property-officer/tenancies'),
      getJson<{ properties: PropertyRecord[] }>('/api/property-officer/properties'),
      getJson<{ units: UnitRecord[] }>('/api/property-officer/units'),
    ]).then(([tenancyData, propertyData, unitData]) => {
      if (!active) return;
      setRecords(tenancyData.tenancies);
      setProperties(propertyData.properties);
      setUnits(unitData.units);
    }).catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : 'Unable to load tenancy records.');
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => {
      active = false;
    };
  }, [agency]);

  const visibleProperties = useMemo(
    () => properties.filter((property) => branchIds.has(property.branch_id)),
    [branchIds, properties],
  );
  const visiblePropertyIds = useMemo(
    () => new Set(visibleProperties.map((property) => property.id)),
    [visibleProperties],
  );
  const visibleRecords = useMemo(
    () => {
      const propertyByUnitId = new Map(units.map((unit) => [unit.id, unit.property_id]));
      return records.filter((tenancy) => visiblePropertyIds.has(propertyByUnitId.get(tenancy.unit_id) ?? -1));
    },
    [records, units, visiblePropertyIds],
  );
  const propertyById = useMemo(
    () => new Map(visibleProperties.map((property) => [property.id, property])),
    [visibleProperties],
  );

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
      <header>
        <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-800">{agency?.name || 'Agency'}</p>
        <h1 className="mt-1 text-2xl font-bold text-slate-950">Tenancy register</h1>
        <p className="mt-1 text-sm text-slate-500">Saved tenancy and tenant assignments in your branch scope.</p>
      </header>
      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}
      {loading ? <p role="status" className="text-sm text-slate-500">Loading tenancy records…</p> : (
        <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>{['Tenant', 'Property', 'Unit', 'Start date', 'End date', 'Status'].map((label) => <th key={label} className="px-4 py-3">{label}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {visibleRecords.map((tenancy) => (
                <tr key={tenancy.id}>
                  <td className="px-4 py-3 font-semibold">{tenancy.tenant_name}</td>
                  <td className="px-4 py-3">{tenancy.property_name}</td>
                  <td className="px-4 py-3 font-mono">{tenancy.unit_code}</td>
                  <td className="px-4 py-3">{tenancy.start_date}</td>
                  <td className="px-4 py-3">{tenancy.end_date || '—'}</td>
                  <td className="px-4 py-3">{tenancy.status}</td>
                </tr>
              ))}
              {!visibleRecords.length && (
                <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">No tenancies have been recorded for the selected branches yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}

function Metric({
  label,
  value,
  detail,
  icon: Icon,
  accent,
  progress,
}: {
  label: string;
  value: number;
  detail: string;
  icon: LucideIcon;
  accent: 'green' | 'blue' | 'amber' | 'red';
  progress?: number;
}) {
  const accentClasses = {
    green: 'bg-[#eef5e9] text-[#62844e]',
    blue: 'bg-[#edf3f7] text-[#59788d]',
    amber: 'bg-[#fbf4e8] text-[#a47b3d]',
    red: 'bg-[#f9eeee] text-[#ad6960]',
  };
  const progressValue = Math.min(100, Math.max(0, progress ?? 0));
  return (
    <article className="rounded-2xl border border-[#e7ebe5] bg-white p-5 shadow-[0_8px_24px_rgba(27,48,34,0.035)] transition duration-200 hover:-translate-y-0.5 hover:shadow-[0_14px_30px_rgba(27,48,34,0.07)]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.13em] text-[#849087]">{label}</p>
          <p className="mt-3 text-[28px] font-bold leading-none tracking-tight text-[#20372a]">{value}</p>
        </div>
        <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ${accentClasses[accent]}`}>
          <Icon size={18} strokeWidth={1.9} />
        </span>
      </div>
      <p className="mt-3 text-xs text-[#879188]">{detail}</p>
      {progress !== undefined && (
        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-[#edf0eb]" role="progressbar" aria-label="Unit occupancy" aria-valuenow={progressValue} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-full rounded-full bg-[#82a967] transition-[width]" style={{ width: `${progressValue}%` }} />
        </div>
      )}
    </article>
  );
}

function getGreeting(date: Date) {
  const hour = date.getHours();
  if (hour >= 5 && hour < 12) return 'Good morning';
  if (hour >= 12 && hour < 17) return 'Good afternoon';
  return 'Good evening';
}

function PropertyOfficerOverview() {
  const { user, agency, branchIds } = usePropertyOfficerPortal();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [greeting, setGreeting] = useState('');

  useEffect(() => {
    const updateGreeting = () => setGreeting(getGreeting(new Date()));
    updateGreeting();
    const timer = window.setInterval(updateGreeting, 60_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!agency) return;
    let active = true;
    setLoading(true);
    setError('');
    void Promise.all([
      getJson<{ properties: PropertyRecord[] }>('/api/property-officer/properties'),
      getJson<{ units: UnitRecord[] }>('/api/property-officer/units'),
      getJson<{ tenancies: TenancyRecord[] }>('/api/property-officer/tenancies'),
      getJson<{ leads: LeadRecord[] }>('/api/property-officer/leads'),
      getJson<{ viewings: ViewingRecord[] }>('/api/property-officer/viewings'),
      getJson<{ pending: TicketRecord[]; inProgress: TicketRecord[] }>('/api/maintenance/landlord'),
    ]).then(([propertyData, unitData, tenancyData, leadData, viewingData, maintenanceData]) => {
      if (!active) return;
      setData({
        properties: propertyData.properties,
        units: unitData.units,
        tenancies: tenancyData.tenancies,
        leads: leadData.leads,
        viewings: viewingData.viewings,
        openMaintenance: maintenanceData.pending.length + maintenanceData.inProgress.length,
      });
    }).catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : 'Unable to load the operations overview.');
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => {
      active = false;
    };
  }, [agency]);

  const visibleProperties = useMemo(
    () => (data?.properties ?? []).filter((property) => branchIds.has(property.branch_id)),
    [branchIds, data],
  );
  const visiblePropertyIds = useMemo(
    () => new Set(visibleProperties.map((property) => property.id)),
    [visibleProperties],
  );
  const visibleUnits = useMemo(
    () => (data?.units ?? []).filter((unit) => visiblePropertyIds.has(unit.property_id)),
    [data, visiblePropertyIds],
  );
  const unitIds = useMemo(
    () => new Set(visibleUnits.map((unit) => unit.id)),
    [visibleUnits],
  );
  const activeTenancies = (data?.tenancies ?? []).filter(
    (tenancy) => unitIds.has(tenancy.unit_id) && tenancy.status === 'active',
  );
  const visibleLeads = (data?.leads ?? []).filter((lead) => branchIds.has(lead.branch_id));
  const visibleLeadIds = new Set(visibleLeads.map((lead) => lead.id));
  const visibleViewings = (data?.viewings ?? []).filter((viewing) => visibleLeadIds.has(viewing.lead_id));
  const vacantUnits = visibleUnits.filter((unit) => unit.status.toLowerCase() === 'vacant');
  const occupiedUnits = visibleUnits.filter((unit) => unit.status.toLowerCase() === 'occupied');
  const occupancyRate = visibleUnits.length
    ? Math.round((occupiedUnits.length / visibleUnits.length) * 100)
    : 0;
  const firstName = user?.name?.trim().split(/\s+/)[0] || 'Property Officer';
  const branchLabel = branchIds.size === (agency?.branches.length ?? 0)
    ? 'All assigned branches'
    : `${branchIds.size} selected ${branchIds.size === 1 ? 'branch' : 'branches'}`;

  return (
    <main className="mx-auto max-w-[1440px] space-y-6 px-4 py-7 sm:px-6 lg:px-8 lg:py-9">
      <section className="relative isolate overflow-hidden rounded-[26px] bg-[#173b2e] px-6 py-7 text-white shadow-[0_16px_40px_rgba(23,59,46,0.16)] sm:px-8 sm:py-8">
        <div aria-hidden="true" className="pointer-events-none absolute -right-20 -top-32 -z-10 h-80 w-80 rounded-full border-[42px] border-white/[0.035]" />
        <div aria-hidden="true" className="pointer-events-none absolute -bottom-36 right-28 -z-10 h-64 w-64 rounded-full border-[32px] border-[#c5f06e]/[0.07]" />
        <div className="relative flex flex-wrap items-end justify-between gap-6">
          <div className="max-w-2xl">
            <p className="text-sm font-semibold text-[#c5e1b4]">{greeting ? `${greeting}, ${firstName}` : `Welcome, ${firstName}`}</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-[38px]">Operations at a glance.</h1>
            <p className="mt-3 max-w-xl text-sm leading-relaxed text-white/65">
              {agency?.name || 'Your agency'} · Live property records for your assigned branches.
            </p>
          </div>
          <div className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.07] px-4 py-3 backdrop-blur-sm">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-[#c5f06e] text-[#173b2e]"><MapIcon size={19} /></span>
            <div>
              <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-white/50">Your scope</p>
              <p className="mt-0.5 text-sm font-semibold">{branchLabel}</p>
            </div>
          </div>
        </div>
      </section>
      {error && <p role="alert" className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}
      {loading ? (
        <div role="status" className="flex items-center gap-3 rounded-2xl border border-[#e7ebe5] bg-white p-5 text-sm text-[#718078] shadow-[0_6px_18px_rgba(27,48,34,0.025)]">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-[#dbe5d6] border-t-[#638256]" />
          Loading saved agency records…
        </div>
      ) : data && (
        <>
          <section aria-label="Operational metrics" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Metric label="Properties" value={visibleProperties.length} detail="Registered in selected branches" icon={Building2} accent="blue" />
            <Metric label="Units" value={visibleUnits.length} detail="Imported or saved unit records" icon={MapIcon} accent="blue" />
            <Metric label="Occupied units" value={occupiedUnits.length} detail={`${occupancyRate}% of visible units occupied`} icon={Users} accent="green" progress={occupancyRate} />
            <Metric label="Vacant units" value={vacantUnits.length} detail="Available unit records" icon={Activity} accent="amber" />
            <Metric label="Active tenancies" value={activeTenancies.length} detail="Current saved tenancies" icon={FileText} accent="green" />
            <Metric label="Open maintenance tickets" value={data.openMaintenance} detail="Pending or in progress" icon={Wrench} accent="red" />
            <Metric label="Leads" value={visibleLeads.length} detail="Saved inquiries" icon={TrendingUp} accent="blue" />
            <Metric label="Viewings" value={visibleViewings.length} detail="Saved appointments" icon={CalendarDays} accent="amber" />
          </section>
          <section className="overflow-hidden rounded-2xl border border-[#e7ebe5] bg-white shadow-[0_8px_24px_rgba(27,48,34,0.035)]">
            <div className="border-b border-[#edf0eb] px-5 py-5 sm:px-6">
              <h2 className="text-base font-bold tracking-tight text-[#243b2c]">Quick actions</h2>
              <p className="mt-1 text-xs text-[#89948c]">Jump into the most common property operations.</p>
            </div>
            <div className="grid gap-3 p-4 sm:grid-cols-3 sm:p-5">
              <a href="/property-officer/properties" className="group flex items-center gap-3 rounded-2xl border border-[#edf0eb] p-4 transition hover:border-[#dce7d5] hover:bg-[#f8faf7]">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[#edf4e9] text-[#62844e]"><Building2 size={18} /></span>
                <span className="min-w-0 flex-1"><span className="block text-sm font-bold text-[#344b3b]">Register a property</span><span className="mt-1 block text-xs text-[#89948c]">Add a new portfolio property</span></span>
                <ArrowUpRight size={16} className="text-[#9aa69c] transition group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-[#62844e]" />
              </a>
              <a href="/property-officer/units" className="group flex items-center gap-3 rounded-2xl border border-[#edf0eb] p-4 transition hover:border-[#dce7d5] hover:bg-[#f8faf7]">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[#edf3f7] text-[#59788d]"><ClipboardCheck size={18} /></span>
                <span className="min-w-0 flex-1"><span className="block text-sm font-bold text-[#344b3b]">Review units</span><span className="mt-1 block text-xs text-[#89948c]">Check availability and occupancy</span></span>
                <ArrowUpRight size={16} className="text-[#9aa69c] transition group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-[#62844e]" />
              </a>
              <a href="/property-officer/imports" className="group flex items-center gap-3 rounded-2xl border border-[#edf0eb] p-4 transition hover:border-[#dce7d5] hover:bg-[#f8faf7]">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[#fbf4e8] text-[#a47b3d]"><WalletCards size={18} /></span>
                <span className="min-w-0 flex-1"><span className="block text-sm font-bold text-[#344b3b]">Import records</span><span className="mt-1 block text-xs text-[#89948c]">Bring legacy data into the system</span></span>
                <ArrowUpRight size={16} className="text-[#9aa69c] transition group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-[#62844e]" />
              </a>
            </div>
            {visibleProperties.length === 0 && (
              <p className="mx-5 mb-5 rounded-xl bg-[#f8faf7] px-4 py-3 text-sm text-[#78867b] sm:mx-6">
                No properties are registered for your selected branches yet.
              </p>
            )}
          </section>
        </>
      )}
    </main>
  );
}

export default function PropertyOfficerDashboardPage() {
  const pathname = usePathname();
  const selectedPage = pathname.split('/').filter(Boolean).at(-1) || 'dashboard';

  if (selectedPage === 'properties' || selectedPage === 'units') {
    return <PropertyUnitManager mode={selectedPage} />;
  }
  if (selectedPage === 'tenants') return <TenantOnboarding />;
  if (selectedPage === 'caretakers') return <CaretakerManager />;
  if (selectedPage === 'maintenance') return <MaintenanceBoard />;
  if (selectedPage === 'viewings') return <LeadViewingManager />;
  if (selectedPage === 'imports') return <LegacyImportManager />;
  if (selectedPage === 'ledger') return <MigrationLedger />;
  if (selectedPage === 'monthly-ledger') return <MonthlyPropertyLedger />;
  if (selectedPage === 'leases') return <TenancyRegister />;
  return <PropertyOfficerOverview />;
}
