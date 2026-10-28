'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Home, ArrowRight, PlayCircle, CreditCard, Wrench, AlertTriangle, CheckCircle } from 'lucide-react';

type MoveInSettlement = { status: string; total_required: number; total_paid: number; outstanding_balance: number; deposit_amount: number; rent_amount: number; garbage_amount: number; water_deposit_amount: number };

type Onboarding = {
  fullName: string;
  tutorialCompleted: boolean;
  isMigrated: boolean;
  moveInSettlement: MoveInSettlement | null;
  transitionSettings: any;
};

const currency = new Intl.NumberFormat('en-KE', { style: 'currency', currency: 'KES', maximumFractionDigits: 0 });

const features = [
  { icon: CreditCard, label: 'Billing & Invoices', desc: 'View monthly rent, water, and garbage bills clearly, and pay your bills directly from the portal.' },
  { icon: CheckCircle, label: 'Track Payments', desc: 'See your payment history and outstanding balances instantly.' },
  { icon: Wrench, label: 'Maintenance Requests', desc: 'Log issues easily and track resolution progress.' },
];

export default function TenantWelcomePage() {
  const router = useRouter();
  const [onboarding, setOnboarding] = useState<Onboarding | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [hovered, setHovered] = useState<number | null>(null);

  useEffect(() => {
    fetch('/api/tenant/onboarding')
      .then((r) => r.json())
      .then((data) => setOnboarding(data))
      .finally(() => setLoading(false));
  }, []);

  const markComplete = async () => {
    await fetch('/api/tenant/onboarding', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tutorialCompleted: true }),
    });
  };

  const goToDashboard = async () => {
    setSaving(true);
    await markComplete();
    window.location.href = '/tenant/dashboard';
  };

  const goToTour = async () => {
    setSaving(true);
    await markComplete();
    window.location.href = '/tenant/dashboard?tour=true';
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-10 h-10 rounded-full border-2 border-emerald-500/30 border-t-emerald-500 animate-spin" />
          <p className="text-slate-500 text-sm font-medium">Setting up your tenant portal…</p>
        </div>
      </div>
    );
  }

  const isMigrated = onboarding?.isMigrated;
  const settlement = onboarding?.moveInSettlement;

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute top-[-10%] right-[-5%] w-[600px] h-[600px] bg-emerald-500/10 rounded-full blur-3xl" />
        <div className="absolute bottom-[-10%] left-[-5%] w-[400px] h-[400px] bg-blue-500/5 rounded-full blur-3xl" />
      </div>

      <div className="relative max-w-5xl mx-auto px-6 py-16">
        <div className="text-center mb-10">
          <div className="inline-flex items-center justify-center w-20 h-20 rounded-3xl bg-emerald-100 border border-emerald-200 mb-8 shadow-lg shadow-emerald-500/10">
            <Home className="text-emerald-600" size={40} />
          </div>
          <p className="text-emerald-600 font-bold uppercase tracking-[0.2em] text-sm mb-4">Welcome to your portal</p>
          <h1 className="text-4xl md:text-6xl font-black tracking-tight leading-tight text-slate-900">
            {onboarding?.fullName ? (
              <>Welcome, <span className="text-emerald-600">{onboarding.fullName.split(' ')[0]}</span>.</>
            ) : 'Welcome.'}
          </h1>
          <p className="mt-5 text-lg text-slate-600 max-w-2xl mx-auto leading-relaxed">
            You are now fully set up. Here is a quick overview of what you can manage from this portal.
          </p>
        </div>

        {/* Dynamic Messaging Box */}
        <div className="max-w-2xl mx-auto mb-14">
          {isMigrated ? (
            <div className="bg-amber-50 border border-amber-200 p-6 rounded-2xl flex gap-4">
              <AlertTriangle className="text-amber-500 shrink-0 mt-1" size={24} />
              <div>
                <h3 className="text-amber-900 font-bold text-lg mb-2">Transition Period Notice</h3>
                <p className="text-amber-800 text-sm leading-relaxed mb-3">
                  Your landlord is transitioning to this online platform. Any outstanding balances and the new invoices for 
                  <strong className="mx-1">{onboarding?.transitionSettings?.first_online_billing_month}</strong> 
                  will be published here.
                </p>
              </div>
            </div>
          ) : settlement ? (
            <div className="bg-white border border-slate-200 p-6 rounded-2xl shadow-sm">
              <h3 className="text-slate-900 font-bold text-lg mb-4 flex items-center gap-2">
                <CheckCircle className="text-emerald-500" size={20} /> Move-in Settlement Summary
              </h3>
              <div className="grid grid-cols-2 gap-4 text-sm mb-4">
                <div className="bg-slate-50 p-3 rounded-lg border border-slate-100">
                  <span className="text-slate-500 block text-xs">Security Deposit</span>
                  <strong className="text-slate-900 text-base">{currency.format(settlement.deposit_amount)}</strong>
                </div>
                <div className="bg-slate-50 p-3 rounded-lg border border-slate-100">
                  <span className="text-slate-500 block text-xs">Water Deposit</span>
                  <strong className="text-slate-900 text-base">{currency.format(settlement.water_deposit_amount)}</strong>
                </div>
                <div className="bg-slate-50 p-3 rounded-lg border border-slate-100">
                  <span className="text-slate-500 block text-xs">Rent</span>
                  <strong className="text-slate-900 text-base">{currency.format(settlement.rent_amount)}</strong>
                </div>
                <div className="bg-slate-50 p-3 rounded-lg border border-slate-100">
                  <span className="text-slate-500 block text-xs">Garbage</span>
                  <strong className="text-slate-900 text-base">{currency.format(settlement.garbage_amount)}</strong>
                </div>
              </div>
              <div className="flex justify-between items-center border-t border-slate-100 pt-4">
                <span className="text-slate-500">Total Required:</span>
                <strong className="text-lg text-emerald-600">{currency.format(settlement.total_required)}</strong>
              </div>
            </div>
          ) : null}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-14">
          {features.map((f, i) => {
            const Icon = f.icon;
            return (
              <div key={f.label}
                onMouseEnter={() => setHovered(i)}
                onMouseLeave={() => setHovered(null)}
                className={`group relative rounded-2xl border p-5 transition-all duration-300 cursor-default ${
                  hovered === i
                    ? 'border-emerald-300 bg-emerald-50/50 shadow-lg shadow-emerald-900/5 -translate-y-0.5'
                    : 'border-slate-200 bg-white hover:border-slate-300 shadow-sm'
                }`}>
                <div className={`w-10 h-10 rounded-xl flex items-center justify-center mb-3 transition-all ${
                  hovered === i ? 'bg-emerald-100 border border-emerald-200' : 'bg-slate-50 border border-slate-100'
                }`}>
                  <Icon size={20} className={hovered === i ? 'text-emerald-600' : 'text-slate-500'} />
                </div>
                <p className="font-bold text-sm text-slate-900">{f.label}</p>
                <p className="text-xs text-slate-500 mt-1 leading-relaxed">{f.desc}</p>
              </div>
            );
          })}
        </div>

        {/* CTAs */}
        <div className="grid md:grid-cols-2 gap-4 max-w-2xl mx-auto">
          <button onClick={goToTour} disabled={saving}
            className="group relative flex items-center justify-between rounded-2xl border border-emerald-300 bg-emerald-50 p-6 text-left hover:bg-emerald-100/50 hover:border-emerald-400 transition-all duration-200 shadow-sm">
            <div>
              <div className="flex items-center gap-2 mb-2">
                <PlayCircle className="text-emerald-600" size={22} />
                <span className="text-xs font-bold text-emerald-600 uppercase tracking-wider">Recommended</span>
              </div>
              <strong className="block text-lg text-slate-900">Take the guided tour</strong>
              <span className="mt-1 block text-sm text-slate-600">Learn how to read your invoices and pay bills.</span>
            </div>
            <ArrowRight className="text-emerald-600 group-hover:translate-x-1 transition-transform" size={20} />
          </button>

          <button onClick={goToDashboard} disabled={saving}
            className="group flex items-center justify-between rounded-2xl border border-slate-200 bg-white p-6 text-left hover:border-slate-300 hover:bg-slate-50 transition-all duration-200 shadow-sm">
            <div>
              <div className="flex items-center gap-2 mb-2">
                <Home className="text-slate-500" size={22} />
              </div>
              <strong className="block text-lg text-slate-900">Go to my dashboard</strong>
              <span className="mt-1 block text-sm text-slate-500">You can start the tour anytime from the dashboard.</span>
            </div>
            <ArrowRight className="text-slate-500 group-hover:translate-x-1 transition-transform" size={20} />
          </button>
        </div>
      </div>
    </div>
  );
}
