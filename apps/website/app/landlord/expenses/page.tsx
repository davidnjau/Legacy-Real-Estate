'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Receipt, RefreshCw } from 'lucide-react';

type PropertyExpenses = {
  id: number;
  name: string;
  approved_expenses: string;
  pending_expenses: string;
  pending_expense_count: number;
};

type ExpenseDashboard = {
  period_start: string;
  period_end: string;
  summary: {
    approved_expenses: string;
    pending_expenses: string;
    pending_expense_count: number;
  };
  properties: PropertyExpenses[];
};

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

export default function LandlordExpensesPage() {
  const [data, setData] = useState<ExpenseDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  async function loadExpenses(signal?: AbortSignal) {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/landlord/dashboard', { cache: 'no-store', signal });
      const payload = await response.json() as ExpenseDashboard & { error?: string; detail?: string };
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || 'Unable to load property expenses.');
      }
      setData(payload);
    } catch (cause) {
      if (cause instanceof Error && cause.name === 'AbortError') return;
      setError(cause instanceof Error ? cause.message : 'Unable to load property expenses.');
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void loadExpenses(controller.signal);
    return () => controller.abort();
  }, []);

  const periodLabel = data
    ? new Date(`${data.period_start}T00:00:00`).toLocaleDateString('en-KE', {
      month: 'long',
      year: 'numeric',
    })
    : '';

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-emerald-700">My portfolio</p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">Property expenses</h1>
          <p className="mt-1 text-sm text-slate-600">
            Approved and submitted expenses for properties linked to your landlord account{periodLabel ? ` · ${periodLabel}` : ''}.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void loadExpenses()}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </header>

      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</p>}

      {loading && !data ? (
        <p className="text-sm text-slate-500">Loading expenses for your properties…</p>
      ) : data && data.properties.length > 0 ? (
        <>
          <section className="grid gap-4 sm:grid-cols-2">
            <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <p className="text-sm font-medium text-slate-500">Approved expenses</p>
              <p className="mt-2 text-2xl font-bold text-slate-900">{currency.format(Number(data.summary.approved_expenses))}</p>
              <p className="mt-1 text-xs text-slate-500">Recorded for this reporting period</p>
            </article>
            <article className="rounded-2xl border border-amber-200 bg-amber-50/60 p-5 shadow-sm">
              <p className="text-sm font-medium text-amber-800">Awaiting review</p>
              <p className="mt-2 text-2xl font-bold text-amber-900">{currency.format(Number(data.summary.pending_expenses))}</p>
              <p className="mt-1 text-xs text-amber-800">{data.summary.pending_expense_count} submitted expense{data.summary.pending_expense_count === 1 ? '' : 's'}</p>
            </article>
          </section>

          <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-100 px-5 py-4">
              <h2 className="font-semibold text-slate-900">Expenses by property</h2>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-5 py-3">Property</th>
                    <th className="px-5 py-3 text-right">Approved</th>
                    <th className="px-5 py-3 text-right">Awaiting review</th>
                    <th className="px-5 py-3 text-right">Pending items</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.properties.map((property) => (
                    <tr key={property.id}>
                      <td className="px-5 py-4 font-semibold text-slate-900">{property.name}</td>
                      <td className="px-5 py-4 text-right text-slate-700">{currency.format(Number(property.approved_expenses))}</td>
                      <td className="px-5 py-4 text-right text-amber-800">{currency.format(Number(property.pending_expenses))}</td>
                      <td className="px-5 py-4 text-right text-slate-600">{property.pending_expense_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <div className="flex flex-wrap gap-3">
            <Link href="/landlord/statements" className="inline-flex items-center gap-2 rounded-lg text-sm font-semibold text-emerald-800 hover:text-emerald-950">
              Review statements <ArrowRight size={15} />
            </Link>
            <Link href="/landlord/remittances" className="inline-flex items-center gap-2 rounded-lg text-sm font-semibold text-emerald-800 hover:text-emerald-950">
              View remittances <ArrowRight size={15} />
            </Link>
          </div>
        </>
      ) : !loading && !error ? (
        <section className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center">
          <Receipt className="mx-auto text-slate-400" size={28} />
          <h2 className="mt-3 font-semibold text-slate-900">No properties linked yet</h2>
          <p className="mt-1 text-sm text-slate-500">Expenses will appear here after properties are linked to your account.</p>
        </section>
      ) : null}
    </div>
  );
}
