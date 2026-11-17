'use client';

import { useEffect, useState } from 'react';
import {
  AlertCircle,
  ArrowRight,
  Building2,
  CheckCircle2,
  Coins,
  FileText,
  Home,
  RefreshCw,
  Send,
  Wallet,
  type LucideIcon,
} from 'lucide-react';
import Link from 'next/link';

/* ─── Types ─────────────────────────────────────────────────────────────── */

type PropertySummary = {
  id: number;
  name: string;
  location: string;
  total_units: number;
  occupied_units: number;
  occupancy_rate: number;
  expected_rent: string;
  collected_rent: string;
  arrears: string;
  approved_expenses: string;
  pending_expenses: string;
  pending_expense_count: number;
  open_maintenance_count: number;
};

type DashboardSummary = {
  total_units: number;
  occupied_units: number;
  occupancy_rate: number;
  expected_rent: string;
  collected_rent: string;
  arrears: string;
  projected_net_payout: string | null;
  projected_net_payout_status: 'configured' | 'not_configured' | 'not_allocated' | 'not_applicable';
  approved_expenses: string;
  pending_expenses: string;
  pending_expense_count: number;
  open_maintenance_count: number;
};

type DashboardData = {
  period_start: string;
  period_end: string;
  summary: DashboardSummary;
  properties: PropertySummary[];
  pending_high_cost_repairs: {
    id: number;
    ticket_id: number;
    ticket_title: string;
    property_name: string;
    unit_code: string | null;
    vendor_name: string;
    amount: string;
    approval_threshold: string;
    submitted_at: string;
  }[];
  collections: {
    id: number;
    received_at: string;
    amount: string;
    source: 'mpesa' | 'bank';
    reference: string;
    property_name: string;
    unit_code: string;
    tenant_name: string;
  }[];
};

/* ─── Currency Formatter ────────────────────────────────────────────────── */

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

function formatCurrency(value: string | number) {
  return currency.format(Number(value));
}

/* ─── Components ────────────────────────────────────────────────────────── */

function KpiCard({
  icon: Icon,
  label,
  value,
  detail,
  tone,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  detail: string;
  tone: 'emerald' | 'blue' | 'amber' | 'red' | 'slate';
}) {
  const iconBg: Record<string, string> = {
    emerald: 'bg-emerald-50 text-emerald-600',
    blue: 'bg-blue-50 text-blue-600',
    amber: 'bg-amber-50 text-amber-600',
    red: 'bg-red-50 text-red-600',
    slate: 'bg-slate-100 text-slate-600',
  };

  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:shadow-md">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-500">{label}</p>
          <p className="mt-2 text-2xl font-bold tracking-tight text-slate-900">{value}</p>
        </div>
        <span className={`shrink-0 rounded-xl p-2.5 ${iconBg[tone]}`}>
          <Icon size={20} aria-hidden="true" />
        </span>
      </div>
      <p className="mt-3 text-xs text-slate-400">{detail}</p>
    </article>
  );
}

/* ─── Page ──────────────────────────────────────────────────────────────── */

export default function LandlordDashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [landlordName, setLandlordName] = useState('Landlord');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const loadDashboard = async (signal?: AbortSignal) => {
    setLoading(true);
    setError('');
    try {
      const [dashRes, userRes] = await Promise.all([
        fetch('/api/landlord/dashboard', {
          cache: 'no-store',
          signal,
        }),
        fetch('/api/auth/me', {
          cache: 'no-store',
          signal,
        }).catch(() => null),
      ]);

      if (userRes && userRes.ok) {
        const uData = await userRes.json();
        if (uData.user?.name) {
          const first = uData.user.name.split(' ')[0];
          setLandlordName(first || uData.user.name);
        }
      }

      const payload = (await dashRes.json()) as DashboardData & { error?: string };
      if (!dashRes.ok) {
        throw new Error(payload.error || 'Unable to load dashboard data.');
      }
      if (
        !payload.summary ||
        !Array.isArray(payload.properties) ||
        !Array.isArray(payload.collections) ||
        !Array.isArray(payload.pending_high_cost_repairs)
      ) {
        throw new Error('The dashboard response was incomplete.');
      }
      setData(payload);
    } catch (loadError) {
      if (loadError instanceof Error && loadError.name === 'AbortError') return;
      setError(loadError instanceof Error ? loadError.message : 'Unable to load dashboard data.');
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    void loadDashboard(controller.signal);
    return () => controller.abort();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!data) {
    return (
      <div className="min-h-screen space-y-4 bg-slate-50 pb-12">
        <p
          role={error ? 'alert' : 'status'}
          className={`rounded-xl border p-5 text-sm ${
            error
              ? 'border-red-200 bg-red-50 text-red-800'
              : 'border-slate-200 bg-white text-slate-600'
          }`}
        >
          {error ? `Could not load dashboard data: ${error}` : 'Loading portfolio dashboard…'}
        </p>
        {error && (
          <button
            type="button"
            onClick={() => void loadDashboard()}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-50"
          >
            <RefreshCw size={16} aria-hidden="true" className={loading ? 'animate-spin' : ''} />
            Retry
          </button>
        )}
      </div>
    );
  }

  const periodLabel = new Date(`${data.period_start}T00:00:00`).toLocaleDateString('en-KE', {
    month: 'long',
    year: 'numeric',
  });

  const s = data.summary;
  const collectionPercent = Number(s.expected_rent) > 0
    ? ((Number(s.collected_rent) / Number(s.expected_rent)) * 100).toFixed(1)
    : '0';

  /* ── Metric cards ── */
  const metrics: {
    label: string;
    value: string;
    detail: string;
    icon: LucideIcon;
    tone: 'emerald' | 'blue' | 'amber' | 'red' | 'slate';
  }[] = [
    {
      label: 'Total Units',
      value: String(s.total_units),
      detail: 'Across all managed properties',
      icon: Building2,
      tone: 'blue',
    },
    {
      label: 'Occupied',
      value: `${s.occupied_units}`,
      detail: `${s.occupancy_rate.toFixed(1)}% occupancy rate`,
      icon: Home,
      tone: 'emerald',
    },
    {
      label: 'Expected Rent',
      value: formatCurrency(s.expected_rent),
      detail: `Invoiced for ${periodLabel}`,
      icon: Coins,
      tone: 'slate',
    },
    {
      label: 'Collected',
      value: formatCurrency(s.collected_rent),
      detail: `${collectionPercent}% collection rate`,
      icon: Wallet,
      tone: 'emerald',
    },
    {
      label: 'Outstanding Arrears',
      value: formatCurrency(s.arrears),
      detail: 'Overdue balances after payments',
      icon: AlertCircle,
      tone: 'red',
    },
    {
      label: 'Projected Net Payout',
      value: s.projected_net_payout === null
        ? 'Unavailable'
        : formatCurrency(s.projected_net_payout),
      detail: s.projected_net_payout_status === 'not_allocated'
        ? 'Fixed agreement fee cannot be attributed per property'
        : s.projected_net_payout_status === 'not_configured'
          ? 'Agreement or tax settings need configuration'
          : 'Current-period estimate after approved expenses and tax',
      icon: Send,
      tone: 'blue',
    },
  ];

  return (
    <div className="min-h-screen space-y-8 bg-slate-50 pb-12">
      {/* ────── 1. Welcome Header ────── */}
      <header className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-[#102820] to-[#123b2c] px-8 py-8 text-white shadow-xl shadow-emerald-950/10 sm:px-10 sm:py-10">
        {/* Decorative circles */}
        <div className="pointer-events-none absolute -right-12 -top-32 h-80 w-80 rounded-full border border-white/[0.06]" />
        <div className="pointer-events-none absolute -right-4 -top-20 h-56 w-56 rounded-full border border-white/[0.06]" />
        <div className="pointer-events-none absolute bottom-0 left-1/2 h-40 w-40 -translate-x-1/2 translate-y-1/2 rounded-full bg-emerald-500/[0.04]" />

        <div className="relative flex flex-wrap items-end justify-between gap-6">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-emerald-300/80">
              Landlord Portal
            </p>
            <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">
              Welcome back, {landlordName}
            </h1>
            <p className="mt-2 text-[15px] text-emerald-100/60">
              {periodLabel} &middot; Your property portfolio at a glance
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadDashboard()}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-xl border border-white/20 bg-white/10 px-4 py-2.5 text-sm font-semibold text-white backdrop-blur-sm transition hover:bg-white/15 disabled:opacity-50"
          >
            <RefreshCw size={16} aria-hidden="true" className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </header>

      {/* Error banner */}
      {error && (
        <p className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900" role="alert">
          Could not refresh dashboard data: {error}
        </p>
      )}

      {data.pending_high_cost_repairs.length > 0 && (
        <section
          aria-label="High-cost repair quote alerts"
          className="rounded-2xl border border-amber-300 bg-amber-50 p-5 shadow-sm"
        >
          <div className="flex items-start gap-3">
            <AlertCircle size={20} className="mt-0.5 shrink-0 text-amber-700" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <h2 className="font-bold text-amber-950">
                {data.pending_high_cost_repairs.length} high-cost repair quote
                {data.pending_high_cost_repairs.length === 1 ? '' : 's'} awaiting approval
              </h2>
              <p className="mt-1 text-sm text-amber-900">
                These submitted quotes exceed the approval threshold in the management agreement.
              </p>
              <ul className="mt-3 divide-y divide-amber-200">
                {data.pending_high_cost_repairs.map((quote) => (
                  <li key={quote.id} className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm">
                    <span className="min-w-56 flex-1">
                      <strong>{quote.property_name}{quote.unit_code ? ` · Unit ${quote.unit_code}` : ''}</strong>
                      <span className="block text-xs text-amber-800">
                        {quote.ticket_title} · {quote.vendor_name}
                      </span>
                    </span>
                    <span className="text-right font-semibold text-amber-950">
                      {formatCurrency(quote.amount)}
                      <span className="block text-xs font-normal text-amber-800">
                        Approval threshold {formatCurrency(quote.approval_threshold)}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>
      )}

      {/* ────── 2. Portfolio KPI Cards ────── */}
      <section aria-label="Portfolio metrics" className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {metrics.map((metric) => (
          <KpiCard key={metric.label} {...metric} />
        ))}
      </section>

      {/* ────── 3. Revenue Performance ────── */}
      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold text-slate-900">Revenue Performance</h2>
            <p className="mt-1 text-sm text-slate-500">
              Collection progress for {periodLabel}
            </p>
          </div>
          <span className="rounded-full bg-emerald-50 px-4 py-1.5 text-sm font-bold text-emerald-700">
            {collectionPercent}% collected
          </span>
        </div>

        <div className="mt-6">
          <div className="h-4 w-full overflow-hidden rounded-full bg-slate-100">
            <div
              className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-emerald-400 transition-all duration-700 ease-out"
              style={{ width: `${collectionPercent}%` }}
            />
          </div>
          <div className="mt-3 flex items-center justify-between text-sm">
            <p className="text-slate-600">
              <span className="font-semibold text-slate-900">{formatCurrency(s.collected_rent)}</span>
              {' '}of {formatCurrency(s.expected_rent)} collected
            </p>
            <p className="text-sm text-red-600 font-medium">
              {formatCurrency(s.arrears)} outstanding
            </p>
          </div>
        </div>
      </section>

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-200 bg-slate-50/80 px-6 py-5">
          <h2 className="text-lg font-bold text-slate-900">Recent Matched Collections</h2>
          <p className="mt-1 text-sm text-slate-500">
            Cleared M-Pesa and bank allocations across your portfolio.
          </p>
        </div>
        {data.collections.length === 0 ? (
          <p className="p-6 text-sm text-slate-500">No matched M-Pesa or bank collections are available yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-white text-left text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th scope="col" className="px-6 py-3.5 font-semibold">Received</th>
                  <th scope="col" className="px-6 py-3.5 font-semibold">Property / unit</th>
                  <th scope="col" className="px-6 py-3.5 font-semibold">Tenant</th>
                  <th scope="col" className="px-6 py-3.5 font-semibold">Method / reference</th>
                  <th scope="col" className="px-6 py-3.5 text-right font-semibold">Allocated</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.collections.map((collection) => (
                  <tr key={collection.id} className="text-slate-700">
                    <td className="whitespace-nowrap px-6 py-4">
                      {new Date(collection.received_at).toLocaleString('en-KE', {
                        timeZone: 'Africa/Nairobi',
                        dateStyle: 'medium',
                        timeStyle: 'short',
                      })}
                    </td>
                    <td className="px-6 py-4">
                      <span className="block font-semibold text-slate-900">{collection.property_name}</span>
                      <span className="text-xs text-slate-500">Unit {collection.unit_code}</span>
                    </td>
                    <td className="whitespace-nowrap px-6 py-4">{collection.tenant_name}</td>
                    <td className="whitespace-nowrap px-6 py-4">
                      <span className="uppercase">{collection.source === 'mpesa' ? 'M-Pesa' : 'Bank'}</span>
                      <span className="ml-2 text-xs text-slate-500">{collection.reference}</span>
                    </td>
                    <td className="whitespace-nowrap px-6 py-4 text-right font-semibold text-emerald-700">
                      {formatCurrency(collection.amount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ────── 4. Property-by-Property Breakdown ────── */}
      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-slate-50/80 px-6 py-5">
          <div>
            <h2 className="text-lg font-bold text-slate-900">Property-by-Property Breakdown</h2>
            <p className="mt-1 text-sm text-slate-500">
              Performance summary across your portfolio
            </p>
          </div>
          <Link
            href="/landlord/properties"
            className="inline-flex items-center gap-1.5 text-sm font-semibold text-emerald-700 hover:text-emerald-800 transition"
          >
            Manage properties <ArrowRight size={14} />
          </Link>
        </div>

        {data.properties.length === 0 ? (
          <div className="p-10 text-center">
            <CheckCircle2 className="mx-auto text-slate-300" size={32} aria-hidden="true" />
            <p className="mt-4 font-semibold text-slate-800">No properties linked yet</p>
            <p className="mt-1 text-sm text-slate-500">
              Once a property is linked to your account, its performance will appear here.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-white text-left text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th scope="col" className="px-6 py-3.5 font-semibold">Property</th>
                  <th scope="col" className="px-6 py-3.5 font-semibold">Units</th>
                  <th scope="col" className="px-6 py-3.5 font-semibold">Occupied</th>
                  <th scope="col" className="px-6 py-3.5 font-semibold">Expected</th>
                  <th scope="col" className="px-6 py-3.5 font-semibold">Collected</th>
                  <th scope="col" className="px-6 py-3.5 font-semibold">Arrears</th>
                  <th scope="col" className="px-6 py-3.5 font-semibold">Collection %</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.properties.map((property) => {
                  const propCollection = Number(property.expected_rent) > 0
                    ? ((Number(property.collected_rent) / Number(property.expected_rent)) * 100).toFixed(1)
                    : '0';
                  return (
                    <tr key={property.id} className="text-slate-700 transition hover:bg-slate-50/60">
                      <td className="px-6 py-4">
                        <span className="block font-semibold text-slate-900">{property.name}</span>
                        <span className="mt-0.5 block text-xs text-slate-400">{property.location}</span>
                      </td>
                      <td className="whitespace-nowrap px-6 py-4 font-medium">{property.total_units}</td>
                      <td className="whitespace-nowrap px-6 py-4">
                        <span className="font-medium">{property.occupied_units}</span>
                        <span className="ml-1 text-xs text-slate-400">({property.occupancy_rate.toFixed(1)}%)</span>
                      </td>
                      <td className="whitespace-nowrap px-6 py-4">{formatCurrency(property.expected_rent)}</td>
                      <td className="whitespace-nowrap px-6 py-4 font-medium text-emerald-700">
                        {formatCurrency(property.collected_rent)}
                      </td>
                      <td className="whitespace-nowrap px-6 py-4 text-red-600">
                        {formatCurrency(property.arrears)}
                      </td>
                      <td className="whitespace-nowrap px-6 py-4">
                        <div className="flex items-center gap-2">
                          <div className="h-2 w-16 overflow-hidden rounded-full bg-slate-100">
                            <div
                              className="h-full rounded-full bg-emerald-500"
                              style={{ width: `${propCollection}%` }}
                            />
                          </div>
                          <span className="text-xs font-semibold text-slate-600">{propCollection}%</span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ────── 5 & 6. Expense and Maintenance Overview ────── */}
      <section className="grid gap-6 lg:grid-cols-2" aria-label="Expenses and maintenance summary">
        <article className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-bold text-slate-900">Submitted Expenses</h2>
              <p className="mt-1 text-sm text-slate-500">Expenses awaiting agency processing</p>
            </div>
            <span className="rounded-full bg-amber-50 px-3.5 py-1 text-xs font-bold text-amber-700 border border-amber-200">
              {s.pending_expense_count} pending
            </span>
          </div>
          <p className="mt-5 rounded-xl border border-slate-200 bg-slate-50/50 p-4 text-sm text-slate-700">
            {formatCurrency(s.pending_expenses)} in submitted expenses for this period.
          </p>
        </article>

        <article className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-bold text-slate-900">Maintenance Overview</h2>
              <p className="mt-1 text-sm text-slate-500">Open requests across your properties</p>
            </div>
            <span className="rounded-full bg-blue-50 px-3.5 py-1 text-xs font-bold text-blue-700 border border-blue-200">
              {s.open_maintenance_count} open
            </span>
          </div>
          <p className="mt-5 rounded-xl border border-slate-200 bg-slate-50/50 p-4 text-sm text-slate-700">
            {s.open_maintenance_count === 0
              ? 'There are no open maintenance requests.'
              : `${s.open_maintenance_count} maintenance request${s.open_maintenance_count === 1 ? '' : 's'} need attention.`}
          </p>
        </article>
      </section>

      {/* ────── 7. Financial Quick Links ────── */}
      <section aria-label="Financial quick links" className="grid grid-cols-1 gap-4">
        {[
          {
            title: 'Statements & Remittances',
            href: '/landlord/statements',
            description: 'Review monthly statements, deposit balances, and payout status',
            icon: FileText,
            tone: 'emerald' as const,
          },
        ].map((link) => {
          const iconBg: Record<string, string> = {
            emerald: 'bg-emerald-50 text-emerald-600',
            blue: 'bg-blue-50 text-blue-600',
            amber: 'bg-amber-50 text-amber-600',
          };
          return (
            <Link
              key={link.title}
              href={link.href}
              className="group rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:shadow-md hover:border-emerald-200"
            >
              <div className="flex items-start gap-4">
                <span className={`shrink-0 rounded-xl p-3 ${iconBg[link.tone]}`}>
                  <link.icon size={22} aria-hidden="true" />
                </span>
                <div className="min-w-0">
                  <h3 className="font-bold text-slate-900 group-hover:text-emerald-700 transition">
                    {link.title}
                  </h3>
                  <p className="mt-1 text-sm text-slate-500">{link.description}</p>
                </div>
              </div>
              <div className="mt-4 flex items-center gap-1 text-sm font-semibold text-emerald-600 opacity-0 transition group-hover:opacity-100">
                Open <ArrowRight size={14} />
              </div>
            </Link>
          );
        })}
      </section>
    </div>
  );
}
