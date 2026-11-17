'use client';

import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { RefreshCw } from 'lucide-react';
import { useRouter } from 'next/navigation';

type RemittanceSummary = {
  collected_invoice_allocations: string;
  commission: string;
  expenses: string;
  tax: string | null;
  net_payout: string;
};

type Remittance = {
  id: number;
  landlord: { id: number; name: string };
  period_start: string;
  period_end: string;
  revision: number;
  summary: RemittanceSummary;
  status: string;
  prepared_by: number;
  reviewed_by: number | null;
  rejection_reason: string;
  payout_reference: string | null;
  payout_date: string | null;
};

type LandlordOption = { id: number; name: string };
type ApiData = { landlords: LandlordOption[]; remittances: Remittance[] };
type CurrentUser = { id: number; roles: string[] };

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 2,
});

function todayInNairobi() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Nairobi',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
}

function initialPeriod() {
  const today = todayInNairobi();
  return { from: `${today.slice(0, 7)}-01`, to: today };
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, cache: 'no-store' });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || 'The request could not be completed.');
  return payload as T;
}

function hasRole(roles: string[], role: string) {
  return roles.some((item) => item.trim().toLowerCase() === role.toLowerCase());
}

export default function LandlordRemittancesPage() {
  const router = useRouter();
  const [period, setPeriod] = useState(initialPeriod);
  const [landlordId, setLandlordId] = useState('');
  const [data, setData] = useState<ApiData>({ landlords: [], remittances: [] });
  const [roles, setRoles] = useState<string[]>([]);
  const [userId, setUserId] = useState<number | null>(null);
  const [payoutReferences, setPayoutReferences] = useState<Record<number, string>>({});
  const [payoutDates, setPayoutDates] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const loadData = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [remittanceData, userData] = await Promise.all([
        requestJson<ApiData>('/api/landlord/remittances'),
        requestJson<{ user: CurrentUser }>('/api/auth/me'),
      ]);
      if (!Array.isArray(remittanceData.landlords) || !Array.isArray(remittanceData.remittances)) {
        throw new Error('The remittance response was incomplete.');
      }
      setData(remittanceData);
      setRoles(userData.user.roles);
      setUserId(userData.user.id);
      setLandlordId((current) => current || String(remittanceData.landlords[0]?.id || ''));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Unable to load remittances.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    if (!loading && (hasRole(roles, 'Landlord') || hasRole(roles, 'Landlord (portal)'))) {
      router.replace('/landlord/statements');
    }
  }, [loading, roles, router]);

  const canPrepare = roles.some((role) =>
    ['Super Admin (Director)', 'Branch Manager', 'Accountant', 'Property Officer / Agent']
      .some((allowed) => role.trim().toLowerCase() === allowed.toLowerCase()),
  );
  const canApprove = hasRole(roles, 'Super Admin (Director)') || hasRole(roles, 'Branch Manager');
  const canRecordPayout = ['Super Admin (Director)', 'Branch Manager', 'Accountant']
    .some((role) => hasRole(roles, role));

  const submitRemittance = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!landlordId || !period.from || !period.to || period.from > period.to) {
      setError('Choose a landlord and a valid date range.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await requestJson('/api/landlord/remittances', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          landlord_id: Number(landlordId),
          period_start: period.from,
          period_end: period.to,
        }),
      });
      await loadData();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'Unable to prepare the remittance.');
    } finally {
      setBusy(false);
    }
  };

  const decide = async (remittance: Remittance, action: 'approve' | 'reject') => {
    const reviewNote = action === 'reject'
      ? window.prompt('Reason for rejecting this remittance?')?.trim()
      : '';
    if (action === 'reject' && !reviewNote) return;
    setBusy(true);
    setError('');
    try {
      await requestJson(`/api/landlord/remittances/${remittance.id}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, review_note: reviewNote || '' }),
      });
      await loadData();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'Unable to review the remittance.');
    } finally {
      setBusy(false);
    }
  };

  const recordPayout = async (remittance: Remittance) => {
    setBusy(true);
    setError('');
    try {
      await requestJson(`/api/landlord/remittances/${remittance.id}/payout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          payout_reference: payoutReferences[remittance.id] || '',
          payout_date: payoutDates[remittance.id] || todayInNairobi(),
        }),
      });
      await loadData();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'Unable to record the payout.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-slate-900">Landlord remittances</h1>
          <p className="mt-1 text-sm text-slate-600">
            Prepare, independently approve, and track manual landlord payments. No payment is sent by this system.
          </p>
        </div>
        <button type="button" onClick={() => void loadData()} disabled={loading} className="inline-flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-semibold disabled:opacity-60">
          <RefreshCw size={16} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </header>

      {error && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      {canPrepare && data.landlords.length > 0 && (
        <form onSubmit={submitRemittance} className="grid gap-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm md:grid-cols-[2fr_1fr_1fr_auto]">
          <label className="grid gap-1 text-sm font-medium text-slate-700">
            Landlord
            <select value={landlordId} onChange={(event) => setLandlordId(event.target.value)} required className="rounded-lg border border-slate-300 px-3 py-2">
              <option value="">Select a landlord</option>
              {data.landlords.map((landlord) => <option key={landlord.id} value={landlord.id}>{landlord.name}</option>)}
            </select>
          </label>
          <label className="grid gap-1 text-sm font-medium text-slate-700">
            Period from
            <input type="date" required value={period.from} onChange={(event) => setPeriod((current) => ({ ...current, from: event.target.value }))} className="rounded-lg border border-slate-300 px-3 py-2" />
          </label>
          <label className="grid gap-1 text-sm font-medium text-slate-700">
            Period to
            <input type="date" required value={period.to} onChange={(event) => setPeriod((current) => ({ ...current, to: event.target.value }))} className="rounded-lg border border-slate-300 px-3 py-2" />
          </label>
          <button type="submit" disabled={busy || loading} className="self-end rounded-lg bg-emerald-700 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-800 disabled:opacity-60">
            Prepare remittance
          </button>
        </form>
      )}

      {loading && <p className="text-sm text-slate-600">Loading remittances…</p>}
      {!loading && !error && data.remittances.length === 0 && <p className="rounded-xl border bg-white p-5 text-sm text-slate-600">No remittances have been prepared.</p>}

      <div className="space-y-4">
        {data.remittances.map((remittance) => (
          <article key={remittance.id} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-bold text-slate-900">{remittance.landlord.name}</h2>
                <p className="text-sm text-slate-500">
                  {remittance.period_start} to {remittance.period_end} · revision {remittance.revision}
                </p>
              </div>
              <span className="rounded-full bg-slate-100 px-3 py-1 text-sm font-semibold text-slate-700">
                {remittance.status.replaceAll('_', ' ')}
              </span>
            </div>

            <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {[
                ['Collections', remittance.summary.collected_invoice_allocations],
                ['Commission', remittance.summary.commission],
                ['Approved expenses', remittance.summary.expenses],
                ['Tax', remittance.summary.tax],
                ['Net payout', remittance.summary.net_payout],
              ].map(([label, amount]) => (
                <div key={label} className="rounded-lg bg-slate-50 p-3">
                  <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
                  <dd className="mt-1 font-semibold text-slate-900">{amount === null ? 'Not configured' : currency.format(Number(amount))}</dd>
                </div>
              ))}
            </dl>

            {remittance.rejection_reason && <p className="mt-3 text-sm text-red-700">Rejection reason: {remittance.rejection_reason}</p>}
            {remittance.payout_reference && (
              <p className="mt-3 text-sm text-slate-600">
                Manual payout recorded: {remittance.payout_reference} · {remittance.payout_date}
              </p>
            )}

            <div className="mt-4 flex flex-wrap items-end gap-3">
              {canApprove && remittance.status === 'awaiting_approval' && remittance.prepared_by !== userId && (
                <>
                  <button type="button" onClick={() => void decide(remittance, 'approve')} disabled={busy} className="rounded-lg bg-emerald-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60">Approve</button>
                  <button type="button" onClick={() => void decide(remittance, 'reject')} disabled={busy} className="rounded-lg border border-red-300 px-4 py-2 text-sm font-semibold text-red-700 disabled:opacity-60">Reject</button>
                </>
              )}
              {canRecordPayout && remittance.status === 'approved' && (
                <>
                  <label className="grid gap-1 text-xs font-medium text-slate-600">
                    Payment reference
                    <input value={payoutReferences[remittance.id] || ''} onChange={(event) => setPayoutReferences((current) => ({ ...current, [remittance.id]: event.target.value }))} maxLength={100} className="rounded-lg border px-3 py-2 text-sm" />
                  </label>
                  <label className="grid gap-1 text-xs font-medium text-slate-600">
                    Payment date
                    <input type="date" value={payoutDates[remittance.id] || todayInNairobi()} onChange={(event) => setPayoutDates((current) => ({ ...current, [remittance.id]: event.target.value }))} className="rounded-lg border px-3 py-2 text-sm" />
                  </label>
                  <button type="button" onClick={() => void recordPayout(remittance)} disabled={busy} className="rounded-lg bg-slate-800 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60">Record manual payout</button>
                </>
              )}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
