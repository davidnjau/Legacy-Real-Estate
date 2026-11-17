'use client';

import { useState } from 'react';
import Link from 'next/link';
import {
  Building2,
  Compass,
  CheckCircle2,
  Clock,
  Receipt,
  Download,
  Smartphone,
  MapPin,
  Calendar,
  AlertCircle,
  FileCheck,
  Check,
  X,
  CreditCard,
  ShieldCheck,
} from 'lucide-react';

/* ─── Currency Formatter ─── */
const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

/* ─── Seed Data for Mary Achieng ─── */
const RECEIPTS = [
  { ref: 'RCP-PLT-0912', mpesa: 'SK82QPL819', amount: 100000, date: '25 Oct 2026', for: 'Instalment #3 of 12' },
  { ref: 'RCP-PLT-0840', mpesa: 'SJ71MNB302', amount: 100000, date: '25 Sep 2026', for: 'Instalment #2 of 12' },
  { ref: 'RCP-PLT-0711', mpesa: 'SI60KZA110', amount: 400000, date: '15 Aug 2026', for: 'Booking Fee & 30% Deposit' },
];

const TRANSFER_STEPS = [
  { step: 1, name: 'Official Land Search', desc: 'Title verified free of encumbrance', status: 'Completed', date: '16 Aug 2026' },
  { step: 2, name: 'Seller KYC & Spousal Consent', desc: 'Vendor documents verified', status: 'Completed', date: '20 Aug 2026' },
  { step: 3, name: 'County Rates & Rent Clearance', desc: 'Zero rates arrears certified', status: 'Completed', date: '12 Sep 2026' },
  { step: 4, name: 'Land Control Board (LCB) Consent', desc: 'Sub-county LCB consent approved', status: 'Completed', date: '15 Oct 2026' },
  { step: 5, name: 'Valuation for Stamp Duty', desc: 'Government valuer inspection', status: 'In Progress', date: 'Expected Nov 2026' },
  { step: 6, name: 'Transfer Forms Executed', desc: 'Buyer & Seller sign transfer deeds', status: 'Pending', date: 'Awaiting full payment' },
  { step: 7, name: 'Stamp Duty Assessed & Paid', desc: 'Assessment via Ardhisasa', status: 'Pending', date: '—' },
  { step: 8, name: 'New Title Deed Issuance', desc: 'Title collected at agency office', status: 'Pending', date: '—' },
];

export default function BuyerDashboardPage() {
  const [stkModal, setStkModal] = useState(false);
  const [stkSuccess, setStkSuccess] = useState(false);

  const handleSimulatePayment = () => {
    setStkSuccess(true);
    setTimeout(() => {
      setStkModal(false);
      setStkSuccess(false);
    }, 2500);
  };

  return (
    <div className="min-h-screen bg-[#f4f7f5] text-slate-900 pb-16">
      {/* Top Banner */}
      <header className="border-b border-emerald-950/10 bg-[#102820] text-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-emerald-400 font-bold text-[#102820]">
              <Compass size={20} />
            </span>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold tracking-widest uppercase text-emerald-300">Land Buyer Workspace</span>
                <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-semibold text-emerald-200 ring-1 ring-emerald-400/30">
                  SRS Section 02 & 07 (Buyer Portal)
                </span>
              </div>
              <h1 className="text-lg font-bold sm:text-xl">Plot Ownership Portal — Mary Achieng</h1>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <span className="rounded-full bg-white/10 px-3 py-1 text-xs font-semibold text-emerald-100">
              Bahati Gardens Phase 2 (Plot #041)
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

      {/* Main Container */}
      <main className="mx-auto max-w-7xl px-4 pt-8 sm:px-6 lg:px-8 space-y-8">
        {/* Prominent M-Pesa Payment Instructions Banner (SRS Section 07.3) */}
        <section className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-[#123b2c] to-[#0a231a] p-6 text-white shadow-md">
          <div className="flex flex-wrap items-center justify-between gap-6">
            <div>
              <span className="rounded-full bg-emerald-400/20 px-2.5 py-0.5 text-xs font-bold text-emerald-300 ring-1 ring-emerald-400/40">
                Official M-Pesa Instalment Instructions
              </span>
              <h2 className="mt-2 text-2xl font-bold tracking-tight">How to Pay Your Land Instalment</h2>
              <p className="mt-1 text-xs text-emerald-100/80">
                Use our Safaricom Daraja C2B Paybill. Enter your Plot Code as the Account Number for instant SMS receipt.
              </p>

              <div className="mt-4 flex flex-wrap items-center gap-6">
                <div className="rounded-xl bg-white/10 px-4 py-2 border border-white/10">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-emerald-200">Business Paybill</p>
                  <p className="font-mono text-xl font-black text-white">522123</p>
                </div>
                <div className="rounded-xl bg-white/10 px-4 py-2 border border-white/10">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-emerald-200">Account Number (Plot Code)</p>
                  <p className="font-mono text-xl font-black text-emerald-300">PLOT-BG2-041</p>
                </div>
              </div>
            </div>

            <button
              onClick={() => setStkModal(true)}
              className="inline-flex items-center gap-2 rounded-xl bg-emerald-400 px-5 py-3 text-sm font-bold text-slate-950 shadow-lg shadow-emerald-950/20 transition hover:bg-emerald-300"
            >
              <Smartphone size={17} /> Lipa na M-Pesa Prompt
            </button>
          </div>
        </section>

        {/* Top Plot Summary Cards */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <span className="text-xs font-bold uppercase tracking-wider text-slate-500">Purchased Parcel</span>
            <p className="mt-2 text-2xl font-bold text-slate-900">PLOT-BG2-041</p>
            <p className="mt-1 text-xs text-slate-500">Bahati Gardens Phase 2 (Nakuru)</p>
            <span className="mt-3 inline-block rounded-md bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-800">
              Size: 50×100 ft (1/8 Acre)
            </span>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <span className="text-xs font-bold uppercase tracking-wider text-slate-500">Agreed Price</span>
            <p className="mt-2 text-2xl font-bold text-slate-900">{currency.format(1200000)}</p>
            <p className="mt-1 text-xs text-slate-500">12 Months Instalment Plan</p>
            <span className="mt-3 inline-block rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-700">
              Deposit Paid: {currency.format(400000)}
            </span>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <span className="text-xs font-bold uppercase tracking-wider text-slate-500">Remaining Balance</span>
            <p className="mt-2 text-2xl font-bold text-amber-700">{currency.format(600000)}</p>
            <p className="mt-1 text-xs text-slate-500">50% Cleared (6 of 12 months)</p>
            <span className="mt-3 inline-block rounded-md bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-800">
              Next: KES 100k due 25 Nov
            </span>
          </div>

          <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
            <span className="text-xs font-bold uppercase tracking-wider text-slate-500">Title Processing Stage</span>
            <p className="mt-2 text-xl font-bold text-purple-900">Stage 5 of 8</p>
            <p className="mt-1 text-xs text-slate-500">Valuation for Stamp Duty</p>
            <span className="mt-3 inline-block rounded-md bg-purple-50 px-2 py-0.5 text-[11px] font-semibold text-purple-800">
              LCB Consent Approved
            </span>
          </div>
        </div>

        {/* 8-Stage Title Transfer Tracker (SRS Section 07.4) */}
        <section className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h3 className="text-base font-bold text-slate-900">Title Deed Transfer Tracker (SRS Section 07.4)</h3>
              <p className="text-xs text-slate-500">
                Track your legal land documentation step-by-step from official search to title deed handover
              </p>
            </div>
            <span className="rounded-full bg-emerald-100 text-emerald-900 px-3 py-1 text-xs font-bold flex items-center gap-1.5">
              <ShieldCheck size={14} /> Legal Due Diligence
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            {TRANSFER_STEPS.map((s) => (
              <div
                key={s.step}
                className={`rounded-xl border p-4 transition ${
                  s.status === 'Completed'
                    ? 'border-emerald-200 bg-emerald-50/50'
                    : s.status === 'In Progress'
                    ? 'border-amber-200 bg-amber-50/60 ring-2 ring-amber-400/40'
                    : 'border-slate-200 bg-slate-50/50 opacity-60'
                }`}
              >
                <div className="flex items-center justify-between text-xs">
                  <span className="font-bold text-slate-500">Stage {s.step}</span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                      s.status === 'Completed'
                        ? 'bg-emerald-200 text-emerald-900'
                        : s.status === 'In Progress'
                        ? 'bg-amber-200 text-amber-900'
                        : 'bg-slate-200 text-slate-600'
                    }`}
                  >
                    {s.status}
                  </span>
                </div>
                <h4 className="font-bold text-slate-900 text-sm mt-2">{s.name}</h4>
                <p className="text-xs text-slate-600 mt-1">{s.desc}</p>
                <p className="text-[11px] font-semibold text-slate-400 mt-3">{s.date}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Payment History & Receipts */}
        <section className="rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-base font-bold text-slate-900">Payment Receipts Ledger</h3>
              <p className="text-xs text-slate-500">All cleared instalments receipted with Safaricom Daraja confirmation</p>
            </div>
            <span className="font-mono text-xs text-slate-500">Total Settled: {currency.format(600000)}</span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-slate-700">
              <thead className="bg-slate-50 text-xs font-bold uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="px-4 py-3">Receipt #</th>
                  <th className="px-4 py-3">M-Pesa Code</th>
                  <th className="px-4 py-3">Description</th>
                  <th className="px-4 py-3">Date</th>
                  <th className="px-4 py-3">Amount</th>
                  <th className="px-4 py-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {RECEIPTS.map((r) => (
                  <tr key={r.ref} className="hover:bg-slate-50/50">
                    <td className="px-4 py-3.5 font-mono font-bold text-slate-900">{r.ref}</td>
                    <td className="px-4 py-3.5 font-mono text-xs text-emerald-800 bg-emerald-50 px-2 py-0.5 rounded w-fit">
                      {r.mpesa}
                    </td>
                    <td className="px-4 py-3.5 text-slate-600">{r.for}</td>
                    <td className="px-4 py-3.5 text-xs text-slate-500">{r.date}</td>
                    <td className="px-4 py-3.5 font-bold text-slate-900">{currency.format(r.amount)}</td>
                    <td className="px-4 py-3.5 text-right">
                      <button
                        disabled
                        className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-400 cursor-not-allowed"
                      >
                        <Download size={13} /> PDF
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </main>

      {/* STK Push Simulator Modal */}
      {stkModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
              <h3 className="font-bold text-slate-900">Lipa na M-Pesa Online (STK Push)</h3>
              <button onClick={() => setStkModal(false)} className="text-slate-400 hover:text-slate-700">
                <X size={18} />
              </button>
            </div>

            {stkSuccess ? (
              <div className="py-8 text-center">
                <CheckCircle2 size={40} className="mx-auto text-emerald-600 mb-2" />
                <h4 className="font-bold text-slate-900">Payment Received & Matched!</h4>
                <p className="text-xs text-slate-500 mt-1">Transaction SK93PQR811 confirmed. Receipt sent to phone.</p>
              </div>
            ) : (
              <div className="py-4 space-y-4">
                <p className="text-xs text-slate-600">
                  A prompt will be sent to your phone <span className="font-mono font-bold">+254 755 *** 050</span> to pay KES 100,000 for Plot PLOT-BG2-041.
                </p>
                <div className="rounded-xl bg-slate-50 p-3 text-xs font-mono space-y-1">
                  <p>Paybill: <strong>522123</strong></p>
                  <p>Account: <strong>PLOT-BG2-041</strong></p>
                  <p>Amount: <strong>KES 100,000</strong></p>
                </div>
                <button
                  onClick={handleSimulatePayment}
                  className="w-full rounded-xl bg-emerald-800 py-3 text-sm font-bold text-white hover:bg-emerald-900"
                >
                  Confirm & Send Prompt
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
