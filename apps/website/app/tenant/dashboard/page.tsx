// app/tenant/dashboard/page.tsx
'use client';

import { useCallback, useEffect, useState } from 'react';
import { CreditCard, Download, Bell, CheckCircle2, Pencil, Save, X, PlayCircle } from 'lucide-react';

type Invoice = {
  id: string;
  month: string;
  baseRent: number;
  garbage: number;
  recurringCharges: { name: string; amount: number }[];
  waterUnits: number;
  waterRate: number;
  waterTotal: number;
  priorArrears: number;
  totalDue: number;
  amountPaid: number;
  balance: number;
  status: string;
  dueDate: string;
  publishedAt: string;
};

type PaymentRecord = {
  id: string;
  date: string;
  month: string;
  amount: number;
  reference: string;
  method: string;
  category: string;
  invoiceMonth: string | null;
};

type MoveInSettlement = { id: string; status: string; totalRequired: number; totalPaid: number; outstandingBalance: number; deposit: number; rent: number; garbage: number; waterDeposit: number };
type TransitionArrears = {
  assignmentId: string;
  unitCode: string | null;
  balance: number;
  payments: { id: string; date: string; amount: number; reference: string; method: string }[];
};

type TenantProfile = {
  fullName: string;
  email: string;
  phone: string;
  deposit: number;
  moveInDate: string | null;
  unitCode: string | null;
  floor: string | null;
};

type TenantAnnouncement = {
  id: string;
  title: string;
  message: string;
  expires_at: string;
};

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

function daysUntilDeadline(dueDate: string): number | null {
  const [year, month, day] = dueDate.split('-').map(Number);
  if (!year || !month || !day) return null;
  const today = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Nairobi',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const todayParts = Object.fromEntries(today.map((part) => [part.type, part.value]));
  const todayUtc = Date.UTC(Number(todayParts.year), Number(todayParts.month) - 1, Number(todayParts.day));
  const dueUtc = Date.UTC(year, month - 1, day);
  return Math.floor((dueUtc - todayUtc) / 86_400_000);
}

function PaymentHistory({ payments }: { payments: PaymentRecord[] }) {
  const grouped = payments.reduce<Record<string, PaymentRecord[]>>((groups, payment) => {
    const month = payment.month || payment.date.slice(0, 7) || 'Other';
    groups[month] = [...(groups[month] || []), payment];
    return groups;
  }, {});

  return (
    <section className="bg-white rounded-xl shadow-sm border border-slate-200">
      <div className="p-5 border-b border-slate-200"><h3 className="font-bold text-slate-900">Payment History</h3></div>
      {Object.keys(grouped).length ? Object.entries(grouped).map(([month, monthPayments]) => (
        <div key={month} className="border-b border-slate-100 last:border-b-0">
          <h4 className="px-5 py-2 bg-slate-50 text-xs font-bold uppercase text-slate-500">{month}</h4>
          <div className="divide-y divide-slate-100">
            {monthPayments.map((payment) => <div key={`${payment.category}-${payment.id}`} className="px-5 py-3 flex items-center justify-between gap-4 text-sm">
              <div><p className="font-medium text-slate-900">{payment.category}{payment.invoiceMonth ? ` · ${payment.invoiceMonth}` : ''}</p><p className="text-xs text-slate-500">{new Date(payment.date).toLocaleString()} · {payment.method.replace(/_/g, ' ')}{payment.reference ? ` · ${payment.reference}` : ''}</p></div>
              <strong className="text-emerald-700">{currency.format(payment.amount)}</strong>
            </div>)}
          </div>
        </div>
      )) : <p className="p-5 text-sm text-slate-500">No payments recorded yet.</p>}
    </section>
  );
}

export default function TenantDashboard() {
  const [currentInvoice, setCurrentInvoice] = useState<Invoice | null>(null);
  const [history, setHistory] = useState<Invoice[]>([]);
  const [payments, setPayments] = useState<PaymentRecord[]>([]);
  const [tenantPhone, setTenantPhone] = useState<string | null>(null);
  const [profile, setProfile] = useState<TenantProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [isPreLive, setIsPreLive] = useState(false);
  const [isPaying, setIsPaying] = useState(false);
  const [transitionArrears, setTransitionArrears] = useState<TransitionArrears | null>(null);
  const [paymentMessage, setPaymentMessage] = useState('');
  const [moveInSettlement, setMoveInSettlement] = useState<MoveInSettlement | null>(null);
  const [transitionSettings, setTransitionSettings] = useState<any>(null);
  const [announcements, setAnnouncements] = useState<TenantAnnouncement[]>([]);

  const loadInvoices = useCallback(async () => {
      try {
        const response = await fetch('/api/tenant/invoices');
        const payload = await response.json();

        if (!response.ok) {
          throw new Error(payload.error || 'Unable to load invoices');
        }

        setCurrentInvoice(payload.current);
        setHistory(payload.history || []);
        setIsPreLive(payload.isPreLive || false);
        setTransitionArrears(payload.transitionArrears || null);
        setTenantPhone(payload.tenantPhone || null);
        setMoveInSettlement(payload.moveInSettlement || null);
        setTransitionSettings(payload.transitionSettings || null);
        setPayments(payload.paymentHistory || []);
      } catch (error) {
        console.error('Failed to load invoices:', error);
      } finally {
        setLoading(false);
      }
  }, []);

  useEffect(() => {
    loadInvoices();
  }, [loadInvoices]);

  useEffect(() => {
    const loadAnnouncements = async () => {
      try {
        const response = await fetch('/api/announcements');
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || 'Unable to load announcements.');
        setAnnouncements(payload.announcements || []);
      } catch (error) {
        console.error('Failed to load tenant announcements:', error);
      }
    };
    loadAnnouncements();
  }, []);

  useEffect(() => {
    if (!isPreLive && !(moveInSettlement && moveInSettlement.outstandingBalance > 0) && !(currentInvoice && currentInvoice.balance > 0)) return;
    const interval = setInterval(loadInvoices, 12000);
    return () => clearInterval(interval);
  }, [isPreLive, moveInSettlement, currentInvoice, loadInvoices]);


  const handleMpesaPush = async (paymentTarget: 'invoice' | 'transition' | 'move-in') => {
    if (paymentTarget === 'invoice' && !currentInvoice) return;
    if (paymentTarget === 'transition' && !transitionArrears) return;
    if (paymentTarget === 'move-in' && !moveInSettlement) return;

    setIsPaying(true);
    setPaymentMessage('');
    try {
      const response = await fetch('/api/mpesa/stkpush', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone: tenantPhone,
          amount: paymentTarget === 'transition'
            ? transitionArrears!.balance
            : paymentTarget === 'move-in' ? moveInSettlement!.outstandingBalance : currentInvoice!.balance,
          ...(paymentTarget === 'transition'
            ? { transitionAssignmentId: transitionArrears!.assignmentId }
            : paymentTarget === 'move-in' ? { settlementId: moveInSettlement!.id } : { invoiceId: currentInvoice!.id }),
        }),
      });

      const data = await response.json();

      if (response.ok && data.success) {
        setPaymentMessage('M-Pesa request sent. Enter your PIN, then this balance will refresh automatically.');
      } else {
        setPaymentMessage(data.error?.message || data.error || 'Failed to initiate payment. Please try again.');
      }
    } catch (error) {
      console.error('M-Pesa push error:', error);
      setPaymentMessage('An error occurred while processing the payment request.');
    } finally {
      setIsPaying(false);
    }
  };

  if (loading) {
    return <div className="p-8 text-slate-500 animate-pulse">Loading dashboard...</div>;
  }



  const moveInDeadline = process.env.NEXT_PUBLIC_MOVE_IN_BALANCE_DEADLINE || 'the deadline';

  const moveInStatus = String(moveInSettlement?.status || '').toLowerCase();
  const hasOpenMoveInSettlement = Boolean(moveInSettlement && moveInSettlement.outstandingBalance > 0);
  const moveInSettlementComplete = Boolean(moveInSettlement && moveInSettlement.outstandingBalance <= 0);
  const dueDays = currentInvoice?.balance && currentInvoice.balance > 0
    ? daysUntilDeadline(currentInvoice.dueDate)
    : null;

  const deadlineNotice = dueDays === null ? null : dueDays < 0
    ? { text: `Overdue by ${Math.abs(dueDays)} day${Math.abs(dueDays) === 1 ? '' : 's'}`, tone: 'red' }
    : dueDays === 0
      ? { text: 'Payment is due today', tone: 'red' }
      : dueDays <= 3
        ? { text: `Payment due in ${dueDays} day${dueDays === 1 ? '' : 's'}`, tone: 'amber' }
        : dueDays <= 7
          ? { text: `Payment due in ${dueDays} days`, tone: 'yellow' }
          : { text: `Payment due in ${dueDays} days`, tone: 'blue' };

  if (hasOpenMoveInSettlement && moveInSettlement) {
    return (
      <div className="space-y-6">
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-3xl font-bold text-slate-900">Welcome to the Apartment!</h1>
            <p className="text-slate-500 mt-1">Your account is successfully activated.</p>
          </div>
          <button
            onClick={() => window.location.assign('?tour=true')}
            className="flex items-center gap-2 border border-emerald-200 bg-emerald-50 hover:bg-emerald-100 text-emerald-700 rounded-lg px-3 py-2 text-sm font-semibold transition-colors"
          >
            <PlayCircle size={16} /> Take Tour
          </button>
        </div>

        <div className="bg-emerald-50 border border-emerald-200 rounded-2xl p-6 sm:p-8 max-w-4xl">
          <h2 className="text-xl font-bold text-emerald-900 mb-4 flex items-center gap-2">
            <CheckCircle2 size={24} className="text-emerald-600" /> Move-In Settlement Details
          </h2>
          
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <div className="bg-white p-4 rounded-xl border border-emerald-100">
              <span className="block text-sm text-slate-500">Security Deposit</span>
              <strong className="text-lg text-slate-900">{currency.format(moveInSettlement.deposit)}</strong>
            </div>
            <div className="bg-white p-4 rounded-xl border border-emerald-100">
              <span className="block text-sm text-slate-500">Water Deposit</span>
              <strong className="text-lg text-slate-900">{currency.format(moveInSettlement.waterDeposit)}</strong>
            </div>
            <div className="bg-white p-4 rounded-xl border border-emerald-100">
              <span className="block text-sm text-slate-500">Garbage Fee</span>
              <strong className="text-lg text-slate-900">{currency.format(moveInSettlement.garbage)}</strong>
            </div>
            <div className="bg-white p-4 rounded-xl border border-emerald-100">
              <span className="block text-sm text-slate-500">Month's Rent</span>
              <strong className="text-lg text-slate-900">{currency.format(moveInSettlement.rent)}</strong>
            </div>
          </div>

          <div className="flex flex-col sm:flex-row sm:items-center justify-between bg-white rounded-xl p-5 border border-emerald-100 shadow-sm gap-4">
            <div>
              <span className="block text-sm text-slate-500">Total Required</span>
              <strong className="text-xl text-slate-900">{currency.format(moveInSettlement.totalRequired)}</strong>
            </div>
            <div>
              <span className="block text-sm text-slate-500">Amount Paid</span>
              <strong className="text-xl text-emerald-600">{currency.format(moveInSettlement.totalPaid)}</strong>
            </div>
            <div>
              <span className="block text-sm text-slate-500">Outstanding Balance</span>
              <strong className={`text-xl ${moveInSettlement.outstandingBalance > 0 ? 'text-red-600' : 'text-slate-900'}`}>
                {currency.format(moveInSettlement.outstandingBalance)}
              </strong>
            </div>
          </div>

          {moveInSettlement.outstandingBalance > 0 ? (
            <div className="mt-6 bg-red-50 border border-red-200 rounded-xl p-4 flex gap-3 items-start">
              <Bell className="text-red-600 shrink-0 mt-0.5" size={20} />
              <div>
                <h3 className="font-bold text-red-900">Balance Clearance Required</h3>
                <p className="text-red-800 mt-1">
                  You have an outstanding move-in balance of <strong>{currency.format(moveInSettlement.outstandingBalance)}</strong>. 
                  Please ensure this is cleared before <strong>{moveInDeadline}</strong>.
                </p>
              </div>
              <button onClick={() => handleMpesaPush('move-in')} disabled={isPaying} className="mt-3 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white font-bold px-4 py-2 rounded-lg flex items-center gap-2"><CreditCard size={17} />{isPaying ? 'Processing...' : 'Pay via M-Pesa'}</button>
              {paymentMessage && <p role="status" className="mt-2 text-sm text-slate-600">{paymentMessage}</p>}
            </div>
          ) : (
            <div className="mt-6 bg-emerald-100/50 border border-emerald-200 rounded-xl p-4">
              <p className="text-emerald-800 font-medium">Your move-in settlement is fully cleared. Your next monthly invoice will appear in the normal billing cycle.</p>
            </div>
          )}
        </div>
        <PaymentHistory payments={payments} />
      </div>
    );
  }

  if (isPreLive) {
    const paymentsTotal = transitionArrears?.payments.reduce((total, payment) => total + payment.amount, 0) || 0;
    return (
      <div className="space-y-6">

        <div>
          <h1 className="text-3xl font-bold text-slate-900">Welcome to your Portal!</h1>
          <p className="text-slate-500 mt-1">Your account is successfully activated.</p>
        </div>

        <div className="bg-white border border-slate-200 rounded-xl p-6 sm:p-8 max-w-4xl">
          <h2 className="text-xl font-bold text-slate-900">Transition balance</h2>
          <p className="text-sm text-slate-500 mt-1">Your remaining opening arrears will carry into your first monthly invoice.</p>
          <div className="mt-5 grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="border border-slate-200 rounded-lg p-4"><span className="text-sm text-slate-500">Opening arrears</span><p className="text-xl font-bold mt-1">{currency.format(paymentsTotal + Number(transitionArrears?.balance || 0))}</p></div>
            <div className="border border-slate-200 rounded-lg p-4"><span className="text-sm text-slate-500">Paid</span><p className="text-xl font-bold text-emerald-700 mt-1">{currency.format(paymentsTotal)}</p></div>
            <div className="border border-amber-200 bg-amber-50 rounded-lg p-4"><span className="text-sm text-amber-800">Remaining</span><p className="text-xl font-bold text-amber-900 mt-1">{currency.format(transitionArrears?.balance || 0)}</p></div>
          </div>
          {transitionArrears && transitionArrears.balance > 0 && (
            <button onClick={() => handleMpesaPush('transition')} disabled={isPaying} className="w-full sm:w-auto mt-5 bg-emerald-600 hover:bg-emerald-700 disabled:bg-emerald-400 text-white font-bold px-5 py-3 rounded-lg flex items-center justify-center gap-2">
              <CreditCard size={18} />{isPaying ? 'Processing...' : 'Pay arrears via M-Pesa'}
            </button>
          )}
          {paymentMessage && <p role="status" className="mt-3 text-sm text-slate-600">{paymentMessage}</p>}
          <div className="mt-6"><PaymentHistory payments={payments} /></div>
          <div className="mt-6 bg-blue-50 border border-blue-200 rounded-lg p-4">
            <h3 className="font-semibold text-blue-900">Manual payment instructions</h3>
            <p className="text-sm text-blue-800 mt-1">For payments made outside this portal, use the Paybill and account details provided by your landlord. Keep your M-Pesa reference for reconciliation.</p>
            {transitionSettings?.paybill_number && <p className="text-sm text-blue-900 mt-2"><strong>Paybill:</strong> {transitionSettings.paybill_number}{transitionSettings.account_number && <> · <strong>Account:</strong> {transitionSettings.account_number}</>}</p>}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 flex gap-4 items-start w-full mr-4">
          <Bell className="text-blue-600 shrink-0 mt-0.5" size={20} />
          <div>
            <h3 className="font-bold text-blue-900">Manual Payment Notice</h3>
            <p className="text-sm text-blue-800 mt-1">If you pay outside the portal, retain your payment reference and share it with your landlord.</p>
            {transitionSettings?.paybill_number && <p className="text-sm text-blue-900 mt-2"><strong>Paybill:</strong> {transitionSettings.paybill_number}{transitionSettings.account_number && <> · <strong>Account:</strong> {transitionSettings.account_number}</>}</p>}
          </div>
        </div>
        <button
          onClick={() => window.location.assign('?tour=true')}
          className="shrink-0 flex items-center gap-2 border border-emerald-200 bg-emerald-50 hover:bg-emerald-100 text-emerald-700 rounded-lg px-4 py-2 font-semibold transition-colors h-fit"
        >
          <PlayCircle size={18} /> Take Tour
        </button>
      </div>

      {moveInSettlementComplete && (
        <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4">
          <h2 className="font-bold text-emerald-900">Welcome. Your move-in settlement is complete.</h2>
          <p className="text-sm text-emerald-800 mt-1">Your rent, garbage, water, and any carried balance will appear here as a monthly invoice when it is released.</p>
        </div>
      )}

      {announcements.length > 0 && (
        <section aria-label="Property announcements" className="space-y-3">
          {announcements.map((announcement) => (
            <article key={announcement.id} className="rounded-xl border border-blue-200 bg-blue-50 p-5">
              <h2 className="font-bold text-blue-950">{announcement.title}</h2>
              <p className="mt-2 whitespace-pre-wrap text-sm text-blue-900">{announcement.message}</p>
              <p className="mt-3 text-xs text-blue-700">Notice expires {new Date(announcement.expires_at).toLocaleDateString('en-KE')}</p>
            </article>
          ))}
        </section>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
          <div className="p-6 border-b border-slate-200 flex justify-between items-center bg-slate-50">
            <div>
              <h2 className="text-xl font-bold text-slate-900">Current Invoice</h2>
              <p className="text-sm text-slate-500">{currentInvoice ? `Billing Cycle: ${currentInvoice.month}` : 'No active invoices'}</p>
            </div>
            {currentInvoice && (
              currentInvoice.status === 'PAID' ? (
                <span className="px-3 py-1 bg-emerald-100 text-emerald-700 font-bold rounded-full flex items-center gap-2">
                  <CheckCircle2 size={16} /> Paid in Full
                </span>
              ) : (
                <span className={`px-3 py-1 font-bold rounded-full ${
                  currentInvoice.status === 'PARTIAL' ? 'bg-amber-100 text-amber-700' : 'bg-red-100 text-red-700'
                }`}>
                  {currentInvoice.status === 'PARTIAL' ? 'Partially Paid' : 'Unpaid Balance'}
                </span>
              )
            )}
          </div>

          {currentInvoice ? (
            <div className="p-6 space-y-4">
              <div className={`rounded-lg border p-3 text-sm font-semibold ${
                deadlineNotice?.tone === 'red' ? 'border-red-200 bg-red-50 text-red-800'
                  : deadlineNotice?.tone === 'amber' ? 'border-amber-200 bg-amber-50 text-amber-800'
                    : deadlineNotice?.tone === 'yellow' ? 'border-yellow-200 bg-yellow-50 text-yellow-800'
                      : 'border-blue-200 bg-blue-50 text-blue-800'
              }`}>
                {currentInvoice.balance <= 0
                  ? <>Payment deadline: {new Date(`${currentInvoice.dueDate}T00:00:00`).toLocaleDateString('en-KE')}</>
                  : <>{deadlineNotice?.text || 'Payment deadline'} · Due by {new Date(`${currentInvoice.dueDate}T00:00:00`).toLocaleDateString('en-KE')}</>}
              </div>
              <div className="flex justify-between items-center py-2 border-b border-slate-100">
                <span className="text-slate-600">Base Rent</span>
                <span className="font-medium">{currency.format(currentInvoice.baseRent)}</span>
              </div>
              <div className="flex justify-between items-center py-2 border-b border-slate-100">
                <span className="text-slate-600">Garbage Collection</span>
                <span className="font-medium">{currency.format(currentInvoice.garbage)}</span>
              </div>
              {currentInvoice.recurringCharges.map((charge) => (
                <div key={charge.name} className="flex justify-between items-center py-2 border-b border-slate-100">
                  <span className="text-slate-600">{charge.name}</span>
                  <span className="font-medium">{currency.format(charge.amount)}</span>
                </div>
              ))}
              <div className="flex justify-between items-center py-2 border-b border-slate-100">
                <span className="text-slate-600">Water Consumption ({currentInvoice.waterUnits} units @ {currency.format(currentInvoice.waterRate)})</span>
                <span className="font-medium">{currency.format(currentInvoice.waterTotal)}</span>
              </div>
              {currentInvoice.priorArrears > 0 && (
                <div className="flex justify-between items-center py-2 border-b border-slate-100 text-red-600">
                  <span>Carried-Over Arrears</span>
                  <span className="font-medium">{currency.format(currentInvoice.priorArrears)}</span>
                </div>
              )}
              {currentInvoice.amountPaid > 0 && (
                <div className="flex justify-between items-center py-2 border-b border-slate-100 text-emerald-600">
                  <span>Amount Already Paid</span>
                  <span className="font-medium">{currency.format(currentInvoice.amountPaid)}</span>
                </div>
              )}

              <div className="flex justify-between items-center pt-4">
                <span className="text-lg font-bold text-slate-900">Total Amount Due</span>
                <span className={`text-2xl font-bold ${currentInvoice.balance > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                  {currency.format(currentInvoice.balance)}
                </span>
              </div>

              {currentInvoice.status !== 'PAID' && currentInvoice.balance > 0 && (
                <button
                  onClick={() => handleMpesaPush('invoice')}
                  disabled={isPaying}
                  className="w-full mt-6 bg-emerald-600 hover:bg-emerald-700 disabled:bg-emerald-400 text-white font-bold py-3 rounded-lg flex items-center justify-center gap-2 transition-colors"
                >
                  <CreditCard size={20} />
                  {isPaying ? 'Processing...' : 'Pay via M-Pesa'}
                </button>
              )}
            </div>
          ) : (
            <div className="p-6 text-slate-600">
              {moveInSettlementComplete
                ? 'Your move-in settlement is cleared. Your first monthly invoice will appear here when it is released.'
                : 'No active invoices at this moment. You will see your invoice here once it is generated by your landlord.'}
            </div>
          )}
        </div>

        <PaymentHistory payments={payments} />
      </div>

      {history.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-slate-200">
          <div className="p-6 border-b border-slate-200 bg-slate-50">
            <h3 className="font-bold text-slate-900">Past Invoices</h3>
          </div>
          <div className="divide-y divide-slate-100">
            {history.map((invoice) => (
              <div key={invoice.id} className="p-4 flex justify-between items-center hover:bg-slate-50 transition-colors">
                <div>
                  <p className="font-bold text-slate-900">{invoice.month}</p>
                  <p className="text-sm text-slate-500">Total: {currency.format(invoice.totalDue)}</p>
                </div>
                <div className="text-right">
                  <span className={`px-2 py-1 text-xs font-bold rounded-full ${
                    invoice.status === 'PAID' ? 'bg-emerald-100 text-emerald-700' :
                    invoice.status === 'PARTIAL' ? 'bg-amber-100 text-amber-700' :
                    'bg-red-100 text-red-700'
                  }`}>
                    {invoice.status}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}