'use client';

import { useEffect, useMemo, useState } from 'react';
import { Fragment } from 'react';
import { usePropertyOfficerPortal } from './PortalShell';
import { PortalLoading, PortalPagination, usePortalPagination } from './PortalUI';

type PropertyRecord = {
  id: number;
  branch_id: number;
  name: string;
};

type BillLine = {
  invoice_number: string;
  charge_type: string;
  description: string;
  amount: string;
};

type PaymentRecord = {
  id: number;
  received_at: string;
  reference: string;
  source: string;
  amount: string;
};

type LedgerRow = {
  tenancy_id: number;
  property_id: number;
  property_name: string;
  branch_id: number;
  branch_name: string;
  unit_id: number;
  unit_code: string;
  tenant_name: string;
  invoice_numbers: string[];
  new_charges: string;
  prior_balance: string;
  expected: string;
  paid: string;
  waived: string;
  waiver_notes: { invoice_number: string; amount: string; reason: string }[];
  remaining: string;
  status: 'unpaid' | 'partial' | 'cleared';
  bill_lines: BillLine[];
  payments: PaymentRecord[];
};

type LedgerSummary = {
  records: number;
  new_charges: string;
  prior_balance: string;
  expected: string;
  paid: string;
  waived: string;
  remaining: string;
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
  partial: 'Partially paid',
  cleared: 'Cleared',
};

function monthLabel(month: string) {
  return new Date(`${month}-01T00:00:00`).toLocaleString('en', { month: 'long', year: 'numeric' });
}

function currentMonth() {
  const today = new Date();
  return `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`;
}

export default function MonthlyPropertyLedger() {
  const { agency, branchIds } = usePropertyOfficerPortal();
  const assignedBranches = (agency?.branches ?? []).filter((branch) => branchIds.has(branch.id));
  const [properties, setProperties] = useState<PropertyRecord[]>([]);
  const [months, setMonths] = useState<string[]>([]);
  const [selectedMonth, setSelectedMonth] = useState(currentMonth);
  const [propertyId, setPropertyId] = useState('');
  const [status, setStatus] = useState<'all' | LedgerRow['status']>('all');
  const [branchFilter, setBranchFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [rows, setRows] = useState<LedgerRow[]>([]);
  const [summary, setSummary] = useState<LedgerSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [expandedTenancyId, setExpandedTenancyId] = useState<number | null>(null);
  const branchKey = useMemo(() => [...branchIds].sort((a, b) => a - b).join(','), [branchIds]);
  const requestBranchKey = branchFilter === 'all' ? branchKey : branchFilter;
  const requestBranchIds = requestBranchKey.split(',').filter(Boolean);

  useEffect(() => {
    if (!agency) return;
    let active = true;
    void fetch('/api/property-officer/properties', { cache: 'no-store' })
      .then(async (response) => {
        const payload = await response.json() as ApiPayload<{ properties: PropertyRecord[] }>;
        if (!response.ok) throw new Error(payload.detail || payload.error || 'Unable to load properties.');
        if (active) {
          const visibleProperties = payload.properties.filter((property) =>
            branchIds.has(property.branch_id)
            && (branchFilter === 'all' || String(property.branch_id) === branchFilter),
          );
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
  }, [agency, branchFilter, branchIds, branchKey]);

  useEffect(() => {
    if (!agency) return;
    if (!branchKey) {
      setMonths([]);
      setSelectedMonth(currentMonth());
      setRows([]);
      setSummary(null);
      setLoading(false);
      return;
    }
    let active = true;
    const query = new URLSearchParams();
    requestBranchIds.forEach((branchId) => query.append('branch_id', branchId));
    if (propertyId) query.set('property_id', propertyId);
    void fetch(`/api/property-officer/monthly-ledger/months?${query.toString()}`, { cache: 'no-store' })
      .then(async (response) => {
        const payload = await response.json() as ApiPayload<{ months: string[] }>;
        if (!response.ok) throw new Error(payload.detail || payload.error || 'Unable to load ledger months.');
        if (active) {
          setMonths(payload.months);
          setSelectedMonth((current) => payload.months.includes(current) ? current : payload.months[0] || '');
          setError('');
        }
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : 'Unable to load ledger months.');
      });
    return () => {
      active = false;
    };
  }, [agency, branchKey, propertyId, requestBranchKey]);

  useEffect(() => {
    if (!agency || !branchKey || !selectedMonth) return;
    let active = true;
    const query = new URLSearchParams({ month: selectedMonth, status });
    requestBranchIds.forEach((branchId) => query.append('branch_id', branchId));
    if (propertyId) query.set('property_id', propertyId);
    setLoading(true);
    void fetch(`/api/property-officer/monthly-ledger?${query.toString()}`, { cache: 'no-store' })
      .then(async (response) => {
        const payload = await response.json() as ApiPayload<{
          ledger: LedgerRow[];
          summary: LedgerSummary;
        }>;
        if (!response.ok) throw new Error(payload.detail || payload.error || 'Unable to load the monthly ledger.');
        if (active) {
          setRows(payload.ledger);
          setSummary(payload.summary);
          setError('');
        }
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : 'Unable to load the monthly ledger.');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [agency, branchKey, propertyId, requestBranchKey, selectedMonth, status]);

  const filteredRows = useMemo(() => {
    const query = search.trim().toLowerCase();
    return rows.filter((row) => !query || [
      row.tenant_name,
      row.property_name,
      row.unit_code,
      row.branch_name,
      ...row.invoice_numbers,
    ].some((value) => value.toLowerCase().includes(query)));
  }, [rows, search]);
  const ledgerPagination = usePortalPagination(filteredRows);
  const sumField = (field: 'new_charges' | 'prior_balance' | 'paid' | 'remaining') => (
    filteredRows.reduce((total, row) => total + Number(row[field]), 0).toFixed(2)
  );
  const cards = summary ? [
    { label: 'New monthly charges', amount: sumField('new_charges') },
    { label: 'Prior balance', amount: sumField('prior_balance') },
    { label: 'Payments received', amount: sumField('paid') },
    { label: 'Remaining balance', amount: sumField('remaining') },
  ] : [];
  const monthOptions = months.includes(selectedMonth) ? months : [selectedMonth, ...months];

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-wrap justify-end gap-3">
        <div className="flex flex-wrap gap-3">
          <label>
            <span className="mb-1 block text-xs font-semibold text-slate-500">Branch</span>
            <select
              aria-label="Filter by branch"
              value={branchFilter}
              onChange={(event) => {
                setBranchFilter(event.target.value);
                setPropertyId('');
              }}
              className="min-w-44 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
            >
              <option value="all">All assigned branches</option>
              {assignedBranches.map((branch) => <option key={branch.id} value={branch.id}>{branch.name}</option>)}
            </select>
          </label>
          <label>
            <span className="mb-1 block text-xs font-semibold text-slate-500">Property</span>
            <select
              aria-label="Filter by property"
              value={propertyId}
              onChange={(event) => setPropertyId(event.target.value)}
              className="min-w-52 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
            >
              <option value="">All selected-branch properties</option>
              {properties.map((property) => (
                <option key={property.id} value={property.id}>{property.name}</option>
              ))}
            </select>
          </label>
          <label>
            <span className="mb-1 block text-xs font-semibold text-slate-500">Search records</span>
            <input
              aria-label="Search monthly ledger records"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Tenant, unit, invoice"
              className="min-w-48 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
            />
          </label>
          <label>
            <span className="mb-1 block text-xs font-semibold text-slate-500">Month</span>
            <select
              aria-label="Select ledger month"
              value={selectedMonth}
              onChange={(event) => setSelectedMonth(event.target.value)}
              className="min-w-44 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
            >
              {monthOptions.map((month) => <option key={month} value={month}>{monthLabel(month)}</option>)}
            </select>
          </label>
          <label>
            <span className="mb-1 block text-xs font-semibold text-slate-500">Status</span>
            <select
              aria-label="Filter by payment status"
              value={status}
              onChange={(event) => setStatus(event.target.value as 'all' | LedgerRow['status'])}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
            >
              <option value="all">All statuses</option>
              <option value="unpaid">Unpaid</option>
              <option value="partial">Partially paid</option>
              <option value="cleared">Cleared</option>
            </select>
          </label>
        </div>
      </header>

      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}

      {summary && (
        <section aria-label="Monthly ledger summary" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {cards.map((card) => (
            <article key={card.label} className="rounded-2xl border border-slate-200 bg-white p-5">
              <p className="text-xs font-bold uppercase tracking-wider text-slate-500">{card.label}</p>
              <p className="mt-2 text-2xl font-bold text-slate-950">{currency.format(Number(card.amount))}</p>
            </article>
          ))}
        </section>
      )}

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1150px] text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                {['Property / unit', 'Tenant', 'Monthly bill', 'Prior balance', 'Expected', 'Paid this month', 'Waived', 'Remaining', 'Status'].map((label) => (
                  <th key={label} className="px-4 py-3 font-semibold">{label}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr><td colSpan={9} className="p-4"><PortalLoading label="Loading monthly ledger…" rows={4} /></td></tr>
              ) : filteredRows.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-4 py-10 text-center text-sm text-slate-500">
                    No monthly bills match the selected property, month, and status filters.
                  </td>
                </tr>
              ) : ledgerPagination.pageRecords.map((row) => (
                  <Fragment key={row.tenancy_id}>
                    <tr>
                      <td className="px-4 py-3">
                        <p className="font-semibold text-slate-900">{row.property_name}</p>
                        <p className="mt-0.5 font-mono text-xs text-slate-500">{row.unit_code} · {row.branch_name}</p>
                      </td>
                      <td className="px-4 py-3 font-medium text-slate-800">{row.tenant_name}</td>
                      <td className="px-4 py-3">
                        <button
                          type="button"
                          aria-expanded={expandedTenancyId === row.tenancy_id}
                          onClick={() => setExpandedTenancyId((current) => current === row.tenancy_id ? null : row.tenancy_id)}
                          className="font-semibold text-emerald-800 underline decoration-dotted underline-offset-4"
                        >
                          {currency.format(Number(row.new_charges))}
                        </button>
                        <p className="mt-0.5 text-xs text-slate-500">{row.bill_lines.length} line item{row.bill_lines.length === 1 ? '' : 's'}</p>
                      </td>
                      <td className="px-4 py-3 text-amber-800">{currency.format(Number(row.prior_balance))}</td>
                      <td className="px-4 py-3 font-semibold">{currency.format(Number(row.expected))}</td>
                      <td className="px-4 py-3 text-emerald-700">{currency.format(Number(row.paid))}</td>
                      <td className="px-4 py-3 text-slate-600">
                        {currency.format(Number(row.waived))}
                        {row.waiver_notes.map((waiver, index) => (
                          <p key={`${waiver.invoice_number}-${index}`} className="mt-1 max-w-48 text-xs text-emerald-800" title={waiver.reason}>
                            Waived: {waiver.reason}
                          </p>
                        ))}
                      </td>
                      <td className="px-4 py-3 font-bold">{currency.format(Number(row.remaining))}</td>
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
                    {expandedTenancyId === row.tenancy_id && (
                      <tr key={`${row.tenancy_id}-details`}>
                        <td colSpan={9} className="bg-slate-50 px-5 py-4">
                          <div className="grid gap-6 lg:grid-cols-2">
                            <div>
                              <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500">Itemized monthly bills</h2>
                              <ul className="mt-2 divide-y divide-slate-200">
                                {row.bill_lines.map((line, index) => (
                                  <li key={`${line.invoice_number}-${index}`} className="flex justify-between gap-4 py-2 text-sm">
                                    <span className="text-slate-700">{line.description}<span className="ml-2 text-xs text-slate-400">{line.invoice_number}</span></span>
                                    <strong>{currency.format(Number(line.amount))}</strong>
                                  </li>
                                ))}
                              </ul>
                            </div>
                            <div>
                              <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500">Payments allocated this month</h2>
                              {row.payments.length ? (
                                <ul className="mt-2 divide-y divide-slate-200">
                                  {row.payments.map((payment) => (
                                    <li key={payment.id} className="flex justify-between gap-4 py-2 text-sm">
                                      <span className="text-slate-700">
                                        {new Date(payment.received_at).toLocaleDateString()} · {payment.source.replace(/_/g, ' ')} · {payment.reference}
                                      </span>
                                      <strong className="text-emerald-700">{currency.format(Number(payment.amount))}</strong>
                                    </li>
                                  ))}
                                </ul>
                              ) : <p className="mt-2 text-sm text-slate-500">No confirmed payments allocated this month.</p>}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && <PortalPagination {...ledgerPagination} total={filteredRows.length} onPageChange={ledgerPagination.setPage} onPageSizeChange={ledgerPagination.setPageSize} />}
      </section>
      {summary && selectedMonth && (
        <p className="text-xs text-slate-500">
          {monthLabel(selectedMonth)} · {summary.records} tenancy{summary.records === 1 ? '' : ' records'} · Remaining balance is measured as of {new Date(`${selectedMonth}-01T00:00:00`).getMonth() === new Date().getMonth() && new Date(`${selectedMonth}-01T00:00:00`).getFullYear() === new Date().getFullYear() ? 'today' : 'the end of the selected month'}.
        </p>
      )}
    </main>
  );
}
