// app/landlord/billing/page.tsx
'use client';
import { useEffect, useState, useCallback } from 'react';
import { Droplet, CheckCircle, Clock, AlertTriangle, ChevronRight, Info, RefreshCw } from 'lucide-react';
import { Pagination } from '../../../components/ui/Pagination';
import Link from 'next/link';

interface UnitBillingRow {
  id: string;
  unitCode: string;
  tenantName: string;
  rent: number;
  garbage: number;
  waterRate: number;
  waterUnitsUsed: number | null;
  waterReadingStatus: string;
}

interface DraftInvoice {
  id: string;
  billing_month: string;
  unit_id: string;
  unit_code?: string;
  tenant_name?: string;
  rent_amount: number;
  garbage_amount: number;
  water_units: number;
  water_amount: number;
  water_reading_missing: boolean;
  carried_arrears_amount: number;
  total_amount_due: number;
  amount_paid: number;
  outstanding_balance: number;
  status: string;
}

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

type WizardStep = 'waiting_readings' | 'drafts_ready' | 'published';

export default function BillingWizard({ canEditPaymentInstructions = true }: { canEditPaymentInstructions?: boolean }) {
  const [rows, setRows] = useState<UnitBillingRow[]>([]);
  const [draftInvoices, setDraftInvoices] = useState<DraftInvoice[]>([]);
  const [step, setStep] = useState<WizardStep>('waiting_readings');
  const [preparationMonth, setPreparationMonth] = useState('');
  const [previousMonthLabel, setPreviousMonthLabel] = useState('');
  const [releaseAt, setReleaseAt] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [paybillNumber, setPaybillNumber] = useState('');
  const [accountNumber, setAccountNumber] = useState('');
  const [isSavingPaymentInfo, setIsSavingPaymentInfo] = useState(false);
  const [paymentInfoMessage, setPaymentInfoMessage] = useState('');

  // Pagination
  const [readingsPage, setReadingsPage] = useState(1);
  const [draftsPage, setDraftsPage] = useState(1);
  const ITEMS_PER_PAGE = 10;

  const nextMonth = preparationMonth;
  const nextMonthLabel = nextMonth
    ? new Date(`${nextMonth}-01`).toLocaleString('default', { month: 'long', year: 'numeric' })
    : '';

  const fetchDraftInvoices = useCallback(async (month: string) => {
    try {
      const res = await fetch(`/api/billing/drafts?month=${month}`);
      const data = await res.json();
      if (res.ok) setDraftInvoices(data.drafts || []);
    } catch {
      // Silently ignore
    }
  }, []);

  const fetchBillingRows = useCallback(async () => {
    try {
      const [unitsRes, waterRes] = await Promise.all([
        fetch('/api/units'),
        fetch('/api/caretaker/water-readings'),
      ]);
      const unitsData = await unitsRes.json();
      const waterData = await waterRes.json();
      if (!unitsRes.ok) throw new Error(unitsData.error || 'Failed to fetch units');

      const waterMap = (waterData.readings || []).reduce((acc: any, curr: any) => {
        acc[curr.unitId] = curr;
        return acc;
      }, {});

      const mappedRows = (unitsData.units || [])
        .filter((unit: any) => unit.status === 'occupied' || unit.status === 'notice_given')
        .map((unit: any) => {
          const w = waterMap[unit.id];
          return {
            id: unit.id,
            unitCode: unit.unitCode,
            tenantName: unit.currentTenant || 'Unassigned',
            rent: Number(unit.rentAmount || 0),
            garbage: Number(unit.garbageAmount || 0),
            waterRate: Number(unit.monthlyWaterRate || 0),
            waterUnitsUsed: w?.unitsUsed ?? null,
            waterReadingStatus: w?.status ?? 'not_submitted',
          };
        });
      setRows(mappedRows);
    } catch (err: any) {
      setError('Unable to load billing rows.');
    }
  }, []);

  const loadAll = useCallback(async () => {
    try {
      const cycleRes = await fetch('/api/billing/cycle');
      const cycleData = await cycleRes.json();
      const month: string = cycleData.preparationMonth || '';
      if (!month) return;

      setPreparationMonth(month);
      if (cycleData.releaseAt) setReleaseAt(cycleData.releaseAt);

      // Compute the previously-billed month label
      const prevDate = new Date(`${month}-01`);
      prevDate.setMonth(prevDate.getMonth() - 1);
      setPreviousMonthLabel(prevDate.toLocaleString('default', { month: 'long', year: 'numeric' }));

      // Check status (this also auto-publishes if release time has passed)
      const statusRes = await fetch(`/api/billing/status?month=${month}`);
      const statusData = await statusRes.json();

      if (statusData.hasPublished) {
        setStep('published');
        fetchBillingRows();
      } else if (statusData.hasDrafts) {
        setStep('drafts_ready');
        await fetchDraftInvoices(month);
      } else {
        setStep('waiting_readings');
        fetchBillingRows();
      }
    } catch {
      setError('Unable to load billing status.');
    }
  }, [fetchBillingRows, fetchDraftInvoices]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  useEffect(() => {
    fetch('/api/billing/settings')
      .then((response) => response.json())
      .then((data) => {
        if (data.settings) {
          setPaybillNumber(data.settings.paybill_number || '');
          setAccountNumber(data.settings.account_number || '');
        }
      })
      .catch(() => undefined);
  }, []);

  // Poll every 30s to auto-detect state transitions (e.g., caretaker publishes readings,
  // release time arrives, etc.)
  useEffect(() => {
    const interval = setInterval(() => {
      loadAll();
    }, 30_000);
    return () => clearInterval(interval);
  }, [loadAll]);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    await loadAll();
    setIsRefreshing(false);
  };

  const savePaymentInstructions = async () => {
    setIsSavingPaymentInfo(true);
    setPaymentInfoMessage('');
    try {
      const response = await fetch('/api/billing/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paybillNumber, accountNumber }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to save payment instructions.');
      setPaymentInfoMessage('Manual payment instructions updated.');
    } catch (saveError: any) {
      setPaymentInfoMessage(saveError.message);
    } finally {
      setIsSavingPaymentInfo(false);
    }
  };

  if (!preparationMonth) {
    return (
      <div className="flex justify-center items-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-600"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold text-slate-900">Billing Wizard</h1>
          <p className="text-slate-500 mt-1">Live view of the billing cycle. Everything updates automatically.</p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={isRefreshing}
          className="flex items-center gap-2 text-sm text-slate-500 hover:text-slate-700 border border-slate-200 rounded-lg px-3 py-2 transition-colors disabled:opacity-50"
        >
          <RefreshCw size={14} className={isRefreshing ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-3 bg-red-50 border border-red-200 text-red-700 rounded-xl px-5 py-4 text-sm">
          <AlertTriangle size={18} className="shrink-0" /> {error}
        </div>
      )}

      {canEditPaymentInstructions && <section className="bg-white border border-slate-200 rounded-xl p-5">
        <div className="flex flex-col sm:flex-row sm:items-end gap-3">
          <label className="text-sm font-medium text-slate-700 flex-1">Manual payment Paybill<input value={paybillNumber} onChange={(event) => setPaybillNumber(event.target.value)} className="mt-1 w-full border border-slate-300 rounded-lg px-3 py-2" /></label>
          <label className="text-sm font-medium text-slate-700 flex-1">Account number<input value={accountNumber} onChange={(event) => setAccountNumber(event.target.value)} className="mt-1 w-full border border-slate-300 rounded-lg px-3 py-2" /></label>
          <button onClick={savePaymentInstructions} disabled={isSavingPaymentInfo} className="bg-slate-900 text-white rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-50">{isSavingPaymentInfo ? 'Saving...' : 'Save Payment Instructions'}</button>
        </div>
        {paymentInfoMessage && <p className="mt-2 text-sm text-slate-600" role="status">{paymentInfoMessage}</p>}
      </section>}

      {/* Step Indicator */}
      <div className="flex items-center gap-4 bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        {[
          { key: 'waiting_readings', label: 'Awaiting Readings', num: 1 },
          { key: 'drafts_ready', label: 'Drafts Ready', num: 2 },
          { key: 'published', label: 'Published', num: 3 },
        ].map((s, i, arr) => (
          <div key={s.key} className="flex items-center gap-2">
            <div className={`flex items-center gap-2 font-medium text-sm ${step === s.key ? 'text-emerald-600' : 'text-slate-400'}`}>
              <span className={`w-7 h-7 flex items-center justify-center rounded-full text-white text-xs font-bold ${step === s.key ? 'bg-emerald-600' : ((['drafts_ready', 'published'].includes(step) && s.num < (step === 'published' ? 3 : 2)) ? 'bg-emerald-400' : 'bg-slate-300')}`}>
                {s.num}
              </span>
              {s.label}
            </div>
            {i < arr.length - 1 && <ChevronRight size={16} className="text-slate-300 ml-2" />}
          </div>
        ))}
      </div>

      {/* ─── Step 1: Waiting for caretaker readings ─── */}
      {step === 'waiting_readings' && (
        <div className="space-y-4">
          <div className="bg-blue-50 border border-blue-200 rounded-xl p-5 flex items-start gap-3">
            <Droplet size={20} className="text-blue-500 shrink-0 mt-0.5" />
            <div>
              <p className="font-bold text-blue-900">{nextMonthLabel} — Waiting for Water Readings</p>
              <p className="text-sm text-blue-800 mt-1">
                Draft invoices will be generated automatically once the caretaker publishes the water meter readings.
                The invoices will then be auto-published at <strong>{releaseAt ? new Date(releaseAt).toLocaleString() : `${nextMonth}-01`}</strong>.
              </p>
            </div>
          </div>

          <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
            <div className="p-6 border-b border-slate-200 bg-slate-50">
              <h2 className="font-bold text-slate-900">{nextMonthLabel} — Water Reading Status</h2>
              <p className="text-sm text-slate-500 mt-1">Waiting for the caretaker to submit and publish readings for all units.</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-200 text-sm text-slate-500">
                    <th className="p-4 font-medium">Unit</th>
                    <th className="p-4 font-medium">Tenant</th>
                    <th className="p-4 font-medium">Fixed (Rent + Garbage)</th>
                    <th className="p-4 font-medium">Rate / Unit</th>
                    <th className="p-4 font-medium bg-blue-50/50">Units Used</th>
                    <th className="p-4 font-medium text-right">Est. Water Bill</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200">
                  {rows.length === 0 ? (
                    <tr><td colSpan={6} className="p-6 text-center text-slate-500">No occupied units found.</td></tr>
                  ) : rows.slice((readingsPage - 1) * ITEMS_PER_PAGE, readingsPage * ITEMS_PER_PAGE).map((row) => {
                    const waterUnits = row.waterUnitsUsed || 0;
                    const waterCost = waterUnits * row.waterRate;
                    return (
                      <tr key={row.id} className="hover:bg-slate-50 transition-colors">
                        <td className="p-4 font-bold text-slate-900">{row.unitCode}</td>
                        <td className="p-4 text-slate-700">{row.tenantName}</td>
                        <td className="p-4 text-slate-700">{currency.format(row.rent + row.garbage)}</td>
                        <td className="p-4 text-slate-500 text-sm">{currency.format(row.waterRate)} / unit</td>
                        <td className="p-4 bg-blue-50/30 font-bold text-slate-900">
                          {row.waterUnitsUsed !== null ? (
                            <div className="flex items-center gap-2">
                              <span>{row.waterUnitsUsed} units</span>
                              <span className={`text-[10px] px-2 py-0.5 rounded-full uppercase ${row.waterReadingStatus === 'published' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>
                                {row.waterReadingStatus}
                              </span>
                            </div>
                          ) : (
                            <span className="text-slate-400 font-normal flex items-center gap-1.5">
                              <Clock size={12} /> Pending caretaker
                            </span>
                          )}
                        </td>
                        <td className="p-4 text-right font-bold text-blue-600">
                          {row.waterUnitsUsed !== null ? currency.format(waterCost) : '—'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {rows.length > 0 && (
                <Pagination
                  currentPage={readingsPage}
                  totalPages={Math.ceil(rows.length / ITEMS_PER_PAGE)}
                  onPageChange={setReadingsPage}
                />
              )}
            </div>
          </div>
        </div>
      )}

      {/* ─── Step 2: Drafts Ready (read-only view) ─── */}
      {step === 'drafts_ready' && (
        <div className="space-y-4">
          {draftInvoices.some((invoice) => invoice.water_reading_missing) && (
            <div className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-900">
              <p className="font-bold">Some invoices are on hold until meter readings are corrected.</p>
              <p className="mt-1">
                No water charge has been published for: {draftInvoices.filter((invoice) => invoice.water_reading_missing).map((invoice) => invoice.unit_code).join(', ')}.
                Submit a valid reading for the month before these invoices can be released.
              </p>
              {!canEditPaymentInstructions && <Link href="/caretaker/water-readings" className="mt-2 inline-block font-bold underline">Open water readings</Link>}
            </div>
          )}
          <div className="bg-amber-50 border border-amber-200 rounded-xl p-5 flex items-start gap-3">
            <Clock size={20} className="text-amber-600 shrink-0 mt-0.5" />
            <div>
              <p className="font-bold text-amber-900">Draft Invoices Ready — Pending Auto-Publish</p>
              <p className="text-sm text-amber-800 mt-1">
                The invoices below are prepared and will be <strong>automatically published</strong> at{' '}
                <strong>{releaseAt ? new Date(releaseAt).toLocaleString() : `${nextMonth}-01`}</strong>.
                Before release, transition arrears payments are recorded in the <strong>Arrears Editor</strong>. After release, payments are recorded in the <strong>Ledger</strong>.
              </p>
            </div>
          </div>

          <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
            <div className="p-6 border-b border-slate-200 bg-slate-50">
              <h2 className="font-bold text-slate-900">Draft Invoices — {nextMonthLabel}</h2>
              <p className="text-sm text-slate-500">Read-only until release. Transition arrears and their payment history update these totals.</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-sm">
                <thead>
                  <tr className="bg-slate-900 text-white">
                    <th className="p-4 font-medium">Unit</th>
                    <th className="p-4 font-medium">Tenant</th>
                    <th className="p-4 font-medium">Rent + Garbage</th>
                    <th className="p-4 font-medium">Water Bill</th>
                    <th className="p-4 font-medium text-red-300">Carried Arrears</th>
                    <th className="p-4 font-medium">Amount Paid</th>
                    <th className="p-4 font-medium text-right">Outstanding</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200">
                  {draftInvoices.length === 0 ? (
                    <tr><td colSpan={7} className="p-6 text-center text-slate-500">No draft invoices found for {nextMonth}.</td></tr>
                  ) : draftInvoices.slice((draftsPage - 1) * ITEMS_PER_PAGE, draftsPage * ITEMS_PER_PAGE).map((invoice) => (
                    <tr key={invoice.id} className="hover:bg-slate-50">
                      <td className="p-4 font-bold text-slate-900">{invoice.unit_code}</td>
                      <td className="p-4 text-slate-700">{invoice.tenant_name}</td>
                      <td className="p-4 text-slate-700">{currency.format(invoice.rent_amount + invoice.garbage_amount)}</td>
                      <td className={`p-4 font-medium ${invoice.water_reading_missing ? 'text-red-700' : 'text-blue-600'}`}>
                        {invoice.water_reading_missing ? 'Reading required' : currency.format(invoice.water_amount)}
                      </td>
                      <td className="p-4 text-red-600 font-medium">
                        {invoice.carried_arrears_amount > 0 ? currency.format(invoice.carried_arrears_amount) : '—'}
                      </td>
                      <td className="p-4 text-emerald-600 font-medium">
                        {invoice.amount_paid > 0 ? currency.format(invoice.amount_paid) : '—'}
                      </td>
                      <td className="p-4 text-right font-bold text-slate-900">{currency.format(invoice.outstanding_balance)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {draftInvoices.length > 0 && (
                <Pagination
                  currentPage={draftsPage}
                  totalPages={Math.ceil(draftInvoices.length / ITEMS_PER_PAGE)}
                  onPageChange={setDraftsPage}
                />
              )}
            </div>
          </div>
        </div>
      )}

      {/* ─── Step 3: Published ─── */}
      {step === 'published' && (
        <div className="space-y-6">
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-10 text-center">
            <div className="w-20 h-20 bg-emerald-100 rounded-full flex items-center justify-center mx-auto mb-6">
              <CheckCircle size={40} className="text-emerald-600" />
            </div>
            <h2 className="text-2xl font-bold text-slate-900">
              {previousMonthLabel} invoices are published
            </h2>
            <p className="text-slate-500 mt-3 max-w-lg mx-auto">
              Tenants can already view their bills. The system is now preparing for <strong>{nextMonthLabel}</strong>.
              This page will automatically advance once the caretaker publishes water readings for the new cycle.
            </p>
          </div>

          <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-5">
            <p className="font-semibold text-slate-800 flex items-center gap-1.5 mb-2">
              <Info size={14} className="text-blue-500" /> What to do before end of {nextMonthLabel}:
            </p>
            <ol className="list-decimal ml-5 space-y-1 text-sm text-slate-600">
              <li>Ensure the caretaker submits and publishes water meter readings before the billing date.</li>
              <li>Record any manual tenant payments in the <strong>Ledger</strong> — invoices update in real time.</li>
              <li>
                Draft invoices will appear here automatically once readings are published.
                They will be auto-published at the release time.
              </li>
            </ol>
          </div>
        </div>
      )}
    </div>
  );
}
