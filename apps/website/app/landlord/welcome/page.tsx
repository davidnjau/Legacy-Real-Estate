'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Building2, ArrowRight, PlayCircle, LayoutDashboard,
  Home, FileText, Wallet, Wrench, Users, FolderUp,
} from 'lucide-react';

type Onboarding = {
  fullName: string;
  propertyName: string;
  onboardingCompleted: boolean;
  tutorialCompleted: boolean;
};

const features = [
  { icon: FolderUp,      label: 'Migration Setup',    desc: 'Import all your existing units and tenants in one go.' },
  { icon: Home,          label: 'Units & Tenants',     desc: 'Manage rooms, generate claim codes, and track occupancy.' },
  { icon: FileText,      label: 'Billing Wizard',      desc: 'Generate and publish monthly invoices with water, rent & garbage.' },
  { icon: Wallet,        label: 'Financial Ledger',    desc: 'Track every payment, balance, and arrear at a glance.' },
  { icon: Wrench,        label: 'Maintenance',         desc: 'Let tenants report issues and manage resolutions here.' },
  { icon: Users,         label: 'Staff & Caretakers',  desc: 'Invite caretakers and manage their access.' },
];

export default function LandlordWelcomePage() {
  const router = useRouter();
  const [onboarding, setOnboarding] = useState<Onboarding | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [hovered, setHovered] = useState<number | null>(null);

  useEffect(() => {
    fetch('/api/landlord/onboarding')
      .then((r) => r.json())
      .then((data) => setOnboarding(data))
      .finally(() => setLoading(false));
  }, []);

  const goToDashboard = async () => {
    setSaving(true);
    await fetch('/api/landlord/onboarding', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tutorialCompleted: false }),
    });
    window.location.href = '/landlord/dashboard';
  };

  const goToTour = async () => {
    setSaving(true);
    await fetch('/api/landlord/onboarding', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tutorialCompleted: false }),
    });
    window.location.assign('/landlord/dashboard?tour=true');
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-10 h-10 rounded-full border-2 border-emerald-500/30 border-t-emerald-500 animate-spin" />
          <p className="text-slate-500 text-sm font-medium">Setting up your workspace…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      {/* Ambient glows */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute top-[-10%] right-[-5%] w-[600px] h-[600px] bg-emerald-500/10 rounded-full blur-3xl" />
        <div className="absolute bottom-[-10%] left-[-5%] w-[400px] h-[400px] bg-blue-500/5 rounded-full blur-3xl" />
      </div>

      <div className="relative max-w-5xl mx-auto px-6 py-16">
        {/* Hero */}
        <div className="text-center mb-16">
          <div className="inline-flex items-center justify-center w-20 h-20 rounded-3xl bg-emerald-100 border border-emerald-200 mb-8 shadow-lg shadow-emerald-500/10">
            <Building2 className="text-emerald-600" size={40} />
          </div>
          <p className="text-emerald-600 font-bold uppercase tracking-[0.2em] text-sm mb-4">Welcome to your portal</p>
          <h1 className="text-4xl md:text-6xl font-black tracking-tight leading-tight text-slate-900">
            {onboarding?.fullName ? (
              <>Welcome, <span className="text-emerald-600">{onboarding.fullName.split(' ')[0]}</span>.</>
            ) : 'Welcome.'}
          </h1>
          <p className="mt-5 text-lg text-slate-600 max-w-2xl mx-auto leading-relaxed">
            <strong className="text-slate-900">{onboarding?.propertyName || 'Your property'}</strong> is now set up on the platform.
            Here's everything you can manage from this portal.
          </p>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-14">
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
              <span className="mt-1 block text-sm text-slate-600">Learn the workflow in a few short steps.</span>
            </div>
            <ArrowRight className="text-emerald-600 group-hover:translate-x-1 transition-transform" size={20} />
          </button>

          <button onClick={goToDashboard} disabled={saving}
            className="group flex items-center justify-between rounded-2xl border border-slate-200 bg-white p-6 text-left hover:border-slate-300 hover:bg-slate-50 transition-all duration-200 shadow-sm">
            <div>
              <div className="flex items-center gap-2 mb-2">
                <LayoutDashboard className="text-slate-500" size={22} />
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
