'use client';

import { useState } from 'react';
import Link from 'next/link';
import {
  Building2,
  CheckCircle2,
  XCircle,
  Clock,
  AlertCircle,
  FileText,
  DollarSign,
  TrendingUp,
  MapPin,
  Calendar,
  Check,
  X,
  ShieldAlert,
} from 'lucide-react';

/* ─── Currency Formatter ─── */
const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

/* ─── Prototype Seed Data (Branch Scoped) ─── */
const INITIAL_APPROVALS = [
  {
    id: 'APP-101',
    type: 'Deposit Refund (Move-out)',
    subject: 'Brian Omondi — Unit B04 (Sunset Villas)',
    detail: 'Deposit held: KES 20,000 − Damages: KES 2,000 (Mixer tap) = KES 18,000 net refund',
    amount: 18000,
    requestedBy: 'Jane Njeri (Property Officer)',
    status: 'Pending',
  },
  {
    id: 'APP-102',
    type: 'Late Penalty Waiver',
    subject: 'Faith Chebet — Unit A02 (Parklands Heights)',
    detail: 'Late fee KES 1,000 waived due to Safaricom Daraja system downtime during paybill callback',
    amount: 1000,
    requestedBy: 'Grace Wambui (Accountant)',
    status: 'Pending',
  },
  {
    id: 'APP-103',
    type: 'Emergency CAPEX Maintenance',
    subject: 'Borehole Submersible Pump Overhaul (Kahawa Court)',
    detail: 'Repair estimate > KES 10,000 threshold. Quotation attached: KES 38,500 from Davis & Shirtliff',
    amount: 38500,
    requestedBy: 'Samuel Otieno (Caretaker)',
    status: 'Pending',
  },
  {
    id: 'APP-104',
    type: 'Landlord Remittance Authorization',
    subject: 'Sunset Villas Monthly Remittance (Dr. James Mwangi)',
    detail: 'Gross Rent: KES 240,000 − 10% Comm: KES 24,000 − Expenses: KES 12,000 − 7.5% MRI: KES 18,000 = KES 186,000 Net',
    amount: 186000,
    requestedBy: 'Grace Wambui (Accountant)',
    status: 'Pending',
  },
];

const ARREARS_AGEING = [
  { bucket: 'Current (0–5 Days Grace)', amount: 4850000, count: 39, tone: 'text-emerald-700 bg-emerald-50' },
  { bucket: '1–30 Days Arrears', amount: 185000, count: 5, tone: 'text-amber-700 bg-amber-50' },
  { bucket: '31–60 Days Arrears', amount: 95000, count: 2, tone: 'text-orange-700 bg-orange-50' },
  { bucket: '61–90 Days Arrears', amount: 45000, count: 1, tone: 'text-red-700 bg-red-50' },
  { bucket: '90+ Days (Legal Escalation)', amount: 25000, count: 1, tone: 'text-red-900 bg-red-100' },
];

export default function BranchManagerDashboardPage() {
  const [approvals, setApprovals] = useState(INITIAL_APPROVALS);
  const [feedback, setFeedback] = useState<string | null>(null);

  const handleAction = (id: string, action: 'Approved' | 'Rejected') => {
    setApprovals((prev) =>
      prev.map((app) => (app.id === id ? { ...app, status: action } : app))
    );
    setFeedback(`Action successfully recorded in Audit Log: ${id} marked as ${action}.`);
    setTimeout(() => setFeedback(null), 4000);
  };

  return (
    <div className="min-h-screen bg-[#f4f7f5] text-slate-900 pb-16">
      {/* Top Banner */}
      <header className="border-b border-emerald-950/10 bg-[#102820] text-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-emerald-400 font-bold text-[#102820]">
              <Building2 size={20} />
            </span>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold tracking-widest uppercase text-emerald-300">Branch Operations & Approvals</span>
                <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-semibold text-emerald-200 ring-1 ring-emerald-400/30">
                  SRS Section 02 (Branch Manager)
                </span>
              </div>
              <h1 className="text-lg font-bold sm:text-xl">Nairobi Branch Office — Joseph Kariuki</h1>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-3 py-1 text-xs font-semibold text-emerald-100">
              <MapPin size={13} className="text-emerald-300" /> Nairobi Scope Enforced
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

        {/* Top KPI Cards */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <p className="text-xs font-bold uppercase tracking-wider text-slate-500">Branch Occupancy</p>
            <p className="mt-2 text-3xl font-bold text-slate-900">94.2%</p>
            <p className="mt-1 text-xs text-slate-500">40 of 42 units occupied</p>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <p className="text-xs font-bold uppercase tracking-wider text-slate-500">Month Collections</p>
            <p className="mt-2 text-3xl font-bold text-slate-900">{currency.format(3450000)}</p>
            <p className="mt-1 text-xs text-slate-500">Expected: {currency.format(3680000)}</p>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <p className="text-xs font-bold uppercase tracking-wider text-slate-500">Pending Approvals</p>
            <p className="mt-2 text-3xl font-bold text-amber-700">
              {approvals.filter((a) => a.status === 'Pending').length} Action Items
            </p>
            <p className="mt-1 text-xs text-slate-500">Maker-Checker dual sign-off required</p>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <p className="text-xs font-bold uppercase tracking-wider text-slate-500">Today Cash Receipts</p>
            <p className="mt-2 text-3xl font-bold text-slate-900">{currency.format(0)}</p>
            <p className="mt-1 text-xs text-emerald-700 font-semibold">100% Cashless via Paybill</p>
          </div>
        </div>

        {/* Maker-Checker Approval Desk */}
        <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-base font-bold text-slate-900">Maker-Checker Approval Desk (SRS Section 02 & 10.2)</h2>
              <p className="text-xs text-slate-500">
                Sensitive actions require branch manager sign-off: deposit refunds, penalty waivers, and contractor CAPEX
              </p>
            </div>
            <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-bold text-amber-800">
              Manager Authorization Authority
            </span>
          </div>

          <div className="divide-y divide-slate-100">
            {approvals.map((item) => (
              <div key={item.id} className="py-4 flex flex-wrap items-center justify-between gap-4">
                <div className="max-w-2xl">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-bold text-slate-500">{item.id}</span>
                    <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-bold text-slate-700">
                      {item.type}
                    </span>
                    <span className="text-sm font-bold text-slate-900">{item.subject}</span>
                  </div>
                  <p className="text-xs text-slate-600 mt-1">{item.detail}</p>
                  <p className="text-[11px] text-slate-400 mt-0.5">
                    Requested by: <span className="font-semibold text-slate-700">{item.requestedBy}</span>
                  </p>
                </div>

                <div className="flex items-center gap-3">
                  <span className="font-mono text-sm font-bold text-slate-900">
                    {currency.format(item.amount)}
                  </span>
                  {item.status === 'Pending' ? (
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleAction(item.id, 'Approved')}
                        className="inline-flex items-center gap-1 rounded-xl bg-emerald-800 px-3 py-1.5 text-xs font-bold text-white shadow-xs hover:bg-emerald-900"
                      >
                        <Check size={14} /> Approve
                      </button>
                      <button
                        onClick={() => handleAction(item.id, 'Rejected')}
                        className="inline-flex items-center gap-1 rounded-xl border border-red-300 bg-red-50 px-3 py-1.5 text-xs font-bold text-red-800 hover:bg-red-100"
                      >
                        <X size={14} /> Reject
                      </button>
                    </div>
                  ) : (
                    <span
                      className={`rounded-full px-3 py-1 text-xs font-bold ${item.status === 'Approved' ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-800'}`}
                    >
                      {item.status}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Arrears Ageing Breakdown */}
        <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-base font-bold text-slate-900">Branch Arrears Ageing (SRS Section 06.4)</h2>
              <p className="text-xs text-slate-500">Rent overdue past grace period with automated reminder schedule</p>
            </div>
            <span className="text-xs font-bold text-slate-600">Total Arrears: {currency.format(350000)}</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
            {ARREARS_AGEING.map((a) => (
              <div key={a.bucket} className={`rounded-xl p-4 border border-slate-200/60 ${a.tone}`}>
                <p className="text-xs font-bold uppercase">{a.bucket}</p>
                <p className="mt-2 text-lg font-bold">{currency.format(a.amount)}</p>
                <p className="mt-0.5 text-xs opacity-80">{a.count} tenancies</p>
              </div>
            ))}
          </div>
        </div>

        {/* Bottom Dead-State Actions */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
            <h3 className="font-bold text-sm text-slate-900">Demand Letter Batch Dispatch</h3>
            <p className="text-xs text-slate-500 mt-1">Generate formal 7-day demand notices for tenants in 30+ day arrears</p>
            <button
              disabled
              className="mt-4 w-full rounded-xl border border-slate-200 bg-slate-100 py-2 text-xs font-bold text-slate-500 cursor-not-allowed"
            >
              Dispatch PDF Letters via SMS / Post (Phase 2)
            </button>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
            <h3 className="font-bold text-sm text-slate-900">Daily Cash Register Reconciliation</h3>
            <p className="text-xs text-slate-500 mt-1">Audit cash collections against physical deposit slips</p>
            <button
              disabled
              className="mt-4 w-full rounded-xl border border-slate-200 bg-slate-100 py-2 text-xs font-bold text-slate-500 cursor-not-allowed"
            >
              Sign Off Daily Cash Sheet (Phase 2)
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
