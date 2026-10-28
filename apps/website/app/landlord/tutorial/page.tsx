'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowLeft, ArrowRight, CheckCircle2 } from 'lucide-react';

const steps = [
  { title: 'Start with your units', text: 'Add your rooms and property details from Units & Tenants, or bring an existing spreadsheet through Migration Setup.', href: '/landlord/units' },
  { title: 'Connect your tenants', text: 'Invite existing tenants to activate their accounts, or create a move-in settlement for a new tenant.', href: '/landlord/units' },
  { title: 'Capture water readings', text: 'Your caretaker records monthly readings so each bill reflects actual consumption.', href: '/caretaker/water-readings' },
  { title: 'Prepare monthly bills', text: 'The Billing Wizard calculates rent, garbage, water, and carried arrears before you publish invoices.', href: '/landlord/billing' },
  { title: 'Track the ledger', text: 'Use the Financial Ledger to follow collections, balances, and move-in settlements over time.', href: '/landlord/ledger' },
  { title: 'Keep operations moving', text: 'Tenants can report maintenance issues, while you manage staff and caretakers from the portal.', href: '/landlord/maintenance' },
];

export default function LandlordTutorialPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [saving, setSaving] = useState(false);
  const current = steps[step];

  const finish = async () => {
    setSaving(true);
    await fetch('/api/landlord/onboarding', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tutorialCompleted: true }) });
    router.push('/landlord/dashboard');
    router.refresh();
  };

  return (
    <div className="max-w-3xl mx-auto py-8">
      <div className="flex items-center justify-between gap-4">
        <div><p className="text-sm font-semibold uppercase tracking-[0.18em] text-emerald-600">Guided tour</p><h1 className="mt-2 text-3xl font-bold text-slate-900">Your monthly rental workflow</h1></div>
        <span className="text-sm font-medium text-slate-500">{step + 1} of {steps.length}</span>
      </div>
      <div className="mt-8 h-2 rounded-full bg-slate-200"><div className="h-2 rounded-full bg-emerald-600 transition-all" style={{ width: `${((step + 1) / steps.length) * 100}%` }} /></div>
      <section className="mt-10 rounded-3xl border border-slate-200 bg-white p-8 md:p-12 shadow-sm">
        <CheckCircle2 className="text-emerald-600" size={38} />
        <h2 className="mt-6 text-2xl font-bold text-slate-900">{current.title}</h2>
        <p className="mt-4 text-lg leading-8 text-slate-600">{current.text}</p>
        <button onClick={() => router.push(current.href)} className="mt-8 text-sm font-bold text-emerald-700 hover:text-emerald-800">Open this area <ArrowRight className="ml-1 inline" size={16} /></button>
      </section>
      <div className="mt-6 flex items-center justify-between">
        <button onClick={() => step > 0 && setStep(step - 1)} disabled={step === 0 || saving} className="flex items-center gap-2 rounded-lg px-4 py-2 font-semibold text-slate-600 disabled:opacity-40"><ArrowLeft size={18} /> Back</button>
        {step < steps.length - 1 ? <button onClick={() => setStep(step + 1)} disabled={saving} className="flex items-center gap-2 rounded-lg bg-emerald-600 px-5 py-3 font-bold text-white hover:bg-emerald-700">Next <ArrowRight size={18} /></button> : <button onClick={finish} disabled={saving} className="rounded-lg bg-emerald-600 px-5 py-3 font-bold text-white hover:bg-emerald-700">{saving ? 'Saving...' : 'Finish tutorial'}</button>}
      </div>
    </div>
  );
}
