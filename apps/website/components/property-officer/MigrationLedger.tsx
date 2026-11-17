'use client';

import { useEffect, useMemo, useState } from 'react';
import { usePropertyOfficerPortal } from './PortalShell';

type PropertyRecord = {
  id: number;
  branch_id: number;
  name: string;
};

type LedgerRow = {
  invoice_id: number;
  invoice_number: string;
  property_id: number;
  property_name: string;
  branch_id: number;
  branch_name: string;
  unit_id: number;
  unit_code: string;
  tenancy_id: number;
  tenant_name: string;
  arrears_as_of: string;
  opening_arrears: string;
  paid_to_arrears: string;
  waived_amount: string;
  outstanding: string;
  status: 'unpaid' | 'partial' | 'cleared';
};

type LedgerSummary = {
  records: number;
  opening_arrears: string;
  paid_to_arrears: string;
  waived_amount: string;
  outstanding: string;
  cleared: number;
  partial: number;
  unpaid: number;
};

type ApiPayload<T> = T & { detail?: string; error?: string };

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 2,
});

const statusLabels: Record<LedgerRow['status'], string> = {
  unpaid: 'Unpaid',
  partial: 'Partially settled',
  cleared: 'Cleared',
};

export default function MigrationLedger() {
  const { agency, branchIds } = usePropertyOfficerPortal();
  const [properties, setProperties] = useState<PropertyRecord[]>([]);
  const [rows, setRows] = useState<LedgerRow[]>([]);
  const [summary, setSummary] = useState<LedgerSummary | null>(null);
  const [propertyId, setPropertyId] = useState('');
  const [status, setStatus] = useState<'all' | LedgerRow['status']>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const branchKey = useMemo(() => [...branchIds].sort((a, b) => a - b).join(','), [branchIds]);

  useEffect(() => {
    if (!agency) return;
    let active = true;
    void fetch('/api/property-officer/properties', { cache: 'no-store' })
      .then(async (response) => {
        const payload = await response.json() as ApiPayload<{ properties: PropertyRecord[] }>;
        if (!response.ok) throw new Error(payload.detail || payload.error || 'Unable to load properties.');
        if (active) {
          const visibleProperties = payload.properties.filter((property) => branchIds.has(property.branch_id));
          setProperties(visibleProperties);
          setPropertyId((current) =>
            current && visibleProperties.some((property) => property.id === Number(current)) ? current : '',
          );
        }
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : 'Unable to load properties.');
      });
    return () => {
      active = false;
    };
  }, [agency, branchIds, branchKey]);

  useEffect(() => {
    if (!agency) return;
    if (!branchKey) {
      setRows([]);
      setSummary(null);
      setError('');
      setLoading(false);
      return;
    }
    let active = true;
    const query = new URLSearchParams();
    branchKey.split(',').filter(Boolean).forEach((branchId) => query.append('branch_id', branchId));
    if (propertyId) query.set('property_id', propertyId);
    if (status !== 'all') query.set('status', status);
    const refresh = (showLoading: boolean) => {
      if (showLoading) setLoading(true);
      void fetch(`/api/property-officer/migration-ledger?${query.toString()}`, { cache: 'no-store' })
        .then(async (response) => {
          const payload = await response.json() as ApiPayload<{
            ledger: LedgerRow[];
            summary: LedgerSummary;
          }>;
          if (!response.ok) throw new Error(payload.detail || payload.error || 'Unable to load the migration ledger.');
          if (active) {
            setRows(payload.ledger);
            setSummary(payload.summary);
            setError('');
          }
        })
        .catch((cause: unknown) => {
          if (active) setError(cause instanceof Error ? cause.message : 'Unable to load the migration ledger.');
        })
        .finally(() => {
          if (active && showLoading) setLoading(false);
        });
    };
    refresh(true);
    const interval = setInterval(() => refresh(false), 15000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [agency, branchKey, propertyId, status]);

  const cards = summary ? [
    { label: 'Opening arrears', amount: summary.opening_arrears, detail: `${summary.records} migration balances` },
    { label: 'Paid toward arrears', amount: summary.paid_to_arrears, detail: 'Confirmed allocations' },
    { label: 'Waived', amount: summary.waived_amount, detail: 'Approved arrears waivers' },
    { label: 'Outstanding', amount: summary.outstanding, detail: `${summary.unpaid} unpaid · ${summary.partial} partial · ${summary.cleared} cleared` },
  ] : [];

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-800">{agency?.name || 'Agency'}</p>
          <h1 className="mt-1 text-2xl font-bold text-slate-950">Migration ledger</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-500">
            Track opening arrears imported during the transition. Confirmed payments and approved waivers update each balance automatically.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <label className="text-sm font-medium text-slate-700">
            <span className="sr-only">Filter by property</span>
            <select
              aria-label="Filter by property"
              value={propertyId}
              onChange={(event) => setPropertyId(event.target.value)}
              className="min-w-52 rounded-lg border border-slate-300 bg-white px-3 py-2"
            >
              <option value="">All selected-branch properties</option>
              {properties.map((property) => (
                <option key={property.id} value={property.id}>{property.name}</option>
              ))}
            </select>
          </label>
          <label className="text-sm font-medium text-slate-700">
            <span className="sr-only">Filter by payment status</span>
            <select
              aria-label="Filter by payment status"
              value={status}
              onChange={(event) => setStatus(event.target.value as 'all' | LedgerRow['status'])}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2"
            >
              <option value="all">All statuses</option>
              <option value="unpaid">Unpaid</option>
              <option value="partial">Partially settled</option>
              <option value="cleared">Cleared</option>
            </select>
          </label>
        </div>
      </header>

      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}

      {summary && (
        <section aria-label="Migration ledger summary" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {cards.map((card) => (
            <article key={card.label} className="rounded-2xl border border-slate-200 bg-white p-5">
              <p className="text-xs font-bold uppercase tracking-wider text-slate-500">{card.label}</p>
              <p className="mt-2 text-2xl font-bold text-slate-950">{currency.format(Number(card.amount))}</p>
              <p className="mt-1 text-xs text-slate-500">{card.detail}</p>
            </article>
          ))}
        </section>
      )}

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        {loading ? (
          <p role="status" className="p-8 text-center text-sm text-slate-500">Loading migration balances…</p>
        ) : rows.length === 0 ? (
          <p className="p-10 text-center text-sm text-slate-500">
            No imported opening arrears match the selected property and status filters.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1050px] text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase text-slate-500">
                <tr>
                  {['Property / unit', 'Tenant', 'Arrears as of', 'Opening arrears', 'Paid', 'Waived', 'Outstanding', 'Status'].map((label) => (
                    <th key={label} className="px-4 py-3 font-semibold">{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rows.map((row) => (
                  <tr key={row.invoice_id}>
                    <td className="px-4 py-3">
                      <p className="font-semibold text-slate-900">{row.property_name}</p>
                      <p className="mt-0.5 font-mono text-xs text-slate-500">{row.unit_code} · {row.branch_name}</p>
                    </td>
                    <td className="px-4 py-3">
                      <p className="font-medium text-slate-800">{row.tenant_name}</p>
                      <p className="mt-0.5 text-xs text-slate-500">{row.invoice_number}</p>
                    </td>
                    <td className="px-4 py-3 text-slate-600">{row.arrears_as_of}</td>
                    <td className="px-4 py-3 font-medium">{currency.format(Number(row.opening_arrears))}</td>
                    <td className="px-4 py-3 text-emerald-700">{currency.format(Number(row.paid_to_arrears))}</td>
                    <td className="px-4 py-3 text-slate-600">{currency.format(Number(row.waived_amount))}</td>
                    <td className="px-4 py-3 font-semibold text-slate-900">{currency.format(Number(row.outstanding))}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${
                        row.status === 'cleared'
                          ? 'bg-emerald-100 text-emerald-800'
                          : row.status === 'partial'
                            ? 'bg-amber-100 text-amber-800'
                            : 'bg-red-100 text-red-800'
                      }`}>
                        {statusLabels[row.status]}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <p className="text-xs text-slate-500">Balances reflect confirmed allocations and approved waivers as of today. Payments posted to the opening-arrears invoice appear here automatically.</p>
    </main>
  );
}
