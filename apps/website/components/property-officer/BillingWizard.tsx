'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  ArrowUpRight,
  CalendarClock,
  CheckCircle2,
  Clock3,
  Droplets,
  FileText,
  Send,
  ShieldCheck,
} from 'lucide-react';
import { usePropertyOfficerPortal } from './PortalShell';
import { PortalLoading, PortalPagination, usePortalPagination } from './PortalUI';

type InvoiceLine = {
  id: number;
  charge_type: string;
  description: string;
  amount: string;
};

type WaiverRequest = {
  id: number;
  amount: string;
  reason: string;
  status: 'pending' | 'approved' | 'rejected';
  decision_reason: string;
};

type BillingInvoice = {
  id: number;
  number: string;
  period_start: string;
  status: 'draft' | 'issued';
  issued_at: string | null;
  due_date: string;
  total: string;
  waived_amount: string;
  net_amount_due: string;
  lines: InvoiceLine[];
  waiver_requests: WaiverRequest[];
};

type BillingRow = {
  tenancy_id: number;
  property_id: number;
  property_name: string;
  branch_id: number;
  unit_id: number;
  unit_code: string;
  tenant_name: string;
  water_reading_required: boolean;
  ready: boolean;
  readiness_note: string | null;
  waiver_requests: WaiverRequest[];
  invoice: BillingInvoice | null;
};

type BillingProperty = {
  id: number;
  branch_id: number;
  name: string;
};

type BillingResponse = {
  period_start: string;
  rows: BillingRow[];
  summary: {
    tenancies: number;
    drafts: number;
    issued: number;
    waiting_for_readings: number;
    ready_without_draft: number;
  };
  detail?: string;
};

type ApiError = { detail?: string; title?: string };

async function readJsonResponse<T extends ApiError>(response: Response): Promise<T> {
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    const body = await response.text();
    const hint = body.includes('<!DOCTYPE') || body.includes('<html')
      ? 'The billing service returned an HTML error page instead of JSON.'
      : 'The billing service returned an unexpected response.';
    throw new Error(`${hint} (HTTP ${response.status})`);
  }
  try {
    return await response.json() as T;
  } catch {
    throw new Error(`The billing service returned invalid JSON (HTTP ${response.status}).`);
  }
}

function nairobiDateParts(date: Date) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Nairobi',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date);
  return Object.fromEntries(parts.map((part) => [part.type, part.value]));
}

function currentMonth() {
  const parts = nairobiDateParts(new Date());
  return `${parts.year}-${parts.month}`;
}

function nairobiDate(date: Date) {
  const parts = nairobiDateParts(date);
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function firstDayAfterInvoicePeriod(periodStart: string) {
  const [year, month] = periodStart.split('-').map(Number);
  const firstDay = new Date(Date.UTC(year, month, 1, 12));
  return nairobiDate(firstDay);
}

function invoiceVisible(invoice: BillingInvoice, today: string) {
  if (invoice.status === 'draft') return true;
  if (!today || !invoice.issued_at) return false;
  const publishedOn = nairobiDate(new Date(invoice.issued_at));
  return today >= publishedOn && today <= firstDayAfterInvoicePeriod(invoice.period_start);
}

function monthLabel(value: string) {
  return new Date(`${value}-01T12:00:00`).toLocaleDateString('en-KE', {
    month: 'long',
    year: 'numeric',
  });
}

function money(value: string) {
  return new Intl.NumberFormat('en-KE', {
    style: 'currency',
    currency: 'KES',
    maximumFractionDigits: 2,
  }).format(Number(value));
}

export default function BillingWizard() {
  const { agency, branchIds, user } = usePropertyOfficerPortal();
  const [month, setMonth] = useState(currentMonth);
  const [monthSelectedManually, setMonthSelectedManually] = useState(false);
  const [properties, setProperties] = useState<BillingProperty[]>([]);
  const [selectedPropertyId, setSelectedPropertyId] = useState('');
  const [propertiesLoading, setPropertiesLoading] = useState(false);
  const [rows, setRows] = useState<BillingRow[]>([]);
  const [summary, setSummary] = useState<BillingResponse['summary'] | null>(null);
  const [loading, setLoading] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [waiverUnitId, setWaiverUnitId] = useState('');
  const [waiverAmount, setWaiverAmount] = useState('');
  const [waiverReason, setWaiverReason] = useState('');
  const [invoiceSearch, setInvoiceSearch] = useState('');
  const [invoiceStatusFilter, setInvoiceStatusFilter] = useState('all');
  const [waiverSearch, setWaiverSearch] = useState('');
  const [waiverStatusFilter, setWaiverStatusFilter] = useState('all');
  const [today, setToday] = useState('');
  const roleNames = [
    ...(user?.roles ?? []),
    ...(user?.agencies ?? []).flatMap((membership) => membership.roles),
  ];
  const canDecideWaivers = roleNames.some(
    (role) => role === 'Super Admin (Director)',
  );
  const visibleRows = useMemo(
    () => rows.filter((row) =>
      branchIds.has(row.branch_id)
      && row.property_id === Number(selectedPropertyId)
      && (!row.invoice || invoiceVisible(row.invoice, today)),
    ),
    [rows, branchIds, selectedPropertyId, today],
  );
  const filteredInvoiceRows = useMemo(() => {
    const query = invoiceSearch.trim().toLowerCase();
    return visibleRows.filter((row) => {
      const state = row.invoice?.status === 'issued'
        ? 'issued'
        : row.invoice
          ? 'draft'
          : row.ready
            ? 'ready'
            : 'on_hold';
      const matchesStatus = invoiceStatusFilter === 'all' || state === invoiceStatusFilter;
      const matchesSearch = !query || [
        row.property_name,
        row.unit_code,
        row.tenant_name,
        row.invoice?.number ?? '',
      ].some((value) => value.toLowerCase().includes(query));
      return matchesStatus && matchesSearch;
    });
  }, [invoiceSearch, invoiceStatusFilter, visibleRows]);
  const invoicePagination = usePortalPagination(filteredInvoiceRows);
  const displaySummary = useMemo(() => {
    if (!summary) return null;
    return {
      tenancies: visibleRows.length,
      drafts: visibleRows.filter((row) => row.invoice?.status === 'draft').length,
      issued: visibleRows.filter((row) => row.invoice?.status === 'issued').length,
      waiting_for_readings: visibleRows.filter((row) => !row.invoice && !row.ready).length,
      ready_without_draft: visibleRows.filter((row) => !row.invoice && row.ready).length,
    };
  }, [summary, visibleRows]);
  const waiverOptions = useMemo(
    () => visibleRows,
    [visibleRows],
  );
  const waiverHistory = useMemo(
    () => visibleRows.flatMap((row) => row.waiver_requests.map((waiver) => ({
      ...waiver,
      invoice: row.invoice,
      propertyName: row.property_name,
      unitCode: row.unit_code,
      tenantName: row.tenant_name,
    }))),
    [visibleRows],
  );
  const filteredWaiverHistory = useMemo(() => {
    const query = waiverSearch.trim().toLowerCase();
    return waiverHistory.filter((waiver) => {
      const matchesStatus = waiverStatusFilter === 'all' || waiver.status === waiverStatusFilter;
      const matchesSearch = !query || [
        waiver.propertyName,
        waiver.unitCode,
        waiver.tenantName,
        waiver.reason,
        waiver.invoice?.number ?? '',
      ].some((value) => value.toLowerCase().includes(query));
      return matchesStatus && matchesSearch;
    });
  }, [waiverHistory, waiverSearch, waiverStatusFilter]);
  const waiverPagination = usePortalPagination(filteredWaiverHistory);

  useEffect(() => {
    const updateDate = () => {
      const currentDate = nairobiDate(new Date());
      setToday(currentDate);
      if (!monthSelectedManually) setMonth(currentDate.slice(0, 7));
    };
    updateDate();
    const timer = window.setInterval(updateDate, 60_000);
    return () => window.clearInterval(timer);
  }, [monthSelectedManually]);

  useEffect(() => {
    if (!agency) {
      setProperties([]);
      setSelectedPropertyId('');
      return;
    }
    let active = true;
    setPropertiesLoading(true);
    setError('');
    void fetch('/api/property-officer/properties', { cache: 'no-store' })
      .then(async (response) => {
        const payload = await readJsonResponse<{ properties: BillingProperty[] } & ApiError>(response);
        if (!response.ok) throw new Error(payload.detail || payload.title || 'Unable to load properties.');
        return payload.properties.filter((property) => branchIds.has(property.branch_id));
      })
      .then((availableProperties) => {
        if (!active) return;
        setProperties(availableProperties);
        setSelectedPropertyId((current) =>
          availableProperties.some((property) => String(property.id) === current)
            ? current
            : String(availableProperties[0]?.id ?? ''),
        );
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : 'Unable to load properties.');
      })
      .finally(() => {
        if (active) setPropertiesLoading(false);
      });
    return () => {
      active = false;
    };
  }, [agency, branchIds]);

  const loadBilling = useCallback(async () => {
    if (!agency || !month || !selectedPropertyId) {
      setRows([]);
      setSummary(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams({ month, property_id: selectedPropertyId });
      const response = await fetch(`/api/property-officer/billing?${params}`, {
        cache: 'no-store',
      });
      const payload = await readJsonResponse<BillingResponse & ApiError>(response);
      if (!response.ok) throw new Error(payload.detail || payload.title || 'Unable to load billing.');
      setRows(payload.rows);
      setSummary(payload.summary);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load billing.');
    } finally {
      setLoading(false);
    }
  }, [agency, month, selectedPropertyId]);

  useEffect(() => {
    void loadBilling();
  }, [loadBilling]);

  async function submitJson(path: string, body: Record<string, unknown>, fallback: string) {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const payload = await readJsonResponse<ApiError>(response);
    if (!response.ok) throw new Error(payload.detail || payload.title || fallback);
    return payload;
  }

  async function createDrafts() {
    setWorking(true);
    setError('');
    setNotice('');
    try {
      const payload = await submitJson(
        '/api/property-officer/billing/draft',
        { month, property_id: Number(selectedPropertyId) },
        'Unable to prepare drafts.',
      );
      const held = 'held' in payload && Array.isArray(payload.held) ? payload.held.length : 0;
      setNotice(`Draft cycle completed.${held ? ` ${held} unit(s) are on hold until their readings or charges are ready.` : ''}`);
      await loadBilling();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to prepare drafts.');
    } finally {
      setWorking(false);
    }
  }

  async function requestWaiver() {
    const unitId = Number(waiverUnitId);
    if (!Number.isInteger(unitId) || unitId <= 0 || !selectedPropertyId) return;
    setWorking(true);
    setError('');
    setNotice('');
    try {
      await submitJson(
        '/api/property-officer/billing/waivers',
        {
          property_id: Number(selectedPropertyId),
          unit_id: unitId,
          month,
          amount: waiverAmount,
          reason: waiverReason,
        },
        'Unable to request a waiver.',
      );
      setNotice('Waiver request recorded for this unit and billing month, then sent to the Director for approval.');
      setWaiverAmount('');
      setWaiverReason('');
      await loadBilling();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to request a waiver.');
    } finally {
      setWorking(false);
    }
  }

  async function decideWaiver(waiverId: number, decision: 'approved' | 'rejected') {
    const reason = decision === 'rejected'
      ? window.prompt('Enter the reason for rejecting this waiver:') || ''
      : '';
    if (decision === 'rejected' && !reason.trim()) return;
    setWorking(true);
    setError('');
    setNotice('');
    try {
      await submitJson(
        `/api/property-officer/billing/waivers/${waiverId}`,
        { decision, reason },
        'Unable to decide the waiver.',
      );
      setNotice(`Waiver ${decision}.`);
      await loadBilling();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to decide the waiver.');
    } finally {
      setWorking(false);
    }
  }

  const selectedWaiverOption = waiverOptions.find(
    (row) => String(row.unit_id) === waiverUnitId,
  );
  const selectedInvoiceHasPendingWaiver = Boolean(
    selectedWaiverOption?.waiver_requests.length,
  );
  const selectedInvoiceBalance = selectedWaiverOption?.invoice
    ? Number(selectedWaiverOption.invoice.net_amount_due)
    : undefined;

  return (
    <main className="mx-auto max-w-[1440px] space-y-6 px-4 py-7 sm:px-6 lg:px-8 lg:py-9">
      <section className="relative isolate overflow-hidden rounded-[26px] bg-[#173b2e] px-6 py-7 text-white shadow-[0_16px_40px_rgba(23,59,46,0.16)] sm:px-8 sm:py-8">
        <div aria-hidden="true" className="pointer-events-none absolute -right-20 -top-32 -z-10 h-80 w-80 rounded-full border-[42px] border-white/[0.035]" />
        <div className="relative flex flex-wrap items-end justify-between gap-6">
          <div className="max-w-2xl">
            <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#c5e1b4]">Monthly invoicing</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-[38px]">Billing, on schedule.</h1>
            <p className="mt-3 max-w-xl text-sm leading-relaxed text-white/65">
              Automated drafts, water-reading readiness, and recorded waiver decisions in one place.
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs font-semibold text-white/70">
              Property
              <select
                aria-label="Billing property"
                value={selectedPropertyId}
                onChange={(event) => {
                  setSelectedPropertyId(event.target.value);
                  setWaiverUnitId('');
                }}
                disabled={propertiesLoading || properties.length === 0}
                className="mt-1.5 block min-w-48 rounded-xl border border-white/15 bg-white px-3 py-2.5 text-sm font-semibold text-[#294536] outline-none focus:ring-4 focus:ring-white/20 disabled:opacity-70"
              >
                <option value="">{propertiesLoading ? 'Loading properties…' : 'Select a property'}</option>
                {properties.map((property) => (
                  <option key={property.id} value={property.id}>{property.name}</option>
                ))}
              </select>
            </label>
            <label className="text-xs font-semibold text-white/70">
              Billing period
              <input
                type="month"
                value={month}
                onChange={(event) => {
                  setMonthSelectedManually(event.target.value !== currentMonth());
                  setMonth(event.target.value);
                  setWaiverUnitId('');
                }}
                className="mt-1.5 block rounded-xl border border-white/15 bg-white px-3 py-2.5 text-sm font-semibold text-[#294536] outline-none focus:ring-4 focus:ring-white/20"
              />
            </label>
          </div>
        </div>
      </section>

      <section aria-label="Monthly billing schedule" className="grid gap-3 lg:grid-cols-3">
        <article className="flex gap-3 rounded-2xl border border-[#e7ebe5] bg-white p-4 shadow-[0_6px_18px_rgba(27,48,34,0.025)]">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[#fbf4e8] text-[#a47b3d]"><Clock3 size={18} /></span>
          <div>
            <p className="text-xs font-bold text-[#344b3b]">30th · 9:00 a.m. EAT</p>
            <p className="mt-1 text-xs leading-relaxed text-[#7d8981]">Fixed-utility invoices are drafted automatically. Caretakers at metered-water properties are notified to record readings.</p>
          </div>
        </article>
        <article className="flex gap-3 rounded-2xl border border-[#e7ebe5] bg-white p-4 shadow-[0_6px_18px_rgba(27,48,34,0.025)]">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[#edf3f7] text-[#59788d]"><Droplets size={18} /></span>
          <div>
            <p className="text-xs font-bold text-[#344b3b]">After readings are published</p>
            <p className="mt-1 text-xs leading-relaxed text-[#7d8981]">Water charges are calculated from the readings, then eligible invoice drafts are created and the agent is notified.</p>
          </div>
        </article>
        <article className="flex gap-3 rounded-2xl border border-[#e7ebe5] bg-white p-4 shadow-[0_6px_18px_rgba(27,48,34,0.025)]">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[#eef5e9] text-[#62844e]"><Send size={17} /></span>
          <div>
            <p className="text-xs font-bold text-[#344b3b]">1st · 12:00 a.m. EAT</p>
            <p className="mt-1 text-xs leading-relaxed text-[#7d8981]">Eligible invoices are issued to the new month’s ledger and tenant dashboard, even if a waiver is still pending.</p>
          </div>
        </article>
      </section>
      <p className="flex items-start gap-2 rounded-xl bg-[#f0f4ed] px-4 py-3 text-xs leading-relaxed text-[#68796c]">
        <CalendarClock size={15} className="mt-0.5 shrink-0 text-[#738d60]" />
        In February and other shorter months, the scheduled 30th-of-month cycle runs on the final calendar day. You can safely retry the draft check if an expected draft is missing.
        <button
          type="button"
          onClick={() => void createDrafts()}
          disabled={working || loading || !month || !selectedPropertyId}
          className="ml-auto shrink-0 rounded-lg bg-[#244a35] px-3 py-2 text-xs font-bold text-white transition hover:bg-[#193c2a] disabled:opacity-50"
        >
          {working ? 'Checking…' : 'Retry draft check'}
        </button>
      </p>

      {error && <div role="alert" className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</div>}
      {notice && <div role="status" className="rounded-2xl border border-[#dce9d5] bg-[#f1f7ed] p-4 text-sm text-[#365a36]">{notice}</div>}

      <section aria-label="Billing summary" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ['Active tenancies', displaySummary?.tenancies ?? 0],
          ['Draft invoices', displaySummary?.drafts ?? 0],
          ['Issued in visibility window', displaySummary?.issued ?? 0],
          ['Waiting for water readings', displaySummary?.waiting_for_readings ?? 0],
        ].map(([label, value]) => (
          <div key={label} className="rounded-2xl border border-[#e7ebe5] bg-white p-4 shadow-[0_8px_24px_rgba(27,48,34,0.035)]">
            <p className="text-[10px] font-bold uppercase tracking-[0.13em] text-[#849087]">{label}</p>
            <p className="mt-2 text-2xl font-bold tracking-tight text-[#20372a]">{value}</p>
          </div>
        ))}
      </section>

      <section className="overflow-hidden rounded-2xl border border-[#e7ebe5] bg-white shadow-[0_8px_24px_rgba(27,48,34,0.035)]">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#edf0eb] px-5 py-5 sm:px-6">
          <div>
            <div className="flex items-center gap-2">
              <ShieldCheck size={17} className="text-[#718e5c]" />
              <h2 className="text-base font-bold tracking-tight text-[#243b2c]">Waiver requests</h2>
            </div>
            <p className="mt-1 text-xs text-[#89948c]">Requests are saved to the system and routed to the Director. Approved waivers update the invoice balance.</p>
          </div>
          <span className="rounded-full bg-[#f3f6f1] px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.1em] text-[#617565]">
            {filteredWaiverHistory.length} of {waiverHistory.length} recorded
          </span>
        </div>
        <div className="grid gap-0 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
          <form
            className="space-y-3 border-b border-[#edf0eb] p-5 lg:border-b-0 lg:border-r sm:p-6"
            onSubmit={(event) => {
              event.preventDefault();
              void requestWaiver();
            }}
          >
            <div>
              <p className="text-sm font-bold text-[#344b3b]">Open a waiver request</p>
              <p className="mt-1 text-xs leading-relaxed text-[#89948c]">Choose a unit for this property and billing month. The request is recorded now and linked to its invoice when the draft is created.</p>
            </div>
            <label className="block text-xs font-semibold text-[#607267]">
              Unit
              <select
                value={waiverUnitId}
                onChange={(event) => setWaiverUnitId(event.target.value)}
                className="mt-1.5 w-full rounded-xl border border-[#dfe5df] bg-white px-3 py-2.5 text-sm font-medium text-[#304739] outline-none focus:border-[#89a66b] focus:ring-4 focus:ring-[#eaf1e4]"
              >
                <option value="">Select a unit</option>
                {waiverOptions.map((row) => (
                  <option key={row.unit_id} value={row.unit_id}>
                    {row.unit_code} · {row.tenant_name}{row.invoice ? ` · ${row.invoice.number}` : ' · invoice not drafted'}
                  </option>
                ))}
              </select>
            </label>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
              <label className="block text-xs font-semibold text-[#607267]">
                Waiver amount (KES)
                <input
                  type="number"
                  min="0.01"
                  max={selectedInvoiceBalance}
                  step="0.01"
                  value={waiverAmount}
                  onChange={(event) => setWaiverAmount(event.target.value)}
                  disabled={!selectedWaiverOption || selectedInvoiceHasPendingWaiver}
                  className="mt-1.5 w-full rounded-xl border border-[#dfe5df] bg-white px-3 py-2.5 text-sm text-[#304739] outline-none focus:border-[#89a66b] focus:ring-4 focus:ring-[#eaf1e4] disabled:bg-[#f6f8f5]"
                />
              </label>
              <label className="block text-xs font-semibold text-[#607267]">
                Reason
                <input
                  value={waiverReason}
                  onChange={(event) => setWaiverReason(event.target.value)}
                  disabled={!selectedWaiverOption || selectedInvoiceHasPendingWaiver}
                  className="mt-1.5 w-full rounded-xl border border-[#dfe5df] bg-white px-3 py-2.5 text-sm text-[#304739] outline-none focus:border-[#89a66b] focus:ring-4 focus:ring-[#eaf1e4] disabled:bg-[#f6f8f5]"
                  placeholder="Explain why a waiver is requested"
                />
              </label>
            </div>
            {selectedWaiverOption && (
              <p className="text-[11px] text-[#89948c]">
                {selectedWaiverOption.invoice
                  ? `Current invoice balance available for waiver: ${money(selectedWaiverOption.invoice.net_amount_due)}.`
                  : 'The invoice is not drafted yet; the waiver amount will be validated against it when it is created.'}
                {selectedInvoiceHasPendingWaiver ? ' This unit already has a waiver request for this billing month.' : ''}
              </p>
            )}
            {waiverOptions.length === 0 && (
              <p className="rounded-xl bg-[#f8faf7] px-3 py-2.5 text-xs leading-relaxed text-[#7d8981]">
                No active tenanted units are available for this property and billing month.
              </p>
            )}
            <button
              type="submit"
              disabled={
                working
                || !selectedWaiverOption
                || selectedInvoiceHasPendingWaiver
                || !waiverAmount
                || !waiverReason.trim()
                || Number(waiverAmount) <= 0
                || (selectedInvoiceBalance !== undefined && Number(waiverAmount) > selectedInvoiceBalance)
              }
              className="inline-flex items-center gap-2 rounded-xl bg-[#244a35] px-4 py-2.5 text-sm font-bold text-white transition hover:bg-[#193c2a] disabled:cursor-not-allowed disabled:opacity-45"
            >
              {working ? 'Sending…' : 'Submit waiver request'} <ArrowUpRight size={15} />
            </button>
          </form>

          <div>
          <div className="grid gap-3 border-b border-[#edf0eb] p-4 sm:grid-cols-2">
            <label className="text-xs font-semibold text-[#607267]">Search waiver requests
              <input value={waiverSearch} onChange={(event) => setWaiverSearch(event.target.value)} placeholder="Tenant, unit, invoice, or reason" className="mt-1 w-full rounded-xl border border-[#dfe5df] px-3 py-2.5 text-sm" />
            </label>
            <label className="text-xs font-semibold text-[#607267]">Decision status
              <select value={waiverStatusFilter} onChange={(event) => setWaiverStatusFilter(event.target.value)} className="mt-1 w-full rounded-xl border border-[#dfe5df] bg-white px-3 py-2.5 text-sm">
                <option value="all">All decision statuses</option>
                <option value="pending">Pending approval</option>
                <option value="approved">Approved</option>
                <option value="rejected">Rejected</option>
              </select>
            </label>
          </div>
          <div className="divide-y divide-[#edf0eb]">
            {filteredWaiverHistory.length === 0 && waiverHistory.length > 0 ? (
              <p className="px-6 py-8 text-center text-sm text-[#89948c]">No waiver requests match these filters.</p>
            ) : null}
            {waiverPagination.pageRecords.map((waiver) => (
              <article key={waiver.id} className="px-5 py-4 sm:px-6">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-bold text-[#344b3b]">{waiver.propertyName} · {waiver.unitCode} · {waiver.tenantName}</p>
                      <p className="mt-1 text-xs text-[#89948c]">{waiver.invoice?.number || `${monthLabel(month)} invoice not drafted`} · {money(waiver.amount)} requested</p>
                  </div>
                  <span className={`rounded-full px-2.5 py-1 text-[10px] font-bold capitalize ${
                    waiver.status === 'approved'
                      ? 'bg-[#edf5e8] text-[#567744]'
                      : waiver.status === 'rejected'
                        ? 'bg-[#f9eeee] text-[#a85f58]'
                        : 'bg-[#fbf4e8] text-[#98723c]'
                  }`}>
                    {waiver.status === 'pending' ? 'Pending approval' : waiver.status}
                  </span>
                </div>
                <p className="mt-2 text-xs leading-relaxed text-[#69776e]">{waiver.reason}</p>
                {waiver.decision_reason && <p className="mt-1 text-xs text-[#89948c]">Decision note: {waiver.decision_reason}</p>}
                {waiver.status === 'approved' && waiver.invoice && (
                  <p className="mt-2 inline-flex items-center gap-1.5 text-[11px] font-semibold text-[#5d7e4c]">
                    <CheckCircle2 size={13} /> Waived {money(waiver.amount)} · invoice balance {money(waiver.invoice.net_amount_due)}
                  </p>
                )}
                {waiver.status === 'approved' && !waiver.invoice && (
                  <p className="mt-2 inline-flex items-center gap-1.5 text-[11px] font-semibold text-[#5d7e4c]">
                    <CheckCircle2 size={13} /> Approved; will be applied when the invoice draft is created.
                  </p>
                )}
                {canDecideWaivers && waiver.status === 'pending' && (
                  <div className="mt-3 flex gap-2">
                    <button type="button" disabled={working} onClick={() => void decideWaiver(waiver.id, 'approved')} className="rounded-lg bg-[#244a35] px-3 py-1.5 text-xs font-bold text-white disabled:opacity-50">Approve</button>
                    <button type="button" disabled={working} onClick={() => void decideWaiver(waiver.id, 'rejected')} className="rounded-lg border border-[#e4c8c4] px-3 py-1.5 text-xs font-bold text-[#965c56] disabled:opacity-50">Reject</button>
                  </div>
                )}
              </article>
            ))}
            {waiverHistory.length === 0 && (
              <div className="flex h-full min-h-40 flex-col items-center justify-center px-6 py-8 text-center">
                <span className="grid h-10 w-10 place-items-center rounded-full bg-[#f3f6f1] text-[#82927f]"><FileText size={17} /></span>
                <p className="mt-3 text-sm font-semibold text-[#526458]">No waiver requests for this period</p>
                <p className="mt-1 max-w-xs text-xs leading-relaxed text-[#89948c]">Submitted requests and their Director decisions will be recorded here alongside the matching invoice.</p>
              </div>
            )}
          </div>
          <PortalPagination {...waiverPagination} total={filteredWaiverHistory.length} onPageChange={waiverPagination.setPage} onPageSizeChange={waiverPagination.setPageSize} />
          </div>
        </div>
      </section>

      <section className="overflow-hidden rounded-2xl border border-[#e7ebe5] bg-white shadow-[0_8px_24px_rgba(27,48,34,0.035)]">
        <div className="border-b border-[#edf0eb] px-5 py-5 sm:px-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-bold tracking-tight text-[#243b2c]">{month ? monthLabel(month) : 'Monthly'} invoices</h2>
              <p className="mt-1 text-xs text-[#89948c]">Drafts can be reviewed here. Published invoices remain visible through the first day of the following month, then are hidden here; tenant and ledger records remain available.</p>
            </div>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-[#f3f6f1] px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.1em] text-[#617565]">
              <FileText size={13} /> {month ? monthLabel(month) : 'Selected period'}
            </span>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="text-xs font-semibold text-[#657469]">Search invoices
              <input value={invoiceSearch} onChange={(event) => setInvoiceSearch(event.target.value)} placeholder="Tenant, unit, property, or invoice number" className="mt-1 w-full rounded-xl border border-[#dfe5df] px-3 py-2.5 text-sm text-[#344b3b]" />
            </label>
            <label className="text-xs font-semibold text-[#657469]">Readiness / invoice status
              <select value={invoiceStatusFilter} onChange={(event) => setInvoiceStatusFilter(event.target.value)} className="mt-1 w-full rounded-xl border border-[#dfe5df] bg-white px-3 py-2.5 text-sm text-[#344b3b]">
                <option value="all">All billing records</option>
                <option value="draft">Draft invoice</option>
                <option value="issued">Published invoice</option>
                <option value="ready">Ready to draft</option>
                <option value="on_hold">On hold / awaiting readings</option>
              </select>
            </label>
          </div>
        </div>
        {loading ? (
          <div className="p-5"><PortalLoading label="Loading billing readiness…" rows={4} /></div>
        ) : filteredInvoiceRows.length === 0 ? (
          <div className="flex flex-col items-center px-6 py-12 text-center">
            <span className="grid h-11 w-11 place-items-center rounded-full bg-[#f3f6f1] text-[#82927f]"><AlertCircle size={18} /></span>
            <p className="mt-3 text-sm font-semibold text-[#526458]">No invoices visible for this period</p>
            <p className="mt-1 max-w-md text-xs leading-relaxed text-[#89948c]">Eligible drafts appear after the scheduled billing run or when metered-water readings are published. Published invoices outside their visibility window are hidden here.</p>
          </div>
        ) : (
          <div className="divide-y divide-[#edf0eb]">
            {invoicePagination.pageRecords.map((row) => {
              const invoice = row.invoice;
              return (
                <article key={row.tenancy_id} className="space-y-4 p-5 transition-colors hover:bg-[#fcfdfb] sm:p-6">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-bold text-[#344b3b]">{row.property_name} · {row.unit_code}</p>
                      <p className="mt-1 text-xs text-[#89948c]">{row.tenant_name}</p>
                    </div>
                    <span className={`rounded-full px-3 py-1 text-[10px] font-bold uppercase tracking-[0.08em] ${invoice?.status === 'issued' ? 'bg-[#edf3f7] text-[#56768b]' : invoice ? 'bg-[#fbf4e8] text-[#98723c]' : row.ready ? 'bg-[#edf5e8] text-[#567744]' : 'bg-[#fbf0e9] text-[#a16a44]'}`}>
                      {invoice?.status === 'issued' ? 'Published' : invoice ? 'Draft' : row.ready ? 'Ready to draft' : 'On hold'}
                    </span>
                  </div>

                  {!invoice ? (
                    <p className={`flex items-center gap-2 text-xs ${row.ready ? 'text-[#5d7e4c]' : 'text-[#a16a44]'}`}>
                      {row.ready ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
                      {row.readiness_note || (row.water_reading_required ? 'Waiting for the published water reading.' : 'No draft has been created yet.')}
                    </p>
                  ) : (
                    <div className="overflow-hidden rounded-2xl border border-[#e9ede7]">
                      <div className="flex flex-wrap justify-between gap-2 border-b border-[#edf0eb] bg-[#fafbf9] px-4 py-3 text-xs">
                        <span className="font-semibold text-[#526458]">{invoice.number} · due {invoice.due_date}</span>
                        <span className="font-bold text-[#344b3b]">{money(invoice.net_amount_due)} current balance</span>
                      </div>
                      <div className="divide-y divide-[#f0f2ef] px-4">
                        {invoice.lines.map((line) => (
                          <div key={line.id} className="flex justify-between gap-3 py-2.5 text-xs">
                            <span className="text-[#69776e]">{line.description}</span>
                            <span className="font-medium text-[#425849]">{money(line.amount)}</span>
                          </div>
                        ))}
                      </div>
                      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[#edf0eb] px-4 py-3 text-xs">
                        <span className="font-semibold text-[#536458]">Invoice total · {money(invoice.total)}</span>
                        {Number(invoice.waived_amount) > 0 ? (
                          <span className="font-semibold text-[#5d7e4c]">Waived {money(invoice.waived_amount)} · balance {money(invoice.net_amount_due)}</span>
                        ) : (
                          <span className="text-[#89948c]">No approved waiver</span>
                        )}
                      </div>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
        {!loading && <PortalPagination {...invoicePagination} total={filteredInvoiceRows.length} onPageChange={invoicePagination.setPage} onPageSizeChange={invoicePagination.setPageSize} />}
      </section>
    </main>
  );
}
