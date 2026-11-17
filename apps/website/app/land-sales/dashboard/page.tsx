'use client';

import { useState } from 'react';
import Link from 'next/link';
import {
  MapPin,
  TrendingUp,
  FileCheck,
  CheckCircle2,
  Calendar,
  Clock,
  Search,
  Check,
  X,
  FileSpreadsheet,
  AlertCircle,
  Map as MapIcon,
  Compass,
} from 'lucide-react';

/* ─── Currency Formatter ─── */
const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

/* ─── Prototype Seed Data ─── */
const PLOTS = [
  { no: 'PLOT-BG2-041', project: 'Bahati Gardens Phase 2', size: '50×100 ft (1/8 Acre)', price: 1200000, status: 'Sold – Paying', buyer: 'Mary Achieng' },
  { no: 'PLOT-BG2-042', project: 'Bahati Gardens Phase 2', size: '50×100 ft (1/8 Acre)', price: 1200000, status: 'Sold – Paying', buyer: 'Samuel Maina' },
  { no: 'PLOT-BG2-043', project: 'Bahati Gardens Phase 2', size: '50×100 ft (Corner Plot)', price: 1350000, status: 'Reserved', buyer: 'John Kimani (Booking Paid)' },
  { no: 'PLOT-BG2-044', project: 'Bahati Gardens Phase 2', size: '50×100 ft (1/8 Acre)', price: 1200000, status: 'Available', buyer: null },
  { no: 'PLOT-GPR-012', project: 'Greenpark Ridge (Lanet)', size: '100×100 ft (1/4 Acre)', price: 2400000, status: 'Title Processing', buyer: 'Dr. Evans Kosgei' },
  { no: 'PLOT-GPR-014', project: 'Greenpark Ridge (Lanet)', size: '100×100 ft (1/4 Acre)', price: 2400000, status: 'Sold – Fully Paid', buyer: 'Alice Muthoni' },
  { no: 'PLOT-GPR-015', project: 'Greenpark Ridge (Lanet)', size: '100×100 ft (1/4 Acre)', price: 2400000, status: 'Available', buyer: null },
  { no: 'PLOT-BG2-040', project: 'Bahati Gardens Phase 2', size: '50×100 ft (1/8 Acre)', price: 1200000, status: 'Title Issued', buyer: 'David Rotich' },
];

const INSTALMENT_SCHEDULES = [
  {
    buyer: 'Mary Achieng',
    phone: '+254755000050',
    plot: 'PLOT-BG2-041',
    plan: '12 Months Instalments',
    totalPrice: 1200000,
    paid: 600000,
    balance: 600000,
    nextDue: '25 Nov 2026',
    status: 'Current',
  },
  {
    buyer: 'Samuel Maina',
    phone: '+254722334455',
    plot: 'PLOT-BG2-042',
    plan: '6 Months Instalments',
    totalPrice: 1200000,
    paid: 800000,
    balance: 400000,
    nextDue: '20 Nov 2026',
    status: 'Current',
  },
  {
    buyer: 'Dr. Evans Kosgei',
    phone: '+254711223344',
    plot: 'PLOT-GPR-012',
    plan: 'Cash Sale',
    totalPrice: 2400000,
    paid: 2400000,
    balance: 0,
    nextDue: 'Fully Settled',
    status: 'Title Processing',
  },
];

const TITLE_CHECKLIST_STAGES = [
  '1. Land Search Done',
  '2. Seller KYC Verified',
  '3. Rates Clearance Certificate',
  '4. LCB Board Consent (Agricultural)',
  '5. Valuation for Stamp Duty',
  '6. Transfer Forms Executed',
  '7. Stamp Duty Assessed & Paid',
  '8. New Title Issued & Collected',
];

export default function LandSalesAgentDashboardPage() {
  const [activeTab, setActiveTab] = useState<'inventory' | 'instalments' | 'transfer'>('inventory');

  return (
    <div className="min-h-screen bg-[#f4f7f5] text-slate-900 pb-16">
      {/* Header */}
      <header className="border-b border-emerald-950/10 bg-[#102820] text-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-emerald-400 font-bold text-[#102820]">
              <Compass size={20} />
            </span>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold tracking-widest uppercase text-emerald-300">Land Sales & Projects Desk</span>
                <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-semibold text-emerald-200 ring-1 ring-emerald-400/30">
                  SRS Section 02 & 07 (Land Sales Agent)
                </span>
              </div>
              <h1 className="text-lg font-bold sm:text-xl">Land Projects Desk — Peter Kiprono</h1>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <span className="rounded-full bg-white/10 px-3 py-1 text-xs font-semibold text-emerald-100">
              Nakuru Branch (Land Stock)
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
        {/* KPI Cards */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <p className="text-xs font-bold uppercase tracking-wider text-slate-500">Active Land Projects</p>
            <p className="mt-2 text-3xl font-bold text-slate-900">2 Projects</p>
            <p className="mt-1 text-xs text-slate-500">Bahati Gardens & Greenpark Ridge</p>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <p className="text-xs font-bold uppercase tracking-wider text-slate-500">Available Plots</p>
            <p className="mt-2 text-3xl font-bold text-emerald-800">18 Plots</p>
            <p className="mt-1 text-xs text-slate-500">Ready for sale agreements</p>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <p className="text-xs font-bold uppercase tracking-wider text-slate-500">Monthly Sales Volume</p>
            <p className="mt-2 text-3xl font-bold text-slate-900">{currency.format(18500000)}</p>
            <p className="mt-1 text-xs text-emerald-700 font-semibold">14 Plots Sold / Paying</p>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <p className="text-xs font-bold uppercase tracking-wider text-slate-500">Agent Commission</p>
            <p className="mt-2 text-3xl font-bold text-emerald-950">{currency.format(370000)}</p>
            <p className="mt-1 text-xs text-slate-500">2% on cleared buyer deposits</p>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-200 gap-6 text-sm font-semibold text-slate-600">
          <button
            onClick={() => setActiveTab('inventory')}
            className={`pb-3 border-b-2 transition ${activeTab === 'inventory' ? 'border-emerald-800 text-emerald-900 font-bold' : 'border-transparent hover:text-slate-900'}`}
          >
            Plot Inventory Grid (SRS 07.1)
          </button>
          <button
            onClick={() => setActiveTab('instalments')}
            className={`pb-3 border-b-2 transition ${activeTab === 'instalments' ? 'border-emerald-800 text-emerald-900 font-bold' : 'border-transparent hover:text-slate-900'}`}
          >
            Buyer Instalment Tracker (SRS 07.3)
          </button>
          <button
            onClick={() => setActiveTab('transfer')}
            className={`pb-3 border-b-2 transition ${activeTab === 'transfer' ? 'border-emerald-800 text-emerald-900 font-bold' : 'border-transparent hover:text-slate-900'}`}
          >
            Due Diligence & Title Transfer (SRS 07.4)
          </button>
        </div>

        {/* Tab 1: Plot Inventory Grid */}
        {activeTab === 'inventory' && (
          <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs">
            <div className="flex items-center justify-between mb-6">
              <div>
                <h2 className="text-base font-bold text-slate-900">Subdivision Plot Availability Board</h2>
                <p className="text-xs text-slate-500">Track plots from listing to title issuance</p>
              </div>
              <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
                Bahati Gardens & Greenpark
              </span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
              {PLOTS.map((p) => (
                <div key={p.no} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs font-bold text-slate-800">{p.no}</span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                        p.status === 'Available'
                          ? 'bg-emerald-50 text-emerald-800 border border-emerald-200'
                          : p.status.includes('Paying')
                          ? 'bg-amber-50 text-amber-800 border border-amber-200'
                          : 'bg-purple-50 text-purple-800 border border-purple-200'
                      }`}
                    >
                      {p.status}
                    </span>
                  </div>
                  <p className="text-xs font-bold text-slate-900 mt-2">{p.project}</p>
                  <p className="text-xs text-slate-500">{p.size}</p>
                  <p className="mt-3 font-mono font-bold text-sm text-slate-900">{currency.format(p.price)}</p>
                  <p className="text-[11px] text-slate-500 mt-1 truncate">
                    {p.buyer ? `Buyer: ${p.buyer}` : 'Unreserved Plot'}
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Tab 2: Buyer Instalment Tracker */}
        {activeTab === 'instalments' && (
          <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs">
            <h2 className="text-base font-bold text-slate-900 mb-4">Buyer Instalment Payment Plans (SRS Section 07.3)</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm text-slate-700">
                <thead className="bg-slate-50 text-xs font-bold uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="px-4 py-3">Buyer Name</th>
                    <th className="px-4 py-3">Phone</th>
                    <th className="px-4 py-3">Plot Code</th>
                    <th className="px-4 py-3">Agreed Plan</th>
                    <th className="px-4 py-3">Total Price</th>
                    <th className="px-4 py-3">Paid to Date</th>
                    <th className="px-4 py-3">Remaining Balance</th>
                    <th className="px-4 py-3">Next Due</th>
                    <th className="px-4 py-3">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {INSTALMENT_SCHEDULES.map((s) => (
                    <tr key={s.plot} className="hover:bg-slate-50/50">
                      <td className="px-4 py-3.5 font-bold text-slate-900">{s.buyer}</td>
                      <td className="px-4 py-3.5 font-mono text-xs">{s.phone}</td>
                      <td className="px-4 py-3.5 font-mono font-bold text-xs">{s.plot}</td>
                      <td className="px-4 py-3.5 text-xs text-slate-600">{s.plan}</td>
                      <td className="px-4 py-3.5">{currency.format(s.totalPrice)}</td>
                      <td className="px-4 py-3.5 font-bold text-emerald-800">{currency.format(s.paid)}</td>
                      <td className="px-4 py-3.5 font-mono font-bold">{currency.format(s.balance)}</td>
                      <td className="px-4 py-3.5 text-xs">{s.nextDue}</td>
                      <td className="px-4 py-3.5">
                        <span className="rounded-full bg-emerald-50 text-emerald-800 px-2.5 py-0.5 text-xs font-bold">
                          {s.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Tab 3: Due Diligence & Transfer */}
        {activeTab === 'transfer' && (
          <div className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs max-w-3xl">
            <h2 className="text-base font-bold text-slate-900">8-Stage Due Diligence & Title Transfer Checklist (SRS 07.4)</h2>
            <p className="text-xs text-slate-500 mb-6">Sample active tracker for Plot #PLOT-GPR-012 (Dr. Evans Kosgei)</p>

            <div className="space-y-3">
              {TITLE_CHECKLIST_STAGES.map((stage, idx) => (
                <div
                  key={stage}
                  className={`flex items-center justify-between p-3.5 rounded-xl border ${idx < 6 ? 'bg-emerald-50/60 border-emerald-200 text-emerald-950' : idx === 6 ? 'bg-amber-50/70 border-amber-200 text-amber-950' : 'bg-slate-50 border-slate-200 text-slate-400'}`}
                >
                  <span className="text-sm font-semibold">{stage}</span>
                  <span
                    className={`rounded-full px-2.5 py-0.5 text-xs font-bold ${idx < 6 ? 'bg-emerald-200 text-emerald-900' : idx === 6 ? 'bg-amber-200 text-amber-900' : 'bg-slate-200 text-slate-600'}`}
                  >
                    {idx < 6 ? 'Completed' : idx === 6 ? 'In Progress' : 'Pending'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
