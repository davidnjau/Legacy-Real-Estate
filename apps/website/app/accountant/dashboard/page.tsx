'use client';

import { useState } from 'react';
import Link from 'next/link';
import {
  Building2,
  Receipt,
  FileSpreadsheet,
  CheckCircle2,
  AlertTriangle,
  ArrowRight,
  TrendingUp,
  Download,
  Upload,
  RefreshCw,
  Landmark,
  ShieldCheck,
  Check,
  X,
  CreditCard,
} from 'lucide-react';

/* ─── Currency Formatter ─── */
const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

/* ─── Prototype Seed Data ─── */
const INITIAL_UNMATCHED = [
  {
    id: 'TX-MPESA-8842',
    transactionCode: 'SK92KLM401',
    amount: 15500,
    payerPhone: '+254733000003',
    accountEntered: 'b4 (lowercase)',
    suggestedTenant: 'Brian Omondi — Unit NAI-SV-A3 (Sunset Villas)',
    confidence: '95% (Phone match)',
    receivedAt: 'Today 08:42 EAT',
    status: 'Unmatched',
  },
  {
    id: 'TX-MPESA-8841',
    transactionCode: 'SK91KPA992',
    amount: 22000,
    payerPhone: '+254722119988',
    accountEntered: 'faith rent parklands',
    suggestedTenant: 'Faith Chebet — Unit NAI-PH-B2 (Parklands Heights)',
    confidence: '90% (Name & Phone match)',
    receivedAt: 'Today 07:15 EAT',
    status: 'Unmatched',
  },
];

const REMITTANCE_RECORDS = [
  {
    landlord: 'Dr. James Mwangi',
    property: 'Sunset Villas (Kilimani)',
    grossRent: 240000,
    commission: 24000,
    expenses: 12000,
    mriTax: 18000,
    netPayable: 186000,
    payoutMethod: 'Bank: Equity (Acc #01802...)',
    status: 'Ready for Approval',
  },
  {
    landlord: 'Grace Njeri Investments Ltd',
    property: 'Parklands Heights',
    grossRent: 180000,
    commission: 18000,
    expenses: 8500,
    mriTax: 13500,
    netPayable: 140000,
    payoutMethod: 'M-Pesa B2C (+254722998811)',
    status: 'Ready for Approval',
  },
  {
    landlord: 'Pastor David Karanja',
    property: 'Milimani Gardens (Nakuru)',
    grossRent: 100000,
    commission: 10000,
    expenses: 4200,
    mriTax: 7500,
    netPayable: 78300,
    payoutMethod: 'Bank: KCB (Acc #11492...)',
    status: 'Ready for Approval',
  },
];

export default function AccountantDashboardPage() {
  const [unmatched, setUnmatched] = useState(INITIAL_UNMATCHED);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'unmatched' | 'remittance' | 'reconciliation' | 'kra'>('unmatched');

  const handleAllocate = (id: string, tenant: string) => {
    setUnmatched((prev) => prev.filter((item) => item.id !== id));
    setFeedback(`Payment ${id} successfully allocated to ${tenant}. Receipt SMS queued via Softlink.`);
    setTimeout(() => setFeedback(null), 5000);
  };

  return (
    <div className="min-h-screen bg-[#f4f7f5] text-slate-900 pb-16">
      {/* Header */}
      <header className="border-b border-emerald-950/10 bg-[#102820] text-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-emerald-400 font-bold text-[#102820]">
              <Landmark size={20} />
            </span>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold tracking-widest uppercase text-emerald-300">Accounting & Financial Ledgers</span>
                <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-semibold text-emerald-200 ring-1 ring-emerald-400/30">
                  SRS Section 02 & 10 (Accountant)
                </span>
              </div>
              <h1 className="text-lg font-bold sm:text-xl">Financial Control Desk — Grace Wambui, CPA</h1>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <span className="rounded-full bg-emerald-500/20 px-3 py-1 text-xs font-semibold text-emerald-200 ring-1 ring-emerald-400/30">
              Finance MFA Verified
            </span>
            <a
              href="/api/auth/logout"
              className="rounded-xl border border-white/20 bg-white/5 px-3 py-1.5 text-xs font-semibold text-emerald-100 hover:bg-red-500/20 hover:text-white transition"
            >
              Sign Out
            </a>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="mx-auto max-w-7xl px-4 pt-8 sm:px-6 lg:px-8 space-y-8">
        {feedback && (
          <div className="rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-sm font-semibold text-emerald-900 flex items-center justify-between shadow-xs">
            <span>{feedback}</span>
            <button onClick={() => setFeedback(null)} className="text-emerald-700 hover:text-emerald-950">
              <X size={16} />
            </button>
          </div>
        )}

        {/* 3 Money Pots (SRS Section 10) */}
        <div>
          <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-3">
            Three Ledger Pots Separation (SRS Section 10)
          </h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
              <span className="text-xs font-bold uppercase text-slate-500">Landlord Trust Collections</span>
              <p className="mt-2 text-2xl font-bold text-slate-900">{currency.format(4850000)}</p>
              <p className="mt-1 text-xs text-slate-500">Rent held in fiduciary trust</p>
            </div>

            <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
              <span className="text-xs font-bold uppercase text-slate-500">Tenant Deposits Trust</span>
              <p className="mt-2 text-2xl font-bold text-slate-900">{currency.format(1420000)}</p>
              <p className="mt-1 text-xs text-emerald-700 font-semibold">Never counted as income</p>
            </div>

            <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
              <span className="text-xs font-bold uppercase text-slate-500">Agency Commission Income</span>
              <p className="mt-2 text-2xl font-bold text-emerald-800">{currency.format(485000)}</p>
              <p className="mt-1 text-xs text-slate-500">Earned 10% management fee</p>
            </div>

            <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
              <span className="text-xs font-bold uppercase text-slate-500">KRA MRI Tax Withheld</span>
              <p className="mt-2 text-2xl font-bold text-slate-900">{currency.format(363750)}</p>
              <p className="mt-1 text-xs text-slate-500">7.5% due by 20th of next month</p>
            </div>
          </div>
        </div>

        {/* Navigation Tabs */}
        <div className="flex border-b border-slate-200 gap-6 text-sm font-semibold text-slate-600">
          <button
            onClick={() => setActiveTab('unmatched')}
            className={`pb-3 border-b-2 transition flex items-center gap-2 ${activeTab === 'unmatched' ? 'border-emerald-800 text-emerald-900 font-bold' : 'border-transparent hover:text-slate-900'}`}
          >
            <span>Unmatched M-Pesa Queue</span>
            {unmatched.length > 0 && (
              <span className="rounded-full bg-amber-100 text-amber-800 px-2 py-0.5 text-xs font-bold">
                {unmatched.length}
              </span>
            )}
          </button>
          <button
            onClick={() => setActiveTab('remittance')}
            className={`pb-3 border-b-2 transition ${activeTab === 'remittance' ? 'border-emerald-800 text-emerald-900 font-bold' : 'border-transparent hover:text-slate-900'}`}
          >
            Landlord Remittance Runs (SRS 10.2)
          </button>
          <button
            onClick={() => setActiveTab('reconciliation')}
            className={`pb-3 border-b-2 transition ${activeTab === 'reconciliation' ? 'border-emerald-800 text-emerald-900 font-bold' : 'border-transparent hover:text-slate-900'}`}
          >
            Bank & M-Pesa Daily Reconciliation
          </button>
          <button
            onClick={() => setActiveTab('kra')}
            className={`pb-3 border-b-2 transition ${activeTab === 'kra' ? 'border-emerald-800 text-emerald-900 font-bold' : 'border-transparent hover:text-slate-900'}`}
          >
            KRA Monthly Rental Income (eRITS)
          </button>
        </div>

        {/* Tab 1: Unmatched Queue */}
        {activeTab === 'unmatched' && (
          <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-base font-bold text-slate-900">M-Pesa Unmatched Queue (SRS Section 06.2)</h2>
                <p className="text-xs text-slate-500">
                  Callbacks where the account number did not match a valid unit code. Fuzzy phone-match suggestions shown.
                </p>
              </div>
              <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
                Daraja C2B Auto-Catch
              </span>
            </div>

            {unmatched.length === 0 ? (
              <div className="py-12 text-center text-slate-500 text-sm">
                <CheckCircle2 size={32} className="mx-auto text-emerald-600 mb-2" />
                All M-Pesa payments have been matched and allocated!
              </div>
            ) : (
              <div className="space-y-4">
                {unmatched.map((item) => (
                  <div key={item.id} className="rounded-xl border border-slate-200 bg-slate-50/50 p-4 flex flex-wrap items-center justify-between gap-4">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs font-bold bg-emerald-100 text-emerald-900 px-2 py-0.5 rounded">
                          {item.transactionCode}
                        </span>
                        <span className="font-bold text-slate-900 text-sm">{currency.format(item.amount)}</span>
                        <span className="text-xs text-slate-500">• {item.receivedAt}</span>
                      </div>
                      <p className="text-xs text-slate-600 mt-1">
                        Payer Phone: <span className="font-mono font-semibold">{item.payerPhone}</span> • Account typed:{' '}
                        <span className="font-mono text-red-700 bg-red-50 px-1.5 py-0.5 rounded font-bold">
                          "{item.accountEntered}"
                        </span>
                      </p>
                      <div className="mt-2 text-xs flex items-center gap-1.5 text-emerald-800 bg-emerald-50/80 px-2.5 py-1 rounded-lg border border-emerald-200/60 w-fit">
                        <span>Suggested Match: <strong>{item.suggestedTenant}</strong></span>
                        <span className="text-[10px] font-bold text-emerald-700">({item.confidence})</span>
                      </div>
                    </div>

                    <button
                      onClick={() => handleAllocate(item.id, item.suggestedTenant)}
                      className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-800 px-4 py-2 text-xs font-bold text-white shadow-xs hover:bg-emerald-900"
                    >
                      <Check size={14} /> Allocate Payment
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Tab 2: Remittance Runs */}
        {activeTab === 'remittance' && (
          <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-base font-bold text-slate-900">Monthly Landlord Remittance Schedule (SRS 10.2)</h2>
                <p className="text-xs text-slate-500">
                  Formula: Gross Rent − Commission (10%) − Approved Expenses − KRA MRI Tax (7.5%) = Net Remittance
                </p>
              </div>
              <button
                disabled
                className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-100 px-3 py-1.5 text-xs font-bold text-slate-500 cursor-not-allowed"
              >
                <Download size={14} /> Export Bank Bulk File (Phase 2)
              </button>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm text-slate-700">
                <thead className="bg-slate-50 text-xs font-bold uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="px-4 py-3">Landlord</th>
                    <th className="px-4 py-3">Property</th>
                    <th className="px-4 py-3">Gross Rent</th>
                    <th className="px-4 py-3">Commission (10%)</th>
                    <th className="px-4 py-3">Expenses</th>
                    <th className="px-4 py-3">MRI (7.5%)</th>
                    <th className="px-4 py-3">Net Payout</th>
                    <th className="px-4 py-3">Disbursement Destination</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {REMITTANCE_RECORDS.map((r) => (
                    <tr key={r.landlord} className="hover:bg-slate-50/50">
                      <td className="px-4 py-3.5 font-bold text-slate-900">{r.landlord}</td>
                      <td className="px-4 py-3.5 text-xs text-slate-600">{r.property}</td>
                      <td className="px-4 py-3.5">{currency.format(r.grossRent)}</td>
                      <td className="px-4 py-3.5 text-slate-600">−{currency.format(r.commission)}</td>
                      <td className="px-4 py-3.5 text-slate-600">−{currency.format(r.expenses)}</td>
                      <td className="px-4 py-3.5 text-slate-600">−{currency.format(r.mriTax)}</td>
                      <td className="px-4 py-3.5 font-bold text-emerald-800">{currency.format(r.netPayable)}</td>
                      <td className="px-4 py-3.5 font-mono text-xs">{r.payoutMethod}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Tab 3: Reconciliation */}
        {activeTab === 'reconciliation' && (
          <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs max-w-3xl">
            <h2 className="text-base font-bold text-slate-900">Bank & M-Pesa Statement Reconciliation</h2>
            <p className="text-xs text-slate-500 mb-6">Daily automated matching against Safaricom Daraja and Bank MT940 / CSV imports</p>

            <div className="space-y-4">
              <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4 flex items-center justify-between">
                <div>
                  <p className="text-sm font-bold text-emerald-950">Safaricom Daraja API Sync</p>
                  <p className="text-xs text-emerald-800">Last synced 8 minutes ago • 142 transactions reconciled</p>
                </div>
                <span className="rounded-full bg-emerald-200 px-3 py-1 text-xs font-bold text-emerald-900">
                  Operational
                </span>
              </div>

              <div className="rounded-xl border border-slate-200 p-4 flex items-center justify-between">
                <div>
                  <p className="text-sm font-bold text-slate-900">Equity Bank Corporate CSV Upload</p>
                  <p className="text-xs text-slate-500">Upload bank statement to match tenant EFT and RTGS deposits</p>
                </div>
                <button
                  disabled
                  className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-100 px-3 py-1.5 text-xs font-bold text-slate-500 cursor-not-allowed"
                >
                  <Upload size={14} /> Upload Statement (Phase 2)
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Tab 4: KRA MRI */}
        {activeTab === 'kra' && (
          <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-base font-bold text-slate-900">KRA Monthly Rental Income (MRI) Tax Schedule</h2>
                <p className="text-xs text-slate-500">Form for monthly eRITS upload due by the 20th of the following month</p>
              </div>
              <button
                disabled
                className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-100 px-3 py-1.5 text-xs font-bold text-slate-500 cursor-not-allowed"
              >
                <Download size={14} /> Export KRA CSV (Phase 2)
              </button>
            </div>

            <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 text-xs font-mono">
              <p>Total Landlord MRI Tax Withheld for November 2026: <strong>{currency.format(363750)}</strong></p>
              <p className="text-slate-500 mt-1">Status: Fully reconciled across 6 landlord management agreements.</p>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
