'use client';

import { type FormEvent, useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  AlertCircle,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  ClipboardList,
  Home,
  Info,
  Loader2,
  Megaphone,
  Receipt,
  RefreshCw,
  Smartphone,
  UserCircle,
  Wrench,
  X,
} from 'lucide-react';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type DashboardInvoice = {
  id: number;
  number: string;
  period_start: string;
  period_end: string;
  due_date: string;
  status: 'settled' | 'partial' | 'unpaid';
  total: string;
  amount_paid: string;
  waived_amount: string;
  approved_waivers?: { amount: string; reason: string; approved_at: string }[];
  penalty: string;
  balance_due: string;
  lines: { id: number; charge_type: string; description: string; amount: string }[];
};

type DashboardPayment = {
  id: number;
  amount: string;
  reference: string;
  source: string;
  status: string;
  instrument_status: string;
  received_at: string;
  allocations: { invoice_id: number; invoice_number: string; amount: string }[];
  receipt: {
    number: string;
    amount: string;
    balance_after: string;
    issued_at: string;
  } | null;
};

type TenantDashboard = {
  tenant: {
    name: string;
    email: string;
    phone: string;
    unit_code: string | null;
    property_name: string | null;
    paybill_number?: string | null;
    move_in_date: string | null;
    notice_period_days: number | null;
  };
  summary: {
    balance_due: string;
    credit_balance: string;
    net_balance_due: string;
    next_due_date: string | null;
  };
  invoices: DashboardInvoice[];
  payments: DashboardPayment[];
  deposit: {
    balance: string;
    target_amount: string;
    entries: {
      id: number;
      entry_type: string;
      amount: string;
      reference: string;
      description: string;
      created_at: string;
    }[];
  } | null;
  move_out_settlements: {
    id: number;
    property_name: string;
    unit_code: string;
    paybill_number: string | null;
    actual_move_out_date: string;
    notice_status: string;
    inspection_completed_at: string | null;
    settlement_reviewed_at: string | null;
    deposit_balance: string;
    arrears_amount: string;
    utility_amount: string;
    damages_amount: string;
    deduction_from_deposit: string;
    refund_amount: string;
    shortfall_amount: string;
    final_readings: { utility_type: string; name: string; current_value: string; amount: string }[];
    damage_items: { description: string; amount: string }[];
    maintenance_required: boolean;
    inspection_photos: { index: number; caption: string }[];
    status: string;
    payout_reference: string | null;
    payout_date: string | null;
    rejection_reason: string;
  }[];
};

type TenantAnnouncement = {
  id: number;
  title: string;
  body: string;
  expires_at: string | null;
};

type TenantMoveOutNotice = {
  id: number;
  proposed_move_out_date: string;
  status: string;
  reason: string;
  review_note: string;
};

type TenantMoveOutContext = {
  tenancy_id: number;
  unit_code: string;
  property_name: string;
  notice_period_days: number;
  notice: TenantMoveOutNotice | null;
  tenancies: {
    id: number;
    unit_code: string;
    property_name: string;
    notice_period_days: number;
    notice: TenantMoveOutNotice | null;
  }[];
};

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

function formatDate(value: string | null) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('en-KE', {
    dateStyle: 'medium',
    timeZone: 'Africa/Nairobi',
  }).format(new Date(value));
}

function dateAfterNairobiDays(days: number) {
  const today = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Nairobi',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
  const [year, month, day] = today.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

function statusLabel(status: DashboardInvoice['status']) {
  if (status === 'settled') return 'Settled';
  if (status === 'partial') return 'Partially Paid';
  return 'Unpaid';
}

function statusBadge(status: DashboardInvoice['status']) {
  if (status === 'settled')
    return 'bg-emerald-100 text-emerald-800 ring-1 ring-emerald-200';
  if (status === 'partial')
    return 'bg-amber-100 text-amber-800 ring-1 ring-amber-200';
  return 'bg-red-100 text-red-800 ring-1 ring-red-200';
}

function balanceIndicator(balance: number) {
  if (balance <= 0) return { color: 'text-emerald-600', bg: 'bg-emerald-500', label: 'Settled' };
  if (balance < 15000) return { color: 'text-amber-600', bg: 'bg-amber-500', label: 'Partial' };
  return { color: 'text-red-600', bg: 'bg-red-500', label: 'Overdue' };
}

/* ------------------------------------------------------------------ */
/*  Page Component                                                     */
/* ------------------------------------------------------------------ */

export default function TenantDashboardPage() {
  const [dashboard, setDashboard] = useState<TenantDashboard | null>(null);
  const [announcements, setAnnouncements] = useState<TenantAnnouncement[]>([]);
  const [moveOutContext, setMoveOutContext] = useState<TenantMoveOutContext | null>(null);
  const [selectedMoveOutTenancyId, setSelectedMoveOutTenancyId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [moveOutDate, setMoveOutDate] = useState('');
  const [moveOutReason, setMoveOutReason] = useState('');
  const [submittingNotice, setSubmittingNotice] = useState(false);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const dashRes = await fetch('/api/tenant/invoices', { cache: 'no-store' });
      if (!dashRes.ok) {
        const payload = await dashRes.json().catch(() => ({}));
        throw new Error(payload.error || 'Unable to load your tenant account.');
      }
      setDashboard((await dashRes.json()) as TenantDashboard);
      const [announcementResult, moveOutResult] = await Promise.allSettled([
        fetch('/api/announcements', { cache: 'no-store' }),
        fetch('/api/tenant/move-out-notice', { cache: 'no-store' }),
      ]);
      const warnings: string[] = [];
      if (announcementResult.status === 'fulfilled' && announcementResult.value.ok) {
        const announcementPayload = await announcementResult.value.json();
        setAnnouncements(announcementPayload.announcements || []);
      } else {
        setAnnouncements([]);
        warnings.push('Announcements could not be loaded.');
      }
      if (moveOutResult.status === 'fulfilled' && moveOutResult.value.ok) {
        const context = await moveOutResult.value.json() as TenantMoveOutContext;
        setMoveOutContext(context);
        setSelectedMoveOutTenancyId((current) => current ?? context.tenancy_id);
      } else {
        setMoveOutContext(null);
        if (moveOutResult.status === 'rejected' || moveOutResult.value.status !== 404) {
          warnings.push('Move-out notice information could not be loaded.');
        }
      }
      setLoadError(warnings.join(' '));
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Unable to load your tenant account.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  async function submitMoveOutNotice(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmittingNotice(true);
    setLoadError('');
    try {
      const response = await fetch('/api/tenant/move-out-notice', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tenancy_id: selectedMoveOutTenancyId,
          proposed_move_out_date: moveOutDate,
          reason: moveOutReason,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Unable to submit your move-out notice.');
      setMoveOutReason('');
      setMoveOutDate('');
      await loadDashboard();
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Unable to submit your move-out notice.');
    } finally {
      setSubmittingNotice(false);
    }
  }

  // M-Pesa STK Push Payment State
  const [payModalOpen, setPayModalOpen] = useState(false);
  const [payInvoiceId, setPayInvoiceId] = useState<number | null>(null);
  const [payAmount, setPayAmount] = useState('');
  const [payPhone, setPayPhone] = useState('');
  const [payStatus, setPayStatus] = useState<
    'idle' | 'initiating' | 'pending' | 'success' | 'failed'
  >('idle');
  const [payMessage, setPayMessage] = useState('');
  const [, setCheckoutRequestId] = useState<string | null>(null);
  const [pollingTimer, setPollingTimer] = useState<NodeJS.Timeout | null>(null);

  useEffect(() => {
    return () => {
      if (pollingTimer) clearInterval(pollingTimer);
    };
  }, [pollingTimer]);

  const handleOpenPayModal = (
    invoiceId?: number | null,
    defaultAmount?: number
  ) => {
    if (pollingTimer) {
      clearInterval(pollingTimer);
      setPollingTimer(null);
    }
    setPayInvoiceId(invoiceId ?? null);
    if (defaultAmount !== undefined && defaultAmount > 0) {
      setPayAmount(String(defaultAmount));
    } else if (dashboard) {
      const selectedDue = invoiceId
        ? Number(
            dashboard.invoices.find((invoice) => invoice.id === invoiceId)?.balance_due ??
              defaultAmount ??
              0,
          )
        : Number(dashboard.summary.balance_due);
      setPayAmount(
        String(
          Math.max(
            selectedDue - Number(dashboard.summary.credit_balance || 0),
            0,
          ),
        ),
      );
    } else {
      setPayAmount('');
    }
    setPayPhone('');
    setPayStatus('idle');
    setPayMessage('');
    setCheckoutRequestId(null);
    setPayModalOpen(true);
  };

  const handleClosePayModal = () => {
    if (pollingTimer) {
      clearInterval(pollingTimer);
      setPollingTimer(null);
    }
    setPayModalOpen(false);
    if (payStatus === 'success') {
      void loadDashboard();
    }
  };

  const handleInitiatePay = async (e: FormEvent) => {
    e.preventDefault();
    const numAmount = Number(payAmount);
    const invoiceDue = payInvoiceId
      ? Number(
          dashboard?.invoices.find((invoice) => invoice.id === payInvoiceId)?.balance_due ?? 0,
        )
      : Number(dashboard?.summary.balance_due ?? 0);
    const availableCredit = Number(dashboard?.summary.credit_balance ?? 0);
    const remainingAfterCredit = Math.max(invoiceDue - availableCredit, 0);
    if (
      !payAmount.trim() ||
      !Number.isFinite(numAmount) ||
      numAmount < 0 ||
      (numAmount === 0 && remainingAfterCredit > 0)
    ) {
      setPayMessage(
        'Enter a positive amount, or use available credit to cover the full invoice.',
      );
      setPayStatus('failed');
      return;
    }
    if (numAmount > 0 && !payPhone.trim()) {
      setPayMessage('Please enter a Safaricom phone number.');
      setPayStatus('failed');
      return;
    }

    setPayStatus('initiating');
    setPayMessage('Sending M-Pesa prompt to your phone...');

    try {
      const res = await fetch('/api/tenant/pay-rent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone: payPhone.trim(),
          amount: payAmount,
          invoiceId: payInvoiceId || undefined,
        }),
      });

      const resData = await res.json();
      if (!res.ok) {
        throw new Error(
          resData.detail ||
            resData.error ||
            'Failed to initiate M-Pesa payment prompt.'
        );
      }

      if (resData.paymentCoveredByCredit) {
        setPayStatus('success');
        setPayMessage(
          resData.message || 'Available rent credit covered this invoice.',
        );
        void loadDashboard();
        return;
      }

      const reqId = resData.checkoutRequestId;
      setCheckoutRequestId(reqId);
      setPayStatus('pending');
      setPayMessage(
        resData.message ||
          'M-Pesa PIN prompt sent to your phone! Please enter your PIN.'
      );

      let elapsedSeconds = 0;
      const timer = setInterval(async () => {
        elapsedSeconds += 3;
        if (elapsedSeconds > 60) {
          clearInterval(timer);
          setPollingTimer(null);
          setPayStatus('failed');
          setPayMessage(
            'Payment verification timed out. If you entered your PIN, it may take a minute to reflect in your ledger.'
          );
          return;
        }

        try {
          const statusRes = await fetch(
            `/api/tenant/pay-rent/status/${encodeURIComponent(reqId)}`
          );
          if (statusRes.ok) {
            const statusData = await statusRes.json();
            if (statusData.status === 'succeeded') {
              clearInterval(timer);
              setPollingTimer(null);
              setPayStatus('success');
              setPayMessage(
                'Payment received and confirmed! Your ledger and receipt have been updated.'
              );
              void loadDashboard();
            } else if (statusData.status === 'failed') {
              clearInterval(timer);
              setPollingTimer(null);
              setPayStatus('failed');
              setPayMessage(
                statusData.resultDescription ||
                  'M-Pesa payment was cancelled or declined.'
              );
            }
          }
        } catch {
          // ignore transient polling errors
        }
      }, 3000);

      setPollingTimer(timer);
    } catch (err) {
      setPayStatus('failed');
      setPayMessage(
        err instanceof Error
          ? err.message
          : 'Unable to send M-Pesa prompt.'
      );
    }
  };

  if (loading && !dashboard) {
    return <p className="p-8 text-slate-500">Loading your tenant account…</p>;
  }
  if (loadError && !dashboard) {
    return (
      <div role="alert" className="m-6 rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-800">
        <p>{loadError}</p>
        <button type="button" onClick={() => void loadDashboard()} className="mt-3 font-semibold underline">Try again</button>
      </div>
    );
  }
  if (!dashboard) {
    return <p role="alert" className="m-6 text-sm text-slate-600">No tenant account data is available.</p>;
  }
  const data = dashboard;
  const balance = Number(data.summary.balance_due);
  const creditBalance = Number(data.summary.credit_balance || 0);
  const netBalance = Number(data.summary.net_balance_due || 0);
  const firstOutstandingInvoice = data.invoices.find(
    (invoice) => Number(invoice.balance_due) > 0,
  );
  const indicator = balanceIndicator(netBalance);
  const selectedMoveOutTenancy = moveOutContext?.tenancies.find(
    (tenancy) => tenancy.id === selectedMoveOutTenancyId,
  ) ?? (moveOutContext
    ? {
        id: moveOutContext.tenancy_id,
        unit_code: moveOutContext.unit_code,
        property_name: moveOutContext.property_name,
        notice_period_days: moveOutContext.notice_period_days,
        notice: moveOutContext.notice,
      }
    : null);
  const canSubmitMoveOutNotice = Boolean(
    selectedMoveOutTenancy &&
    (!selectedMoveOutTenancy.notice || ['rejected', 'withdrawn'].includes(selectedMoveOutTenancy.notice.status)),
  );
  const earliestMoveOutDate = selectedMoveOutTenancy
    ? dateAfterNairobiDays(selectedMoveOutTenancy.notice_period_days)
    : '';

  return (
    <div className="space-y-6">
      {/* ============================================================ */}
      {/*  1. Resident Welcome Header                                  */}
      {/* ============================================================ */}
      <header className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-[#102820] via-[#123b2c] to-[#164a36] px-7 py-8 text-white shadow-xl shadow-emerald-950/15 sm:px-10 sm:py-10">
        <div className="absolute -right-16 -top-16 h-56 w-56 rounded-full bg-white/[0.03]" />
        <div className="absolute -bottom-20 -left-20 h-64 w-64 rounded-full bg-white/[0.02]" />

        <div className="relative flex flex-wrap items-end justify-between gap-6">
          <div>
            <div className="flex items-center gap-2">
              <Home size={16} className="text-emerald-300" />
              <p className="text-xs font-bold uppercase tracking-[0.2em] text-emerald-300">
                Resident Hub
              </p>
            </div>
            <h1 className="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">
              Welcome home, {data.tenant.name}
            </h1>
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-emerald-100/80">
              <span className="flex items-center gap-1.5">
                <span className="font-mono font-semibold text-white">
                  {data.tenant.unit_code}
                </span>
                <span>at {data.tenant.property_name}</span>
              </span>
              {data.tenant.move_in_date && (
                <span className="flex items-center gap-1.5">
                  <CalendarDays size={14} className="text-emerald-400" />
                  Move-in: {formatDate(data.tenant.move_in_date)}
                </span>
              )}
            </div>
          </div>

          <button
            type="button"
            onClick={() => void loadDashboard()}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-xl border border-white/20 bg-white/10 px-4 py-2.5 text-sm font-semibold text-white backdrop-blur-sm transition hover:bg-white/15 disabled:opacity-50"
          >
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </header>

      {loadError && <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{loadError}</p>}

      {/* ============================================================ */}
      {/*  Pay Rent & Account Overview (Unified M-Pesa Payment Section) */}
      {/* ============================================================ */}
      <section id="pay-rent" aria-label="Pay Rent" className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-6 p-6 sm:p-7 border-b border-slate-100 bg-gradient-to-r from-emerald-50/60 via-slate-50/30 to-white">
          <div>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-100 px-3 py-1 text-xs font-bold text-emerald-800">
              <Smartphone size={13} className="text-emerald-700" />
              Rent Payment Hub
            </span>
            <p className="mt-2 text-sm font-medium text-slate-500">Current Balance Due</p>
            <p className={`mt-1 text-3xl sm:text-4xl font-extrabold tracking-tight ${indicator.color}`}>
              {currency.format(balance)}
            </p>
            <div className="mt-2 flex items-center gap-2">
              <span
                className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-bold ${
                  netBalance <= 0
                    ? 'bg-emerald-100 text-emerald-800'
                    : netBalance < 15000
                      ? 'bg-amber-100 text-amber-800'
                      : 'bg-red-100 text-red-800'
                }`}
              >
                <span className={`h-1.5 w-1.5 rounded-full ${indicator.bg}`} />
                {indicator.label}
              </span>
              <span className="text-xs text-slate-500">
                Next due date: <strong className="text-slate-700">{formatDate(data.summary.next_due_date)}</strong>
              </span>
            </div>
            <p className="mt-2 text-xs text-slate-600">
              Available rent credit:{" "}
              <strong className="text-emerald-700">{currency.format(creditBalance)}</strong>
              <span className="mx-1.5">·</span>
              Balance after credit:{" "}
              <strong className="text-slate-800">{currency.format(netBalance)}</strong>
            </p>
          </div>

          <div className="flex flex-col sm:items-end gap-2">
            <button
              type="button"
              onClick={() => {
                if (firstOutstandingInvoice) {
                  handleOpenPayModal(
                    firstOutstandingInvoice.id,
                    Math.max(
                      Number(firstOutstandingInvoice.balance_due) - creditBalance,
                      0,
                    ),
                  );
                }
              }}
              disabled={!firstOutstandingInvoice}
              className="inline-flex items-center justify-center gap-2.5 rounded-xl bg-emerald-600 px-6 py-3.5 text-sm font-bold text-white shadow-md transition hover:bg-emerald-700 active:scale-[0.99] focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:ring-offset-2 disabled:opacity-50"
            >
              <Smartphone size={18} />
              <span>Pay Rent</span>
            </button>
            <p className="text-[11px] text-slate-500">
              Choose a full or partial amount and enter the Safaricom number
            </p>
          </div>
        </div>

        {/* How to Pay Rent via M-Pesa (Process Flow without raw payment details) */}
        <div className="bg-slate-50/50 p-6 sm:p-7">
          <div className="flex items-center gap-2 text-slate-900 font-bold text-sm mb-4">
            <CheckCircle2 size={18} className="text-emerald-600" />
            <h3 className="text-sm font-bold text-slate-900">How to Pay Rent via M-Pesa</h3>
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <div className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-2xs">
              <div className="flex items-center gap-2 text-emerald-800 font-bold text-xs uppercase tracking-wider">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-100 text-emerald-800 text-xs font-black">1</span>
                <span>Click Pay Rent</span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-slate-600">
                Click <strong>Pay Rent</strong> to pay the latest outstanding invoice after rent credit. You can pay the full balance, a partial amount, or extra; excess becomes credit.
              </p>
            </div>

            <div className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-2xs">
              <div className="flex items-center gap-2 text-emerald-800 font-bold text-xs uppercase tracking-wider">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-100 text-emerald-800 text-xs font-black">2</span>
                <span>Enter Your Phone Number</span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-slate-600">
                Enter your Safaricom phone number to receive the prompt. You can use your profile number or enter any other Safaricom line.
              </p>
            </div>

            <div className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-2xs">
              <div className="flex items-center gap-2 text-emerald-800 font-bold text-xs uppercase tracking-wider">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-100 text-emerald-800 text-xs font-black">3</span>
                <span>Enter PIN & Confirm</span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-slate-600">
                An M-Pesa PIN prompt appears on your phone handset. Enter your secret M-Pesa PIN to complete payment. Your ledger updates automatically.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  4. Recent Invoices                                          */}
      {/* ============================================================ */}
      <section aria-labelledby="invoices-heading" className="space-y-4">
        <div>
          <h2 id="invoices-heading" className="text-xl font-bold text-slate-900">
            Recent Invoices
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Your issued invoices with charge breakdowns and payment status.
          </p>
        </div>

        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50">
                  <th className="px-5 py-3 font-semibold text-slate-600">Period</th>
                  <th className="px-5 py-3 font-semibold text-slate-600">Invoice #</th>
                  <th className="px-5 py-3 font-semibold text-slate-600">Total</th>
                  <th className="px-5 py-3 font-semibold text-slate-600">Waiver</th>
                  <th className="px-5 py-3 font-semibold text-slate-600">Status</th>
                  <th className="px-5 py-3 font-semibold text-slate-600">Line Items</th>
                  <th className="px-5 py-3 font-semibold text-slate-600">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.invoices.map((inv) => (
                  <tr key={inv.id} className="hover:bg-slate-50/50">
                    <td className="px-5 py-4 font-medium text-slate-900">
                      {formatDate(inv.period_start)} – {formatDate(inv.period_end)}
                    </td>
                    <td className="px-5 py-4 font-mono text-xs text-slate-600">{inv.number}</td>
                    <td className="px-5 py-4 font-semibold text-slate-900">
                      {currency.format(Number(inv.total))}
                    </td>
                    <td className="px-5 py-4 text-xs text-slate-600">
                      {Number(inv.waived_amount) > 0 ? (
                        <div>
                          <span className="font-semibold">{currency.format(Number(inv.waived_amount))}</span>
                          {inv.approved_waivers?.map((waiver, index) => (
                            <p key={`${inv.id}-waiver-${index}`} className="mt-1 max-w-xs">
                              {waiver.reason}
                            </p>
                          ))}
                        </div>
                      ) : '—'}
                    </td>
                    <td className="px-5 py-4">
                      <span
                        className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-bold ${statusBadge(inv.status)}`}
                      >
                        {statusLabel(inv.status)}
                      </span>
                    </td>
                    <td className="px-5 py-4 text-xs text-slate-500">
                      {inv.lines.map((l) => l.description).join(', ')}
                    </td>
                    <td className="px-5 py-4">
                      {inv.status !== 'settled' && Number(inv.balance_due) > 0 ? (
                        <button
                          type="button"
                          onClick={() =>
                            handleOpenPayModal(
                              inv.id,
                              Math.max(Number(inv.balance_due) - creditBalance, 0),
                            )
                          }
                          className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white shadow-xs transition hover:bg-emerald-700 active:bg-emerald-800"
                        >
                          <Smartphone size={13} />
                          Pay Rent
                        </button>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600">
                          <CheckCircle2 size={13} />
                          Paid
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/*  5. Payment History                                          */}
      {/* ============================================================ */}
      <section aria-labelledby="payments-heading" className="space-y-4">
        <div>
          <h2 id="payments-heading" className="text-xl font-bold text-slate-900">
            Payment History
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            M-Pesa payments recorded against your account with receipt references.
          </p>
        </div>

        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50">
                  <th className="px-5 py-3 font-semibold text-slate-600">Date</th>
                  <th className="px-5 py-3 font-semibold text-slate-600">Amount</th>
                  <th className="px-5 py-3 font-semibold text-slate-600">Method</th>
                  <th className="px-5 py-3 font-semibold text-slate-600">M-Pesa Ref</th>
                  <th className="px-5 py-3 font-semibold text-slate-600">Receipt</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.payments.map((pmt) => (
                  <tr key={pmt.id} className="hover:bg-slate-50/50">
                    <td className="px-5 py-4 text-slate-900">{formatDate(pmt.received_at)}</td>
                    <td className="px-5 py-4 font-semibold text-slate-900">
                      {currency.format(Number(pmt.amount))}
                    </td>
                    <td className="px-5 py-4">
                      <span className="inline-flex items-center gap-1.5 text-emerald-700">
                        <Smartphone size={14} />
                        {pmt.source.replaceAll('_', ' ')}
                      </span>
                    </td>
                    <td className="px-5 py-4 font-mono text-xs text-slate-600">{pmt.reference}</td>
                    <td className="px-5 py-4">
                      {pmt.receipt ? (
                        <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-700">
                          <Receipt size={14} />
                          {pmt.receipt.number}
                        </span>
                      ) : (
                        <span className="text-xs text-slate-400">Pending</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section aria-labelledby="deposit-heading" className="space-y-4">
        <div>
          <h2 id="deposit-heading" className="text-xl font-bold text-slate-900">Deposit ledger</h2>
          <p className="mt-1 text-sm text-slate-500">Recorded deposit movements for your active tenancy.</p>
        </div>
        {!data.deposit ? (
          <p className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-500">No deposit account is linked to your active tenancy.</p>
        ) : (
          <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="grid gap-4 border-b border-slate-100 p-5 sm:grid-cols-2">
              <div><p className="text-xs font-semibold uppercase text-slate-500">Current deposit balance</p><p className="mt-1 text-2xl font-bold text-slate-900">{currency.format(Number(data.deposit.balance))}</p></div>
              <div><p className="text-xs font-semibold uppercase text-slate-500">Target deposit</p><p className="mt-1 text-2xl font-bold text-slate-900">{currency.format(Number(data.deposit.target_amount))}</p></div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Date</th><th className="px-5 py-3">Movement</th><th className="px-5 py-3">Reference</th><th className="px-5 py-3">Amount</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {data.deposit.entries.map((entry) => <tr key={entry.id}>
                    <td className="px-5 py-3">{formatDate(entry.created_at)}</td>
                    <td className="px-5 py-3">{entry.description} · {entry.entry_type.replaceAll('_', ' ')}</td>
                    <td className="px-5 py-3 font-mono text-xs">{entry.reference}</td>
                    <td className="px-5 py-3 font-semibold">{currency.format(Number(entry.amount))}</td>
                  </tr>)}
                  {data.deposit.entries.length === 0 && <tr><td colSpan={4} className="px-5 py-5 text-center text-slate-500">No deposit movements recorded.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>

      {data.move_out_settlements.length > 0 && (
        <section aria-labelledby="refunds-heading" className="space-y-4">
          <div>
            <h2 id="refunds-heading" className="text-xl font-bold text-slate-900">Move-out settlement tracker</h2>
            <p className="mt-1 text-sm text-slate-500">Follow each unit&apos;s inspection, deposit reconciliation, approval, and payout progress.</p>
          </div>
          <div className="space-y-4">
            {data.move_out_settlements.map((settlement) => {
              const milestones = [
                ['Notice recorded', true],
                ['Exit inspection', Boolean(settlement.inspection_completed_at)],
                ['Manager approval', settlement.status === 'approved' || settlement.status === 'paid'],
                ['Payout recorded', settlement.status === 'paid'],
              ] as const;
              return (
                <article key={settlement.id} className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                  <header className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h3 className="font-semibold text-slate-900">{settlement.property_name} · {settlement.unit_code}</h3>
                      <p className="mt-1 text-sm text-slate-500">Moved out {formatDate(settlement.actual_move_out_date)} · {settlement.status.replaceAll('_', ' ')}</p>
                    </div>
                    <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-800">{settlement.maintenance_required ? 'Under maintenance' : 'Vacancy processing'}</span>
                  </header>
                  <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                    {milestones.map(([label, complete]) => (
                      <li key={label} className={`rounded-lg p-3 text-sm ${complete ? 'bg-emerald-50 text-emerald-800' : 'bg-slate-50 text-slate-500'}`}>
                        <span className="block text-xs font-semibold uppercase">{complete ? 'Complete' : 'Pending'}</span>
                        <span className="mt-1 block font-medium">{label}</span>
                      </li>
                    ))}
                  </ol>
                  <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                    <div><dt className="text-slate-500">Deposit held</dt><dd className="font-semibold">{currency.format(Number(settlement.deposit_balance))}</dd></div>
                    <div><dt className="text-slate-500">Arrears</dt><dd className="font-semibold">{currency.format(Number(settlement.arrears_amount))}</dd></div>
                    <div><dt className="text-slate-500">Final utilities</dt><dd className="font-semibold">{currency.format(Number(settlement.utility_amount))}</dd></div>
                    <div><dt className="text-slate-500">Damage deductions</dt><dd className="font-semibold">{currency.format(Number(settlement.damages_amount))}</dd></div>
                    <div><dt className="text-slate-500">Deducted from deposit</dt><dd className="font-semibold">{currency.format(Number(settlement.deduction_from_deposit))}</dd></div>
                    <div><dt className="text-slate-500">Refund due</dt><dd className="font-semibold text-emerald-700">{currency.format(Number(settlement.refund_amount))}</dd></div>
                    <div><dt className="text-slate-500">Outstanding shortfall</dt><dd className="font-semibold text-red-700">{currency.format(Number(settlement.shortfall_amount))}</dd></div>
                    <div><dt className="text-slate-500">Payout reference</dt><dd className="font-mono text-xs">{settlement.payout_reference || '—'}</dd></div>
                  </dl>
                  {settlement.damage_items.length > 0 && (
                    <div>
                      <h4 className="text-sm font-semibold text-slate-800">Itemized damage findings</h4>
                      <ul className="mt-2 space-y-1 text-sm text-slate-600">
                        {settlement.damage_items.map((item, index) => <li key={`${item.description}-${index}`} className="flex justify-between gap-3"><span>{item.description}</span><span>{currency.format(Number(item.amount))}</span></li>)}
                      </ul>
                    </div>
                  )}
                  {settlement.final_readings.length > 0 && (
                    <div>
                      <h4 className="text-sm font-semibold text-slate-800">Final utility readings</h4>
                      <ul className="mt-2 space-y-1 text-sm text-slate-600">
                        {settlement.final_readings.map((reading) => <li key={reading.utility_type}>{reading.name}: {reading.current_value} · {currency.format(Number(reading.amount))}</li>)}
                      </ul>
                    </div>
                  )}
                  {settlement.inspection_photos.length > 0 && (
                    <div>
                      <h4 className="text-sm font-semibold text-slate-800">Exit inspection photos</h4>
                      <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                        {settlement.inspection_photos.map((photo) => (
                          <a key={photo.index} href={`/api/tenant/move-out-settlements/${settlement.id}/photos/${photo.index}`} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-200 p-2 text-sm text-emerald-800 hover:bg-emerald-50">
                            {photo.caption}
                          </a>
                        ))}
                      </div>
                    </div>
                  )}
                  {Number(settlement.shortfall_amount) > 0 && (
                    <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
                      A remaining balance of {currency.format(Number(settlement.shortfall_amount))} is due. {settlement.paybill_number ? `Pay via Paybill ${settlement.paybill_number}` : 'Contact the Property Officer for the property Paybill'} using unit {settlement.unit_code} as the account reference, or contact the Property Officer to arrange debt clearance.
                    </p>
                  )}
                  {settlement.rejection_reason && <p className="text-sm text-red-700">Settlement review note: {settlement.rejection_reason}</p>}
                </article>
              );
            })}
          </div>
        </section>
      )}

      {/* ============================================================ */}
      {/*  6. Move-Out Notice Section                                  */}
      {/* ============================================================ */}
      <section
        id="move-out-notice"
        aria-labelledby="move-out-heading"
        className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"
      >
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <ClipboardList size={20} className="text-slate-400" />
              <h2 id="move-out-heading" className="text-xl font-bold text-slate-900">
                Move-Out Notice
              </h2>
            </div>
            <p className="mt-1 text-sm text-slate-500">
              Notice period:{' '}
              <span className="font-semibold text-slate-700">
                {selectedMoveOutTenancy?.notice_period_days ?? 'Unavailable'}{selectedMoveOutTenancy ? ' days' : ''}
              </span>
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
              {selectedMoveOutTenancy?.notice?.status || (selectedMoveOutTenancy ? 'No active notice' : 'No active tenancy data')}
            </span>
          </div>
        </div>

        <div className="mt-5 rounded-xl border border-slate-100 bg-slate-50 p-4">
          {moveOutContext && moveOutContext.tenancies.length > 1 && (
            <label className="mb-4 block text-sm font-medium text-slate-700">Tenancy
              <select value={selectedMoveOutTenancyId ?? moveOutContext.tenancy_id} onChange={(event) => {
                setSelectedMoveOutTenancyId(Number(event.target.value));
                setMoveOutDate('');
                setMoveOutReason('');
              }} className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2">
                {moveOutContext.tenancies.map((tenancy) => <option key={tenancy.id} value={tenancy.id}>{tenancy.property_name} · {tenancy.unit_code}</option>)}
              </select>
            </label>
          )}
          <p className="text-sm text-slate-600">
            {selectedMoveOutTenancy?.notice
              ? `Your move-out notice for ${selectedMoveOutTenancy.property_name} / ${selectedMoveOutTenancy.unit_code} is ${selectedMoveOutTenancy.notice.status} for ${formatDate(selectedMoveOutTenancy.notice.proposed_move_out_date)}.`
              : selectedMoveOutTenancy
                ? <>You currently have no active move-out notice for {selectedMoveOutTenancy.property_name} / {selectedMoveOutTenancy.unit_code}. Submit at least <strong>{selectedMoveOutTenancy.notice_period_days} days</strong> before your intended move-out date.</>
                : 'Move-out notice information is unavailable because no active tenancy was returned.'}
          </p>
          {canSubmitMoveOutNotice && (
            <form onSubmit={submitMoveOutNotice} className="mt-4 grid gap-3 sm:grid-cols-[1fr_2fr_auto]">
              <label className="text-xs font-semibold text-slate-700">Proposed move-out date
                <input required type="date" min={earliestMoveOutDate} value={moveOutDate} onChange={(event) => setMoveOutDate(event.target.value)} className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              </label>
              <label className="text-xs font-semibold text-slate-700">Reason (optional)
                <input value={moveOutReason} onChange={(event) => setMoveOutReason(event.target.value)} className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              </label>
              <button type="submit" disabled={submittingNotice || !moveOutDate} className="self-end rounded-lg bg-emerald-800 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">{submittingNotice ? 'Submitting…' : 'Submit notice'}</button>
            </form>
          )}
        </div>
      </section>

      {/* ============================================================ */}
      {/*  7. Announcements                                            */}
      {/* ============================================================ */}
      {announcements.length > 0 && (
        <section aria-labelledby="announcements-heading" className="space-y-3">
          <div className="flex items-center gap-2">
            <Megaphone size={20} className="text-blue-500" />
            <h2 id="announcements-heading" className="text-xl font-bold text-slate-900">
              Announcements
            </h2>
          </div>
          {announcements.map((ann) => (
            <article
              key={ann.id}
              className="rounded-2xl border border-blue-200 bg-gradient-to-br from-blue-50 to-indigo-50/40 p-5 shadow-sm"
            >
              <h3 className="font-bold text-blue-950">{ann.title}</h3>
              <p className="mt-2 whitespace-pre-wrap text-sm text-blue-900">{ann.body}</p>
              {ann.expires_at && (
                <p className="mt-3 text-xs text-blue-600">
                  Notice expires: {formatDate(ann.expires_at)}
                </p>
              )}
            </article>
          ))}
        </section>
      )}

      {/* ============================================================ */}
      {/*  8. Quick Links                                              */}
      {/* ============================================================ */}
      <section aria-label="Quick links" className="grid gap-4 sm:grid-cols-2">
        <Link
          href="/tenant/maintenance"
          className="group flex items-center gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-emerald-300 hover:shadow-md"
        >
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-emerald-100 text-emerald-700 transition group-hover:bg-emerald-600 group-hover:text-white">
            <Wrench size={22} />
          </div>
          <div className="flex-1">
            <h3 className="font-bold text-slate-900">Maintenance Requests</h3>
            <p className="mt-0.5 text-sm text-slate-500">Report and track repair requests</p>
          </div>
          <ChevronRight size={20} className="text-slate-300 transition group-hover:text-emerald-500" />
        </Link>

        <Link
          href="/tenant/profile"
          className="group flex items-center gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-emerald-300 hover:shadow-md"
        >
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-emerald-100 text-emerald-700 transition group-hover:bg-emerald-600 group-hover:text-white">
            <UserCircle size={22} />
          </div>
          <div className="flex-1">
            <h3 className="font-bold text-slate-900">Profile</h3>
            <p className="mt-0.5 text-sm text-slate-500">Update your contact details</p>
          </div>
          <ChevronRight size={20} className="text-slate-300 transition group-hover:text-emerald-500" />
        </Link>
      </section>

      {/* ============================================================ */}
      {/*  9. M-Pesa STK Push Payment Modal                            */}
      {/* ============================================================ */}
      {payModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="relative w-full max-w-lg overflow-hidden rounded-2xl bg-white shadow-2xl transition-all">
            {/* Modal Header */}
            <div className="flex items-center justify-between border-b border-slate-100 bg-gradient-to-r from-emerald-800 to-teal-900 px-6 py-4 text-white">
              <div className="flex items-center gap-2.5">
                <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-500/30 text-emerald-300 ring-1 ring-emerald-400/40">
                  <Smartphone size={20} />
                </div>
                <div>
                  <h3 className="font-bold text-lg leading-tight">Pay Rent with M-Pesa</h3>
                  <p className="text-xs text-emerald-200">An M-Pesa PIN prompt will be sent directly to your phone</p>
                </div>
              </div>
              <button
                type="button"
                onClick={handleClosePayModal}
                disabled={payStatus === 'initiating'}
                className="rounded-lg p-1.5 text-emerald-200 transition hover:bg-white/10 hover:text-white"
              >
                <X size={20} />
              </button>
            </div>

            {/* Modal Body */}
            <div className="p-6">
              {payStatus === 'success' ? (
                <div className="flex flex-col items-center py-4 text-center">
                  <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-emerald-100 text-emerald-600">
                    <CheckCircle2 size={36} />
                  </div>
                  <h4 className="text-xl font-bold text-slate-900">Payment Confirmed!</h4>
                  <p className="mt-2 text-sm text-slate-600 max-w-sm">
                    {payMessage ||
                      'Your M-Pesa payment was successfully processed and allocated to your rent account.'}
                  </p>
                  <div className="mt-4 rounded-xl bg-emerald-50 border border-emerald-100 px-4 py-2.5 text-xs text-emerald-800">
                    An SMS confirmation has been queued and your ledger has been updated.
                  </div>
                  <button
                    type="button"
                    onClick={handleClosePayModal}
                    className="mt-6 w-full rounded-xl bg-emerald-600 py-3 text-sm font-bold text-white shadow-sm transition hover:bg-emerald-700"
                  >
                    Done
                  </button>
                </div>
              ) : payStatus === 'pending' ? (
                <div className="flex flex-col items-center py-4 text-center">
                  <div className="relative mb-5 flex h-20 w-20 items-center justify-center rounded-full bg-emerald-100 text-emerald-600">
                    <Smartphone size={38} className="animate-pulse" />
                    <span className="absolute -top-1 -right-1 flex h-6 w-6 items-center justify-center rounded-full bg-emerald-600 text-white text-xs">
                      <Loader2 size={14} className="animate-spin" />
                    </span>
                  </div>
                  <h4 className="text-xl font-bold text-slate-900">M-Pesa Prompt Sent!</h4>
                  <p className="mt-2 text-sm text-slate-600 max-w-sm">
                    Check your phone screen (<strong>{payPhone}</strong>) and enter your M-Pesa PIN to complete payment of{' '}
                    <strong>{currency.format(Number(payAmount) || 0)}</strong>.
                  </p>
                  <div className="mt-5 flex items-center justify-center gap-2 text-xs text-slate-500">
                    <Loader2 size={16} className="animate-spin text-emerald-600" />
                    <span>Waiting for your PIN confirmation on Safaricom M-Pesa...</span>
                  </div>
                  <button
                    type="button"
                    onClick={handleClosePayModal}
                    className="mt-6 text-xs font-semibold text-slate-500 underline hover:text-slate-800"
                  >
                    Dismiss (payment will update automatically in background)
                  </button>
                </div>
              ) : (
                <form onSubmit={handleInitiatePay} className="space-y-4">
                  {payMessage && payStatus === 'failed' && (
                    <div className="flex items-start gap-2.5 rounded-xl border border-red-200 bg-red-50 p-3 text-xs text-red-800">
                      <AlertCircle size={16} className="mt-0.5 shrink-0 text-red-600" />
                      <span>{payMessage}</span>
                    </div>
                  )}

                  {payInvoiceId ? (
                    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3 text-xs text-slate-600">
                      <p>
                        Paying for Invoice #{data.invoices.find((i) => i.id === payInvoiceId)?.number || payInvoiceId}
                      </p>
                      <p className="mt-1">
                        Available rent credit: <strong>{currency.format(creditBalance)}</strong>
                      </p>
                    </div>
                  ) : (
                    <div className="rounded-xl border border-emerald-100 bg-emerald-50/60 p-3 text-xs text-emerald-900 flex items-center justify-between">
                      <span>
                        Account Balance: <strong>{currency.format(balance)}</strong>
                      </span>
                      <span className="text-[10px] uppercase font-bold tracking-wider text-emerald-700 bg-white/80 px-2 py-0.5 rounded-md border border-emerald-200">
                        Unit: {data.tenant.unit_code}
                      </span>
                    </div>
                  )}

                  <div>
                    <label
                      htmlFor="pay-phone"
                      className="block text-xs font-bold uppercase tracking-wider text-slate-600 mb-1"
                    >
                      Safaricom Phone Number
                    </label>
                    <div className="relative">
                      <input
                        id="pay-phone"
                        type="tel"
                        required={Number(payAmount) > 0}
                        placeholder="0712345678"
                        value={payPhone}
                        onChange={(e) => setPayPhone(e.target.value)}
                        className="w-full rounded-xl border border-slate-300 px-4 py-2.5 font-mono text-sm text-slate-900 placeholder:text-slate-400 focus:border-emerald-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
                      />
                    </div>
                    <p className="mt-1 text-[11px] text-slate-500">
                      Enter the Safaricom line that will receive the M-Pesa PIN prompt (e.g. 07XXXXXXXX or 2547XXXXXXXX).
                    </p>
                  </div>

                  <div>
                    <div className="mb-1 flex items-center justify-between gap-3">
                      <label
                        htmlFor="pay-amount"
                        className="block text-xs font-bold uppercase tracking-wider text-slate-600 mb-1"
                      >
                        Amount to Pay (KES)
                      </label>
                      <button
                        type="button"
                        onClick={() => {
                          const selectedDue = payInvoiceId
                            ? Number(
                                data.invoices.find(
                                  (invoice) => invoice.id === payInvoiceId,
                                )?.balance_due ?? 0,
                              )
                            : balance;
                          setPayAmount(
                            String(Math.max(selectedDue - creditBalance, 0)),
                          );
                        }}
                        className="text-[11px] font-semibold text-emerald-700 underline"
                      >
                        Use full amount
                      </button>
                    </div>
                    <div className="relative">
                      <span className="absolute left-3.5 top-2.5 font-bold text-sm text-slate-400">KES</span>
                      <input
                        id="pay-amount"
                        type="number"
                        min="0"
                        step="1"
                        required
                        placeholder="e.g. 25000"
                        value={payAmount}
                        onChange={(e) => setPayAmount(e.target.value)}
                        className="w-full rounded-xl border border-slate-300 pl-14 pr-4 py-2.5 font-mono text-sm font-bold text-slate-900 placeholder:text-slate-400 focus:border-emerald-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
                      />
                    </div>
                    <p className="mt-1 text-[11px] text-slate-500">
                      Available rent credit applies to this invoice. Enter a partial or full payment; any excess after the invoice is settled stays as credit.
                    </p>
                  </div>

                  <div className="pt-2">
                    <button
                      type="submit"
                      disabled={payStatus === 'initiating'}
                      className="flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-600 py-3 text-sm font-bold text-white shadow-md transition hover:bg-emerald-700 active:bg-emerald-800 disabled:opacity-50"
                    >
                      {payStatus === 'initiating' ? (
                        <>
                          <Loader2 size={18} className="animate-spin" />
                          <span>Sending Prompt...</span>
                        </>
                      ) : (
                        <>
                          <Smartphone size={18} />
                          <span>Send M-Pesa PIN Prompt</span>
                        </>
                      )}
                    </button>
                    <p className="mt-2 text-center text-[11px] text-slate-400">
                      Secure payment via Safaricom Lipa na M-Pesa.
                    </p>
                  </div>
                </form>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
