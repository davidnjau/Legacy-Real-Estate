'use client';

import { usePathname } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertCircle,
  Building2,
  CircleDollarSign,
  MapPin,
  TrendingUp,
  type LucideIcon,
} from 'lucide-react';
import StaffManagement from './StaffManagement';
import { useDirectorPortal } from './DirectorPortalShell';

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 2,
});

type BranchMetric = {
  id: number;
  name: string;
  town: string;
  properties: number;
  units: number;
  occupied_units: number;
  occupancy_rate: number;
  billed: string;
  collected: string;
  arrears: string;
};

type OverviewData = {
  agency: { id: number; name: string };
  period: { from: string; to: string };
  totals: {
    properties: number;
    units: number;
    occupied_units: number;
    occupancy_rate: number;
    billed: string;
    collected: string;
    arrears: string;
  };
  branches: BranchMetric[];
  unmatched_payments: {
    id: number;
    reference: string;
    account_reference: string;
    amount: string;
    received_at: string;
    tenant_name: string | null;
    branch_name: string | null;
  }[];
};

type PropertyData = {
  properties: {
    id: number;
    name: string;
    property_type: string;
    location: string;
    address: string;
    branch: { id: number; name: string; town: string };
    landlord: { id: number; name: string } | null;
    unit_count: number;
    occupied_count: number;
    occupancy_rate: number;
    commission_model: string | null;
    commission_rate: string | null;
    agreement_expiry: string | null;
    units: { id: number; code: string; unit_type: string; status: string }[];
  }[];
};

type FinancialData = {
  summary: {
    billed_rent: string;
    collected_rent: string;
    remittance_count?: number;
    pending_approval_count?: number;
    pending_approval: string;
    approved_payout_count?: number;
    approved_payouts: string;
    paid_payout_count?: number;
    paid_payouts: string;
    tax_withheld: string;
  };
  pending_waiver_count?: number;
  waiver_requests?: {
    id: number;
    invoice_id: number | null;
    invoice_number: string | null;
    invoice_status: string | null;
    invoice_total: string | null;
    waived_amount: string;
    net_amount_due: string | null;
    requested_amount: string;
    reason: string;
    requested_by: string;
    created_at: string;
    billing_month: string;
    tenant_name: string;
    unit_code: string;
    property_name: string;
    branch_name: string;
  }[];
  remittances: {
    id: number;
    landlord_name: string;
    branch_name: string;
    period_start: string;
    period_end: string;
    revision: number;
    collected_amount: string;
    commission_amount: string;
    expenses_amount: string;
    tax_amount: string | null;
    net_payout: string;
    status: string;
    prepared_by: string;
    rejection_reason: string;
    payout_reference: string;
    payout_date: string | null;
  }[];
};

type AuditData = {
  events: {
    id: number;
    timestamp: string;
    user: string;
    email: string | null;
    action: string;
    entity: string;
    old_values: unknown;
    new_values: unknown;
    ip_address: string | null;
  }[];
};

type SettingsData = {
  settings: {
    mri_tax_rate: string;
    commission_vat_enabled: boolean;
    commission_vat_rate: string;
    grace_period_days: number;
    penalty_type: string;
    penalty_rate: string;
    penalty_flat_fee: string;
    sms_templates: Record<string, string>;
  };
  branches: {
    id: number;
    name: string;
    town: string;
    paybill_numbers: string[];
    grace_period_days: number;
    penalty_type: string;
    penalty_rate: string;
    penalty_flat_fee: string;
  }[];
};

type WorkspaceData = OverviewData | PropertyData | FinancialData | AuditData | SettingsData;

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    cache: 'no-store',
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  });
  const payload = await response.json().catch(() => null) as { error?: string; detail?: string } | T | null;
  if (!response.ok) {
    const message = payload && typeof payload === 'object' && ('error' in payload || 'detail' in payload)
      ? payload.error || payload.detail
      : undefined;
    throw new Error(message || `Request failed with status ${response.status}.`);
  }
  if (!payload) throw new Error('The server returned an empty response.');
  return payload as T;
}

function PageTitle({ title, description }: { title: string; description: string }) {
  return (
    <header className="pb-1">
      <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#728779]">Director workspace</p>
      <h1 className="mt-1.5 text-2xl font-bold tracking-tight text-[#1d3528] sm:text-[28px]">{title}</h1>
      <p className="mt-1.5 max-w-3xl text-sm leading-relaxed text-[#748178]">{description}</p>
    </header>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-2xl border border-[#e7ebe5] bg-white p-5 shadow-[0_8px_24px_rgba(27,48,34,0.035)]">
      <p className="text-[11px] font-bold uppercase tracking-[0.13em] text-[#849087]">{label}</p>
      <p className="mt-2 text-2xl font-bold tracking-tight text-[#20372a]">{value}</p>
    </div>
  );
}

function OverviewMetric({
  label,
  value,
  detail,
  icon: Icon,
  accent,
  progress,
}: {
  label: string;
  value: string;
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
          <p className="mt-3 text-[26px] font-bold leading-none tracking-tight text-[#20372a]">{value}</p>
        </div>
        <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ${accentClasses[accent]}`}>
          <Icon size={18} strokeWidth={1.9} />
        </span>
      </div>
      <p className="mt-3 text-xs text-[#879188]">{detail}</p>
      {progress !== undefined && (
        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-[#edf0eb]" role="progressbar" aria-label="Portfolio occupancy" aria-valuenow={progressValue} aria-valuemin={0} aria-valuemax={100}>
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

function OverviewSection({ data, userName }: { data: OverviewData; userName: string }) {
  const [greeting, setGreeting] = useState('');
  useEffect(() => {
    const updateGreeting = () => setGreeting(getGreeting(new Date()));
    updateGreeting();
    const timer = window.setInterval(updateGreeting, 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const firstName = userName.trim().split(/\s+/)[0] || 'Director';
  const occupancy = Number(data.totals.occupancy_rate) || 0;
  const branchCount = data.branches.length;
  return (
    <div className="space-y-6">
      <section className="relative isolate overflow-hidden rounded-[26px] bg-[#173b2e] px-6 py-7 text-white shadow-[0_16px_40px_rgba(23,59,46,0.16)] sm:px-8 sm:py-8">
        <div aria-hidden="true" className="pointer-events-none absolute -right-20 -top-32 -z-10 h-80 w-80 rounded-full border-[42px] border-white/[0.035]" />
        <div aria-hidden="true" className="pointer-events-none absolute -bottom-36 right-28 -z-10 h-64 w-64 rounded-full border-[32px] border-[#c5f06e]/[0.07]" />
        <div className="relative flex flex-wrap items-end justify-between gap-6">
          <div className="max-w-2xl">
            <p className="text-sm font-semibold text-[#c5e1b4]">{greeting ? `${greeting}, ${firstName}` : `Welcome, ${firstName}`}</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-[38px]">A clear view of your portfolio.</h1>
            <p className="mt-3 max-w-xl text-sm leading-relaxed text-white/65">
              {data.agency.name} · Executive overview for {data.period.from} to {data.period.to}.
            </p>
          </div>
          <div className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.07] px-4 py-3 backdrop-blur-sm">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-[#c5f06e] text-[#173b2e]"><Building2 size={19} /></span>
            <div>
              <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-white/50">Portfolio</p>
              <p className="mt-0.5 text-sm font-semibold">{data.totals.properties} properties · {data.totals.units} units</p>
            </div>
          </div>
        </div>
      </section>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <OverviewMetric label="Occupancy" value={`${data.totals.occupancy_rate}%`} detail={`${data.totals.occupied_units} of ${data.totals.units} units occupied`} icon={TrendingUp} accent="green" progress={occupancy} />
        <OverviewMetric label="Properties" value={String(data.totals.properties)} detail={`${branchCount} ${branchCount === 1 ? 'branch' : 'branches'} in this view`} icon={Building2} accent="blue" />
        <OverviewMetric label="Rent collected" value={currency.format(Number(data.totals.collected))} detail="Confirmed collections for this period" icon={CircleDollarSign} accent="green" />
        <OverviewMetric label="Arrears" value={currency.format(Number(data.totals.arrears))} detail="Outstanding across the portfolio" icon={AlertCircle} accent="amber" />
      </div>
      <section className="overflow-hidden rounded-2xl border border-[#e7ebe5] bg-white shadow-[0_8px_24px_rgba(27,48,34,0.035)]">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#edf0eb] px-5 py-5 sm:px-6">
          <div>
            <h2 className="text-base font-bold tracking-tight text-[#243b2c]">Branch performance</h2>
            <p className="mt-1 text-xs text-[#89948c]">A comparative view of occupancy and collection health.</p>
          </div>
          <span className="inline-flex items-center gap-1.5 rounded-full bg-[#f3f6f1] px-3 py-1.5 text-[11px] font-semibold text-[#617565]">
            <MapPin size={13} /> {branchCount} {branchCount === 1 ? 'branch' : 'branches'}
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[840px] text-left text-sm">
            <thead className="bg-[#f8faf7] text-[10px] font-bold uppercase tracking-[0.12em] text-[#87938a]">
              <tr>{['Branch', 'Properties', 'Occupied units', 'Occupancy', 'Billed', 'Collected', 'Arrears'].map((label) => <th key={label} className="px-5 py-3.5">{label}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-[#f0f2ef]">
              {data.branches.map((branch) => (
                <tr key={branch.id} className="transition-colors hover:bg-[#fbfcfa]">
                  <td className="px-5 py-4 font-semibold text-[#2b4333]">{branch.name}<span className="mt-0.5 block text-xs font-normal text-[#909a92]">{branch.town}</span></td>
                  <td className="px-5 py-4 text-[#617067]">{branch.properties}</td>
                  <td className="px-5 py-4 text-[#617067]">{branch.occupied_units} / {branch.units}</td>
                  <td className="px-5 py-4"><span className="rounded-full bg-[#f0f5ec] px-2.5 py-1 text-xs font-semibold text-[#587846]">{branch.occupancy_rate}%</span></td>
                  <td className="px-5 py-4 text-[#617067]">{currency.format(Number(branch.billed))}</td>
                  <td className="px-5 py-4 font-semibold text-[#2b4333]">{currency.format(Number(branch.collected))}</td>
                  <td className="px-5 py-4 text-[#617067]">{currency.format(Number(branch.arrears))}</td>
                </tr>
              ))}
              {data.branches.length === 0 && <tr><td className="px-5 py-8 text-center text-sm text-[#89948c]" colSpan={7}>No branches are available in this scope.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
      <section className="overflow-hidden rounded-2xl border border-[#e7ebe5] bg-white shadow-[0_8px_24px_rgba(27,48,34,0.035)]">
        <div className="flex items-center gap-3 border-b border-[#edf0eb] px-5 py-5 sm:px-6">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-[#fbf4e8] text-[#a47b3d]"><AlertCircle size={17} /></span>
          <div>
            <h2 className="text-base font-bold tracking-tight text-[#243b2c]">Unmatched payments</h2>
            <p className="mt-0.5 text-xs text-[#89948c]">Payments that need a tenant or account match.</p>
          </div>
        </div>
        <div className="divide-y divide-[#f0f2ef]">
          {data.unmatched_payments.map((payment) => (
            <div key={payment.id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 transition-colors hover:bg-[#fbfcfa] sm:px-6">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-[#344b3b]">{payment.reference || payment.account_reference || `Payment ${payment.id}`}</p>
                <p className="mt-1 text-xs text-[#89948c]">{payment.tenant_name || 'Unknown tenant'} · {payment.branch_name || 'Unassigned'}</p>
              </div>
              <div className="flex items-center gap-4">
                <div className="text-right">
                  <p className="text-sm font-bold text-[#344b3b]">{currency.format(Number(payment.amount))}</p>
                  <p className="mt-1 text-[11px] text-[#89948c]">{new Date(payment.received_at).toLocaleDateString()}</p>
                </div>
                <span className="rounded-full bg-[#fbf4e8] px-2.5 py-1 text-[10px] font-bold text-[#97713d]">Needs match</span>
              </div>
            </div>
          ))}
          {data.unmatched_payments.length === 0 && (
            <div className="flex items-center gap-3 px-5 py-6 sm:px-6">
              <span className="grid h-8 w-8 place-items-center rounded-full bg-[#eff5eb] text-[#6b8d56]"><Activity size={15} /></span>
              <p className="text-sm text-[#78867b]">No unmatched payments in this agency.</p>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function PropertiesSection({ data }: { data: PropertyData }) {
  return (
    <div className="space-y-6">
      <PageTitle title="Agency properties" description="Persisted property inventory, unit occupancy, landlord, and active agreement data." />
      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>{['Property', 'Branch', 'Landlord', 'Units', 'Occupancy', 'Commission', 'Agreement expiry'].map((label) => <th key={label} className="px-4 py-3">{label}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.properties.map((property) => (
                <tr key={property.id}>
                  <td className="px-4 py-3">
                    <details>
                      <summary className="cursor-pointer font-semibold text-slate-900">{property.name}</summary>
                      <p className="mt-2 text-xs text-slate-600">{property.property_type} · {property.location}{property.address ? ` · ${property.address}` : ''}</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {property.units.map((unit) => <span key={unit.id} className="rounded-full bg-slate-100 px-2 py-1 text-xs">{unit.code}: {unit.status}</span>)}
                        {property.units.length === 0 && <span className="text-xs text-slate-500">No units recorded.</span>}
                      </div>
                    </details>
                  </td>
                  <td className="px-4 py-3">{property.branch.name}</td>
                  <td className="px-4 py-3">{property.landlord?.name || 'Not assigned'}</td>
                  <td className="px-4 py-3">{property.occupied_count} / {property.unit_count}</td>
                  <td className="px-4 py-3">{property.occupancy_rate}%</td>
                  <td className="px-4 py-3">{property.commission_model ? `${property.commission_model}${property.commission_rate ? ` (${property.commission_rate}%)` : ''}` : 'No active agreement'}</td>
                  <td className="px-4 py-3">{property.agreement_expiry || 'Open-ended / not set'}</td>
                </tr>
              ))}
              {data.properties.length === 0 && <tr><td className="px-4 py-6 text-center text-slate-500" colSpan={7}>No properties are available in this scope.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function FinancialsSection({
  data,
  agencyId,
  branchId,
  onUpdated,
  onError,
}: {
  data: FinancialData;
  agencyId: number;
  branchId: number | 'all';
  onUpdated: () => void;
  onError: (message: string) => void;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [busyWaiverId, setBusyWaiverId] = useState<number | null>(null);

  async function decide(id: number, action: 'approve' | 'reject') {
    const reviewNote = action === 'reject' ? window.prompt('Reason for rejecting this remittance:')?.trim() : '';
    if (action === 'reject' && !reviewNote) return;
    setBusyId(id);
    onError('');
    try {
      await requestJson(`/api/director/remittances/${id}/decision`, {
        method: 'POST',
        body: JSON.stringify({ agency_id: agencyId, action, review_note: reviewNote }),
      });
      onUpdated();
    } catch (error) {
      onError(error instanceof Error ? error.message : 'Unable to review remittance.');
    } finally {
      setBusyId(null);
    }
  }

  async function decideWaiver(id: number, decision: 'approved' | 'rejected') {
    const reason = decision === 'rejected'
      ? window.prompt('Reason for rejecting this invoice waiver:')?.trim() || ''
      : '';
    if (decision === 'rejected' && !reason) return;
    setBusyWaiverId(id);
    onError('');
    try {
      await requestJson(`/api/property-officer/billing/waivers/${id}`, {
        method: 'POST',
        body: JSON.stringify({
          agency_id: agencyId,
          ...(branchId !== 'all' ? { branch_id: branchId } : {}),
          decision,
          reason,
        }),
      });
      onUpdated();
    } catch (error) {
      onError(error instanceof Error ? error.message : 'Unable to decide the invoice waiver.');
    } finally {
      setBusyWaiverId(null);
    }
  }

  const summary = data.summary;
  const waiverRequests = Array.isArray(data.waiver_requests) ? data.waiver_requests : null;
  return (
    <div className="space-y-6">
      <PageTitle title="Agency financials" description="Agency-wide issued invoice value, allocated collections, and landlord remittance review for the selected period." />
      <p className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-950">
        Invoice value includes all issued invoice charges (such as rent, utilities, and carried balances), not rent alone. Collections are confirmed payment allocations received during this period, including payments allocated to earlier invoices.
      </p>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <Metric label="Issued invoice value" value={currency.format(Number(summary.billed_rent))} />
        <Metric label="Payments allocated" value={currency.format(Number(summary.collected_rent))} />
        <Metric label={summary.pending_approval_count === undefined ? 'Remittances awaiting approval' : `Remittances awaiting approval (${summary.pending_approval_count})`} value={currency.format(Number(summary.pending_approval))} />
        <Metric label={summary.approved_payout_count === undefined ? 'Approved remittances' : `Approved remittances (${summary.approved_payout_count})`} value={currency.format(Number(summary.approved_payouts))} />
        <Metric label={summary.paid_payout_count === undefined ? 'Paid remittances' : `Paid remittances (${summary.paid_payout_count})`} value={currency.format(Number(summary.paid_payouts))} />
        <Metric label="Tax withheld" value={currency.format(Number(summary.tax_withheld))} />
      </div>
      <section className="overflow-hidden rounded-2xl border border-amber-200 bg-amber-50">
        <div className="border-b border-amber-200 px-5 py-4">
          <h2 className="font-bold text-amber-950">Waiver requests</h2>
          <p className="mt-1 text-sm text-amber-900">Review pending requests, including those opened before an invoice draft exists. Approved requests are applied when the invoice is generated.</p>
        </div>
        <div className="divide-y divide-amber-100">
          {waiverRequests === null && (
            <p role="status" className="px-5 py-4 text-sm text-amber-900">
              Waiver requests are unavailable because the API response is missing waiver data. Restart or redeploy the Django API with the latest changes, then refresh this page.
            </p>
          )}
          {waiverRequests?.map((item) => (
            <article key={item.id} className="flex flex-wrap items-start justify-between gap-4 px-5 py-4">
              <div className="min-w-64 flex-1 text-sm">
                <p className="font-bold text-slate-900">{item.property_name} · {item.unit_code} · {item.tenant_name}</p>
                <p className="mt-1 text-xs text-slate-500">{item.branch_name} · {item.billing_month} · {item.invoice_number ? `Invoice ${item.invoice_number} (${item.invoice_status})` : 'Invoice not drafted'} · Requested by {item.requested_by} · {new Date(item.created_at).toLocaleString()}</p>
                <p className="mt-2">Request: <strong>{currency.format(Number(item.requested_amount))}</strong> · {item.reason}</p>
                {item.invoice_total !== null && item.net_amount_due !== null && (
                  <p className="mt-1 text-xs text-slate-600">Invoice total {currency.format(Number(item.invoice_total))} · already waived {currency.format(Number(item.waived_amount))} · current due {currency.format(Number(item.net_amount_due))}</p>
                )}
              </div>
              <div className="flex gap-2">
                <button type="button" disabled={busyWaiverId === item.id} onClick={() => void decideWaiver(item.id, 'approved')} className="rounded-lg bg-emerald-800 px-3 py-2 text-xs font-bold text-white disabled:opacity-50">Approve waiver</button>
                <button type="button" disabled={busyWaiverId === item.id} onClick={() => void decideWaiver(item.id, 'rejected')} className="rounded-lg border border-red-300 px-3 py-2 text-xs font-bold text-red-800 disabled:opacity-50">Reject</button>
              </div>
            </article>
          ))}
          {waiverRequests?.length === 0 && <p className="px-5 py-6 text-sm text-slate-600">There are no pending invoice waiver requests in this agency/branch scope.</p>}
        </div>
        {waiverRequests && data.pending_waiver_count !== undefined && data.pending_waiver_count > waiverRequests.length && (
          <p className="border-t border-amber-200 px-5 py-3 text-xs text-amber-900">Showing the oldest {waiverRequests.length} pending requests of {data.pending_waiver_count}; review these before continuing to newer requests.</p>
        )}
      </section>
      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div className="border-b border-slate-100 px-5 py-4">
          <h2 className="font-bold text-slate-900">Landlord remittances</h2>
          <p className="mt-1 text-sm text-slate-500">
            This section reviews prepared remittance statements and approves or rejects them; it does not execute landlord payouts.{' '}
            {summary.remittance_count === undefined
              ? 'Summary totals are for the selected period; the table shows up to 200 most recent records.'
              : `Summary totals cover all ${summary.remittance_count} remittances in the selected period. The table shows up to 200 most recent records.`}
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>{['Landlord', 'Branch', 'Period', 'Collected', 'Commission', 'Expenses', 'Tax', 'Net payout', 'Status', 'Action'].map((label) => <th key={label} className="px-3 py-3">{label}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.remittances.map((item) => (
                <tr key={item.id}>
                  <td className="px-3 py-3 font-semibold">{item.landlord_name}<span className="block text-xs font-normal text-slate-500">Prepared by {item.prepared_by}</span></td>
                  <td className="px-3 py-3">{item.branch_name}</td>
                  <td className="px-3 py-3">{item.period_start} – {item.period_end}<span className="block text-xs text-slate-500">Revision {item.revision}</span></td>
                  <td className="px-3 py-3">{currency.format(Number(item.collected_amount))}</td>
                  <td className="px-3 py-3">{currency.format(Number(item.commission_amount))}</td>
                  <td className="px-3 py-3">{currency.format(Number(item.expenses_amount))}</td>
                  <td className="px-3 py-3">{item.tax_amount === null ? '—' : currency.format(Number(item.tax_amount))}</td>
                  <td className="px-3 py-3 font-semibold">{currency.format(Number(item.net_payout))}</td>
                  <td className="px-3 py-3">
                    <span className="rounded-full bg-slate-100 px-2 py-1 text-xs">{item.status.replaceAll('_', ' ')}</span>
                    {item.rejection_reason && <span className="mt-1 block max-w-48 text-xs text-red-700">{item.rejection_reason}</span>}
                  </td>
                  <td className="px-3 py-3">
                    {item.status === 'awaiting_approval' ? (
                      <div className="flex gap-2">
                        <button disabled={busyId === item.id} onClick={() => void decide(item.id, 'approve')} className="rounded-lg bg-emerald-700 px-2 py-1 text-xs font-semibold text-white disabled:opacity-50">Approve</button>
                        <button disabled={busyId === item.id} onClick={() => void decide(item.id, 'reject')} className="rounded-lg border border-red-200 px-2 py-1 text-xs font-semibold text-red-700 disabled:opacity-50">Reject</button>
                      </div>
                    ) : <span className="text-xs text-slate-400">—</span>}
                  </td>
                </tr>
              ))}
              {data.remittances.length === 0 && <tr><td className="px-4 py-6 text-center text-slate-500" colSpan={10}>No remittances in this period.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function AuditSection({ data }: { data: AuditData }) {
  return (
    <div className="space-y-6">
      <PageTitle title="Agency audit trail" description="Append-only audit events attributed to this agency. Legacy events without an unambiguous agency are excluded." />
      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div className="divide-y divide-slate-100">
          {data.events.map((event) => (
            <details key={event.id} className="px-5 py-4">
              <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-3 text-sm">
                <span><span className="font-semibold text-slate-900">{event.action}</span><span className="ml-2 text-slate-500">{event.entity}</span><span className="ml-2 text-emerald-800">{event.user}</span></span>
                <span className="text-xs text-slate-500">{new Date(event.timestamp).toLocaleString()} · {event.ip_address || 'IP unavailable'}</span>
              </summary>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <div><p className="text-xs font-bold uppercase text-slate-500">Previous values</p><pre className="mt-1 overflow-auto rounded-lg bg-slate-50 p-3 text-xs">{JSON.stringify(event.old_values, null, 2) || '—'}</pre></div>
                <div><p className="text-xs font-bold uppercase text-slate-500">New values</p><pre className="mt-1 overflow-auto rounded-lg bg-slate-50 p-3 text-xs">{JSON.stringify(event.new_values, null, 2) || '—'}</pre></div>
              </div>
            </details>
          ))}
          {data.events.length === 0 && <p className="px-5 py-6 text-sm text-slate-500">No agency audit events found.</p>}
        </div>
      </section>
    </div>
  );
}

function SettingsSection({ data, agencyId, onSaved }: { data: SettingsData; agencyId: number; onSaved: () => void }) {
  const [values, setValues] = useState(data.settings);
  const [paybills, setPaybills] = useState<Record<number, string>>({});
  const [templatesText, setTemplatesText] = useState(JSON.stringify(data.settings.sms_templates, null, 2));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    setValues(data.settings);
    setTemplatesText(JSON.stringify(data.settings.sms_templates, null, 2));
    setPaybills(Object.fromEntries(data.branches.map((branch) => [branch.id, branch.paybill_numbers.join(', ')])));
  }, [data]);

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const smsTemplates: unknown = JSON.parse(templatesText);
      if (!smsTemplates || typeof smsTemplates !== 'object' || Array.isArray(smsTemplates)) {
        throw new Error('SMS templates must be a JSON object.');
      }
      await requestJson('/api/director/settings', {
        method: 'PATCH',
        body: JSON.stringify({
          agency_id: agencyId,
          ...values,
          sms_templates: smsTemplates,
          branch_paybills: Object.fromEntries(
            data.branches.map((branch) => [
              String(branch.id),
              (paybills[branch.id] || '').split(',').map((number) => number.trim()).filter(Boolean),
            ]),
          ),
        }),
      });
      setNotice('Agency settings saved.');
      onSaved();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to save agency settings.');
    } finally {
      setSaving(false);
    }
  }

  const fieldClass = 'mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm';
  return (
    <div className="space-y-6">
      <PageTitle title="Agency policy & settings" description="Persisted tax, VAT, arrears, branch Paybill, and SMS template settings." />
      <form onSubmit={save} className="max-w-4xl space-y-6 rounded-2xl border border-slate-200 bg-white p-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-sm font-semibold">MRI tax rate (%)
            <input className={fieldClass} type="number" min="0" max="100" step="0.01" value={values.mri_tax_rate} onChange={(event) => setValues({ ...values, mri_tax_rate: event.target.value })} />
          </label>
          <label className="text-sm font-semibold">Commission VAT rate (%)
            <input className={fieldClass} type="number" min="0" max="100" step="0.01" value={values.commission_vat_rate} onChange={(event) => setValues({ ...values, commission_vat_rate: event.target.value })} />
          </label>
          <label className="flex items-center gap-2 text-sm font-semibold">
            <input type="checkbox" checked={values.commission_vat_enabled} onChange={(event) => setValues({ ...values, commission_vat_enabled: event.target.checked })} />
            Commission VAT setting enabled
          </label>
          <label className="text-sm font-semibold">Grace period (days)
            <input className={fieldClass} type="number" min="0" max="365" step="1" value={values.grace_period_days} onChange={(event) => setValues({ ...values, grace_period_days: Number(event.target.value) })} />
          </label>
          <label className="text-sm font-semibold">Arrears penalty type
            <select className={fieldClass} value={values.penalty_type} onChange={(event) => setValues({ ...values, penalty_type: event.target.value })}>
              <option value="percentage">Percentage</option>
              <option value="flat">Flat fee</option>
            </select>
          </label>
          {values.penalty_type === 'percentage' ? (
            <label className="text-sm font-semibold">Penalty rate (%)
              <input className={fieldClass} type="number" min="0" max="100" step="0.0001" value={values.penalty_rate} onChange={(event) => setValues({ ...values, penalty_rate: event.target.value })} />
            </label>
          ) : (
            <label className="text-sm font-semibold">Penalty flat fee (KES)
              <input className={fieldClass} type="number" min="0" step="0.01" value={values.penalty_flat_fee} onChange={(event) => setValues({ ...values, penalty_flat_fee: event.target.value })} />
            </label>
          )}
        </div>
        <div className="space-y-3">
          <h2 className="font-bold text-slate-900">Branch payment numbers</h2>
          {data.branches.map((branch) => (
            <label key={branch.id} className="block text-sm font-semibold">{branch.name} ({branch.town})
              <input className={fieldClass} value={paybills[branch.id] ?? ''} onChange={(event) => setPaybills({ ...paybills, [branch.id]: event.target.value })} placeholder="Comma-separated Paybill numbers" />
            </label>
          ))}
          {data.branches.length === 0 && <p className="text-sm text-slate-500">No branches are assigned to this agency.</p>}
        </div>
        <label className="block text-sm font-semibold">SMS templates (JSON object)
          <textarea className={`${fieldClass} min-h-32 font-mono`} value={templatesText} onChange={(event) => setTemplatesText(event.target.value)} />
        </label>
        {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
        {notice && <p role="status" className="text-sm text-emerald-800">{notice}</p>}
        <button disabled={saving} className="rounded-xl bg-emerald-800 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">{saving ? 'Saving…' : 'Save agency settings'}</button>
      </form>
      <section className="max-w-4xl rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
        Tax and VAT settings are stored for this agency. Existing remittance calculations continue to use approved management-agreement tax terms; changing these settings does not retroactively alter existing agreements.
      </section>
    </div>
  );
}

export default function DirectorWorkspace() {
  const pathname = usePathname();
  const { agency, branchId, userName } = useDirectorPortal();
  const page = pathname.split('/').filter(Boolean).at(-1) || 'dashboard';
  const view = page === 'dashboard' ? 'overview' : page;
  const [data, setData] = useState<WorkspaceData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [auditAction, setAuditAction] = useState('');
  const [auditUser, setAuditUser] = useState('');
  const [auditFrom, setAuditFrom] = useState('');
  const [auditTo, setAuditTo] = useState('');
  const [auditFilters, setAuditFilters] = useState({ action: '', user: '', from: '', to: '' });

  const query = useMemo(() => {
    const params = new URLSearchParams();
    if (agency) params.set('agency_id', String(agency.id));
    if ((view === 'overview' || view === 'properties' || view === 'financials') && branchId !== 'all') {
      params.set('branch_id', String(branchId));
    }
    if (view === 'overview' || view === 'financials') params.set('month', month);
    if (view === 'audit') {
      for (const [key, value] of Object.entries(auditFilters)) if (value) params.set(key, value);
    }
    return params.toString();
  }, [agency, auditFilters, branchId, month, view]);

  const load = useCallback(() => setRefreshKey((key) => key + 1), []);

  useEffect(() => {
    if (view === 'staff' || !agency) {
      setData(null);
      setLoading(false);
      return;
    }
    const endpoints: Record<string, string> = {
      overview: 'overview',
      properties: 'properties',
      financials: 'financials',
      audit: 'audit-logs',
      settings: 'settings',
    };
    const endpoint = endpoints[view];
    if (!endpoint) return;
    let active = true;
    setLoading(true);
    setError('');
    void requestJson<WorkspaceData>(`/api/director/${endpoint}?${query}`)
      .then((result) => {
        if (active) setData(result);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : 'Unable to load Director portal data.');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [agency, query, refreshKey, view]);

  if (view === 'staff') {
    return <main className="mx-auto max-w-[1440px] space-y-6 px-4 py-8 sm:px-6 lg:px-8"><PageTitle title="Staff & access" description="Manage staff memberships and activation for this agency." /><StaffManagement selectedAgencyId={agency?.id ?? null} /></main>;
  }

  if (!agency) {
    return <main className="p-8"><p role="alert" className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">No active Director agency is selected.</p></main>;
  }

  return (
    <main className="mx-auto max-w-[1440px] space-y-6 px-4 py-7 sm:px-6 lg:px-8 lg:py-9">
      {(view === 'overview' || view === 'financials') && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-[#e7ebe5] bg-white px-4 py-3 shadow-[0_6px_18px_rgba(27,48,34,0.025)] sm:px-5">
          <div>
            <p className="text-xs font-bold text-[#425849]">Reporting period</p>
            <p className="mt-0.5 text-[11px] text-[#8a968e]">Choose a month to update the portfolio view.</p>
          </div>
          <label className="text-xs font-semibold text-[#607267]">
            <span className="sr-only">Reporting month</span>
            <input aria-label="Reporting month" className="rounded-xl border border-[#dfe5df] bg-[#fbfcfa] px-3 py-2 text-sm font-semibold text-[#34503d] outline-none transition focus:border-[#89a66b] focus:ring-4 focus:ring-[#eaf1e4]" type="month" value={month} onChange={(event) => setMonth(event.target.value)} />
          </label>
        </div>
      )}
      {view === 'audit' && (
        <form className="flex flex-wrap items-end gap-3 rounded-2xl border border-[#e7ebe5] bg-white p-5 shadow-[0_6px_18px_rgba(27,48,34,0.025)]" onSubmit={(event) => {
          event.preventDefault();
          setAuditFilters({ action: auditAction, user: auditUser, from: auditFrom, to: auditTo });
        }}>
          <label className="text-xs font-semibold text-slate-600">Action<input className="mt-1 block rounded-lg border border-slate-300 px-3 py-2 text-sm" value={auditAction} onChange={(event) => setAuditAction(event.target.value)} /></label>
          <label className="text-xs font-semibold text-slate-600">User<input className="mt-1 block rounded-lg border border-slate-300 px-3 py-2 text-sm" value={auditUser} onChange={(event) => setAuditUser(event.target.value)} /></label>
          <label className="text-xs font-semibold text-slate-600">From<input className="mt-1 block rounded-lg border border-slate-300 px-3 py-2 text-sm" type="date" value={auditFrom} onChange={(event) => setAuditFrom(event.target.value)} /></label>
          <label className="text-xs font-semibold text-slate-600">To<input className="mt-1 block rounded-lg border border-slate-300 px-3 py-2 text-sm" type="date" value={auditTo} onChange={(event) => setAuditTo(event.target.value)} /></label>
          <button className="rounded-xl bg-[#244a35] px-4 py-2 text-sm font-bold text-white transition hover:bg-[#193c2a]">Filter events</button>
        </form>
      )}
      {error && <p role="alert" className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}
      {actionError && <p role="alert" className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{actionError}</p>}
      {loading && <div role="status" className="rounded-2xl border border-[#e7ebe5] bg-white p-5 text-sm text-[#718078] shadow-[0_6px_18px_rgba(27,48,34,0.025)]">Loading agency data…</div>}
      {!loading && !error && view === 'overview' && data && 'totals' in data && <OverviewSection data={data} userName={userName} />}
      {!loading && !error && view === 'properties' && data && 'properties' in data && <PropertiesSection data={data} />}
      {!loading && !error && view === 'financials' && data && 'remittances' in data && <FinancialsSection data={data} agencyId={agency.id} branchId={branchId} onUpdated={load} onError={setActionError} />}
      {!loading && !error && view === 'audit' && data && 'events' in data && <AuditSection data={data} />}
      {!loading && !error && view === 'settings' && data && 'settings' in data && <SettingsSection data={data} agencyId={agency.id} onSaved={load} />}
    </main>
  );
}
