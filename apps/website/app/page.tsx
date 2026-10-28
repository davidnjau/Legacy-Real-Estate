import Link from 'next/link';
import { ArrowRight, Building2, CheckCircle2, ClipboardList, Droplets, ShieldCheck, Wallet, Wrench } from 'lucide-react';

const features = [
  { icon: Wallet, title: 'Clear rent tracking', description: 'Review monthly invoices, collections, balances, and arrears across your property.' },
  { icon: Droplets, title: 'Water and utility billing', description: 'Record meter readings and include recurring charges in tenant invoices.' },
  { icon: Wrench, title: 'Maintenance coordination', description: 'Give tenants a clear way to report issues and help caretakers follow them through.' },
  { icon: ClipboardList, title: 'One property record', description: 'Keep tenant, unit, payment, move-in, and move-out information together.' },
];

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-white text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <nav className="mx-auto flex max-w-7xl items-center justify-between px-6 py-5">
          <Link href="/" className="flex items-center gap-2 text-xl font-bold tracking-tight">
            <Building2 className="text-emerald-600" size={27} /> Rental Management
          </Link>
          <div className="flex items-center gap-3">
            <Link href="/login" className="rounded-lg px-4 py-2 text-sm font-semibold text-slate-700 hover:text-emerald-700">Sign in</Link>
            <Link href="/landlord-signup" className="rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-bold text-white hover:bg-emerald-700">Get started</Link>
          </div>
        </nav>
      </header>

      <section className="bg-slate-950 px-6 py-24 text-white md:py-32">
        <div className="mx-auto grid max-w-7xl gap-14 md:grid-cols-[1.1fr_0.9fr] md:items-center">
          <div>
            <p className="mb-5 inline-flex items-center gap-2 rounded-full border border-emerald-400/30 bg-emerald-400/10 px-4 py-2 text-sm font-semibold text-emerald-300">
              Property operations, made clearer
            </p>
            <h1 className="max-w-3xl text-4xl font-extrabold leading-tight tracking-tight md:text-6xl">
              Manage rentals with confidence, from move-in to monthly reporting.
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-relaxed text-slate-300">
              Bring landlords, caretakers, and tenants into one practical workflow for rent, utilities, maintenance, and property communication.
            </p>
            <div className="mt-9 flex flex-wrap gap-4">
              <Link href="/landlord-signup" className="inline-flex items-center gap-2 rounded-xl bg-emerald-500 px-6 py-3.5 font-bold text-white hover:bg-emerald-400">
                Create a landlord account <ArrowRight size={18} />
              </Link>
              <Link href="/login" className="rounded-xl border border-slate-600 px-6 py-3.5 font-bold text-white hover:bg-slate-900">Sign in to your portal</Link>
            </div>
          </div>
          <div className="rounded-3xl border border-white/10 bg-white/5 p-7 shadow-2xl">
            <p className="text-sm font-bold uppercase tracking-[0.18em] text-emerald-300">Designed for the whole team</p>
            <div className="mt-6 space-y-5">
              {[
                ['Landlords', 'See property performance, balances, staff, and operational activity.'],
                ['Caretakers', 'Manage tenant records, readings, maintenance, and announcements.'],
                ['Tenants', 'View bills and due dates, make payments, and report issues.'],
              ].map(([title, description]) => (
                <div key={title} className="flex gap-4 border-b border-white/10 pb-5 last:border-0 last:pb-0">
                  <CheckCircle2 className="mt-0.5 shrink-0 text-emerald-400" size={20} />
                  <div><h2 className="font-bold">{title}</h2><p className="mt-1 text-sm leading-relaxed text-slate-300">{description}</p></div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="px-6 py-20 md:py-24">
        <div className="mx-auto max-w-7xl">
          <div className="mx-auto max-w-3xl text-center">
            <p className="font-bold uppercase tracking-widest text-emerald-700">The challenge</p>
            <h2 className="mt-3 text-3xl font-extrabold tracking-tight md:text-4xl">Less chasing information. More control of the work.</h2>
            <p className="mt-5 leading-relaxed text-slate-600">Rental operations can get fragmented across spreadsheets, messages, paper receipts, and separate payment records. This platform gives each role a shared, property-scoped place to do its work and keeps important activity visible to the owner.</p>
          </div>
          <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            {features.map(({ icon: Icon, title, description }) => (
              <article key={title} className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                <div className="mb-5 inline-flex rounded-xl bg-emerald-50 p-3 text-emerald-700"><Icon size={22} /></div>
                <h3 className="font-bold">{title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-slate-600">{description}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="bg-slate-50 px-6 py-20">
        <div className="mx-auto max-w-7xl">
          <div className="max-w-3xl">
            <p className="font-bold uppercase tracking-widest text-emerald-700">A role for everyone</p>
            <h2 className="mt-3 text-3xl font-extrabold tracking-tight md:text-4xl">Property oversight without getting in the caretaker’s way.</h2>
          </div>
          <div className="mt-10 grid gap-5 md:grid-cols-3">
            {[
              ['Landlord portal', 'Oversee property activity, review rent analysis and ledgers, manage caretaker access, and keep an eye on maintenance and move-in/out settlements.'],
              ['Caretaker portal', 'Handle day-to-day operations: tenant records, monthly billing preparation, water readings, rent collection updates, maintenance requests, and announcements.'],
              ['Tenant portal', 'Check current and past bills, see payment deadlines, follow account balances, make payments, and submit maintenance requests.'],
            ].map(([title, description]) => (
              <article key={title} className="rounded-2xl bg-white p-7 shadow-sm ring-1 ring-slate-200">
                <h3 className="text-xl font-bold">{title}</h3>
                <p className="mt-3 text-sm leading-relaxed text-slate-600">{description}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="px-6 py-20">
        <div className="mx-auto max-w-7xl">
          <div className="mx-auto max-w-3xl text-center">
            <p className="font-bold uppercase tracking-widest text-emerald-700">Plans</p>
            <h2 className="mt-3 text-3xl font-extrabold tracking-tight md:text-4xl">A straightforward setup for properties of different sizes.</h2>
            <p className="mt-4 text-slate-600">Start with a property, configure its units and charges, then add the people who need access. Plan pricing can be aligned to your portfolio size.</p>
          </div>
          <div className="mt-10 grid gap-5 md:grid-cols-3">
            {[
              ['Single property', 'For an owner getting one apartment organized.'],
              ['Growing portfolio', 'For owners managing more units and a broader team.'],
              ['Multi-property', 'For landlords who oversee several properties.'],
            ].map(([title, description]) => (
              <article key={title} className="rounded-2xl border border-slate-200 p-7">
                <h3 className="text-lg font-bold">{title}</h3>
                <p className="mt-3 min-h-12 text-sm leading-relaxed text-slate-600">{description}</p>
                <Link href="/landlord-signup" className="mt-6 inline-flex items-center gap-2 font-bold text-emerald-700 hover:text-emerald-800">Get started <ArrowRight size={16} /></Link>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="bg-emerald-700 px-6 py-16 text-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-7 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="flex items-center gap-2 text-emerald-100"><ShieldCheck size={20} /> Built around clear responsibilities</div>
            <h2 className="mt-3 text-3xl font-extrabold">Make rental operations easier to follow.</h2>
            <p className="mt-3 max-w-2xl text-emerald-50">Give your team the right tools and keep the essential records in view.</p>
          </div>
          <Link href="/landlord-signup" className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-white px-6 py-3.5 font-bold text-emerald-800 hover:bg-emerald-50">Create an account <ArrowRight size={18} /></Link>
        </div>
      </section>

      <footer className="border-t border-slate-200 px-6 py-8 text-center text-sm text-slate-500">
        <p>Rental Management · Tools for landlords, caretakers, and tenants</p>
      </footer>
    </main>
  );
}
