'use client';

import { useCallback, useEffect, useState } from 'react';
import { Check, ClipboardCheck, RefreshCw, X } from 'lucide-react';

type Quotation = {
  id: number;
  ticket_id: number;
  ticket_title: string;
  ticket_description: string;
  property_id: number;
  property_name: string;
  unit_code: string | null;
  vendor_name: string;
  vendor_trade: string;
  description: string;
  amount: string;
  approval_threshold: string;
  valid_until: string | null;
  submitted_at: string;
};

type ApiError = { error?: string; detail?: string };

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 2,
});

export default function LandlordApprovalsPage() {
  const [quotations, setQuotations] = useState<Quotation[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [rejectionReasons, setRejectionReasons] = useState<Record<number, string>>({});

  const loadQuotations = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/landlord/maintenance/quotations', {
        cache: 'no-store',
      });
      const payload = await response.json() as { quotations?: Quotation[] } & ApiError;
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || 'Unable to load quotation approvals.');
      }
      if (!Array.isArray(payload.quotations)) {
        throw new Error('The quotation response was incomplete.');
      }
      setQuotations(payload.quotations);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load quotation approvals.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadQuotations();
  }, [loadQuotations]);

  const decide = async (quotationId: number, approved: boolean) => {
    const rejectionReason = rejectionReasons[quotationId]?.trim() ?? '';
    if (!approved && !rejectionReason) {
      setError('Add a reason before rejecting a quotation.');
      return;
    }

    setBusyId(quotationId);
    setError('');
    try {
      const response = await fetch(
        `/api/landlord/maintenance/quotations/${quotationId}/decision`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            approved,
            ...(approved ? {} : { rejection_reason: rejectionReason }),
          }),
        },
      );
      const payload = await response.json() as ApiError;
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || 'Unable to submit the quotation decision.');
      }
      await loadQuotations();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to submit the quotation decision.');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-emerald-700">My portfolio</p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">
            Expense approvals
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-600">
            Review maintenance quotations above the approval threshold in your management agreement.
            Approved landlord allocations are included in statements and remittances prepared afterward.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void loadQuotations()}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
          Refresh
        </button>
      </header>

      {error && (
        <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </p>
      )}

      {loading && quotations.length === 0 ? (
        <p className="text-sm text-slate-500">Loading quotations for your properties…</p>
      ) : quotations.length > 0 ? (
        <section aria-label="Pending maintenance quotations" className="space-y-4">
          {quotations.map((quotation) => (
            <article
              key={quotation.id}
              className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6"
            >
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="text-xs font-bold uppercase tracking-wide text-emerald-700">
                    {quotation.property_name}
                    {quotation.unit_code ? ` · Unit ${quotation.unit_code}` : ''}
                    {' · '}Ticket #{quotation.ticket_id}
                  </p>
                  <h2 className="mt-1 text-lg font-bold text-slate-900">{quotation.ticket_title}</h2>
                  <p className="mt-1 max-w-3xl text-sm text-slate-600">{quotation.ticket_description}</p>
                </div>
                <p className="whitespace-nowrap rounded-xl bg-amber-50 px-4 py-2 text-lg font-bold text-amber-900">
                  {currency.format(Number(quotation.amount))}
                </p>
              </div>

              <dl className="mt-5 grid gap-4 border-t border-slate-100 pt-4 text-sm sm:grid-cols-2">
                <div>
                  <dt className="font-semibold text-slate-500">Vendor</dt>
                  <dd className="mt-1 text-slate-900">{quotation.vendor_name} · {quotation.vendor_trade}</dd>
                </div>
                <div>
                  <dt className="font-semibold text-slate-500">Quoted work</dt>
                  <dd className="mt-1 text-slate-900">{quotation.description}</dd>
                </div>
                <div>
                  <dt className="font-semibold text-slate-500">Approval threshold</dt>
                  <dd className="mt-1 text-slate-900">{currency.format(Number(quotation.approval_threshold))}</dd>
                </div>
                <div>
                  <dt className="font-semibold text-slate-500">Submitted</dt>
                  <dd className="mt-1 text-slate-900">
                    {new Date(quotation.submitted_at).toLocaleString('en-KE')}
                    {quotation.valid_until ? ` · Valid until ${quotation.valid_until}` : ''}
                  </dd>
                </div>
              </dl>

              <div className="mt-5 flex flex-col gap-3 border-t border-slate-100 pt-4 sm:flex-row sm:items-end sm:justify-between">
                <label className="grid flex-1 gap-1 text-sm font-medium text-slate-700">
                  Rejection reason (required to reject)
                  <textarea
                    value={rejectionReasons[quotation.id] ?? ''}
                    onChange={(event) => setRejectionReasons((current) => ({
                      ...current,
                      [quotation.id]: event.target.value,
                    }))}
                    rows={2}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2"
                  />
                </label>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => void decide(quotation.id, false)}
                    disabled={busyId === quotation.id}
                    className="inline-flex items-center gap-2 rounded-lg border border-red-200 px-4 py-2 text-sm font-semibold text-red-800 hover:bg-red-50 disabled:opacity-50"
                  >
                    <X size={16} aria-hidden="true" /> Reject
                  </button>
                  <button
                    type="button"
                    onClick={() => void decide(quotation.id, true)}
                    disabled={busyId === quotation.id}
                    className="inline-flex items-center gap-2 rounded-lg bg-emerald-700 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-800 disabled:opacity-50"
                  >
                    <Check size={16} aria-hidden="true" /> Approve
                  </button>
                </div>
              </div>
            </article>
          ))}
        </section>
      ) : !loading && !error ? (
        <section className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center">
          <ClipboardCheck className="mx-auto text-slate-400" size={28} aria-hidden="true" />
          <h2 className="mt-3 font-semibold text-slate-900">No quotations awaiting your review</h2>
          <p className="mt-1 text-sm text-slate-500">
            Above-threshold maintenance quotations submitted for your properties will appear here.
          </p>
        </section>
      ) : null}
    </div>
  );
}
