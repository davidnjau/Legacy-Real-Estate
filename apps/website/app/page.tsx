import Link from 'next/link';
import ListingSearch from '../components/landing/ListingSearch';
import {
  ArrowRight,
  BadgeCheck,
  Building2,
  Check,
  ChevronRight,
  CircleDollarSign,
  ClipboardList,
  House,
  KeyRound,
  MapPin,
  ShieldCheck,
  Sparkles,
  Users,
  Wrench,
} from 'lucide-react';

const roles = [
  {
    name: 'Property owners',
    eyebrow: 'OWNER PORTAL',
    description: 'See your portfolio, occupancy, rent performance, expenses and property activity in one clear view.',
    icon: Building2,
    tone: 'emerald',
    features: ['Portfolio overview', 'Property and unit records', 'Rental performance'],
  },
  {
    name: 'Residents',
    eyebrow: 'RESIDENT PORTAL',
    description: 'Keep your rental account close at hand, from invoices and payments to property updates.',
    icon: House,
    tone: 'blue',
    features: ['Bills and payment history', 'Property updates', 'Home and account details'],
  },
  {
    name: 'Caretakers',
    eyebrow: 'PROPERTY TEAM',
    description: 'Access assigned properties, daily tasks and on-site operations from a mobile-friendly workspace.',
    icon: KeyRound,
    tone: 'blue',
    features: ['Assigned properties', 'Daily task list', 'On-site operations'],
  },
];

const capabilities = [
  {
    icon: Building2,
    title: 'Know your portfolio',
    description: 'Keep properties, units, ownership and occupancy information together in a dependable workspace.',
  },
  {
    icon: CircleDollarSign,
    title: 'Bring rental finances into focus',
    description: 'Follow invoices, collections, balances and deposits with records designed for accountability.',
  },
  {
    icon: Wrench,
    title: 'Coordinate property operations',
    description: 'Give teams a clear place to organize maintenance, resident needs and day-to-day property work.',
  },
];

function ProductPreview() {
  return (
    <div className="relative mx-auto w-full max-w-[540px]">
      <div className="absolute -inset-6 rounded-[2.5rem] bg-emerald-300/10 blur-2xl" />
      <div className="relative overflow-hidden rounded-[1.7rem] border border-white/15 bg-[#f7faf8] p-3 shadow-2xl shadow-black/30 sm:p-4">
        <div className="flex items-center justify-between border-b border-slate-200 px-2 pb-3">
          <div className="flex items-center gap-2.5">
            <span className="grid size-8 place-items-center rounded-lg bg-[#123b2c] text-emerald-200">
              <Building2 size={17} />
            </span>
            <div>
              <p className="text-[11px] font-bold text-slate-900">Portfolio overview</p>
              <p className="text-[9px] text-slate-500">Owner workspace</p>
            </div>
          </div>
          <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-[9px] font-semibold text-emerald-800">
            Product preview
          </span>
        </div>

        <div className="p-2 pt-4 sm:p-3">
          <div className="flex items-end justify-between gap-3">
            <div>
              <p className="text-[10px] font-medium text-slate-500">Good morning</p>
              <p className="mt-1 text-lg font-semibold tracking-tight text-slate-950 sm:text-xl">Your properties, at a glance.</p>
            </div>
            <span className="mb-1 inline-flex items-center gap-1 text-[9px] font-semibold text-slate-500">
              <MapPin size={11} /> Kenya
            </span>
          </div>

          <div className="mt-4 grid grid-cols-3 gap-2">
            {[
              ['Properties', 'Portfolio'],
              ['Occupancy', 'At a glance'],
              ['Collections', 'By period'],
            ].map(([label, detail], index) => (
              <div key={label} className="rounded-xl border border-slate-200 bg-white p-3">
                <p className="text-[9px] font-medium text-slate-500">{label}</p>
                <div className="mt-2 flex items-center gap-1.5">
                  <span className={`size-2 rounded-full ${index === 0 ? 'bg-emerald-500' : index === 1 ? 'bg-blue-500' : 'bg-amber-400'}`} />
                  <p className="text-[10px] font-semibold text-slate-800">{detail}</p>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-3 grid gap-3 sm:grid-cols-[1.2fr_0.8fr]">
            <div className="rounded-xl border border-slate-200 bg-white p-3">
              <div className="flex items-center justify-between">
                <p className="text-[10px] font-semibold text-slate-800">Property activity</p>
                <span className="text-[9px] text-slate-400">Overview</span>
              </div>
              <div className="mt-4 flex h-20 items-end gap-2 border-b border-slate-100 px-1">
                {[35, 55, 42, 75, 58, 88, 66, 96, 72, 83, 61, 93].map((height, index) => (
                  <span
                    key={`${height}-${index}`}
                    className={`flex-1 rounded-t-sm ${index > 8 ? 'bg-emerald-700' : 'bg-emerald-200'}`}
                    style={{ height: `${height}%` }}
                  />
                ))}
              </div>
              <div className="mt-2 flex justify-between text-[8px] text-slate-400">
                <span>Portfolio records</span><span>Illustrative preview</span>
              </div>
            </div>
            <div className="rounded-xl bg-[#123b2c] p-3 text-white">
              <div className="flex size-8 items-center justify-center rounded-lg bg-white/10 text-emerald-200">
                <ShieldCheck size={16} />
              </div>
              <p className="mt-4 text-[10px] font-semibold">One connected workspace</p>
              <p className="mt-1 text-[9px] leading-4 text-emerald-50/70">
                Role-aware access for owners, residents and property teams.
              </p>
              <div className="mt-3 flex items-center gap-1 text-[9px] font-medium text-emerald-200">
                <BadgeCheck size={12} /> Built for clarity
              </div>
            </div>
          </div>

          <div className="mt-3 flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2.5">
            <span className="grid size-7 place-items-center rounded-lg bg-amber-50 text-amber-700"><ClipboardList size={15} /></span>
            <div className="min-w-0 flex-1">
              <p className="text-[9px] font-semibold text-slate-800">Property operations</p>
              <p className="mt-0.5 truncate text-[8px] text-slate-500">Records, finance and service workflows, brought together.</p>
            </div>
            <ChevronRight size={14} className="text-slate-400" />
          </div>
        </div>
      </div>
      <div className="absolute -bottom-5 -left-4 hidden items-center gap-2 rounded-xl border border-white/10 bg-white px-4 py-3 text-xs font-semibold text-slate-800 shadow-xl sm:flex">
        <Sparkles size={15} className="text-emerald-700" /> Thoughtful property management
      </div>
    </div>
  );
}

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-[#f7f8f6] text-slate-950">
      <header className="sticky top-0 z-30 border-b border-slate-200/80 bg-white/95 backdrop-blur">
        <nav aria-label="Main navigation" className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4 sm:px-8">
          <Link href="/" className="flex items-center gap-3" aria-label="Legacy Real Estate home">
            <span className="grid size-10 place-items-center rounded-xl bg-[#123b2c] text-emerald-200">
              <Building2 size={21} />
            </span>
            <span>
              <span className="block text-sm font-bold tracking-tight">LEGACY REAL ESTATE</span>
              <span className="block text-[10px] font-medium uppercase tracking-[0.16em] text-slate-500">Property, in good hands</span>
            </span>
          </Link>
          <div className="hidden items-center gap-8 md:flex">
            <a href="#platform" className="text-sm font-medium text-slate-600 transition hover:text-emerald-900">Platform</a>
            <a href="#portals" className="text-sm font-medium text-slate-600 transition hover:text-emerald-900">Who we serve</a>
            <a href="#approach" className="text-sm font-medium text-slate-600 transition hover:text-emerald-900">Our approach</a>
          </div>
          <Link
            href="/login"
            className="inline-flex items-center gap-2 rounded-xl bg-[#123b2c] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-emerald-950 shadow-xs"
          >
            <span>Sign in</span>
            <ArrowRight size={15} />
          </Link>
        </nav>
      </header>

      <section id="top" className="relative isolate overflow-hidden bg-[#102820] text-white">
        <div className="pointer-events-none absolute -right-36 -top-40 size-[36rem] rounded-full border border-emerald-100/10" />
        <div className="pointer-events-none absolute -right-12 -top-20 size-[26rem] rounded-full border border-emerald-100/10" />
        <div className="pointer-events-none absolute -bottom-64 -left-32 size-[34rem] rounded-full bg-emerald-500/10 blur-3xl" />
        <div className="relative mx-auto grid max-w-7xl items-center gap-16 px-5 py-16 sm:px-8 sm:py-20 lg:grid-cols-[1fr_0.95fr] lg:gap-10 lg:py-24">
          <div>
            <p className="mb-6 inline-flex items-center gap-2 rounded-full border border-emerald-200/20 bg-white/5 px-4 py-2 text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-200 sm:text-xs">
              <span className="size-2 rounded-full bg-emerald-300" /> Property management, made clearer
            </p>
            <h1 className="max-w-2xl text-4xl font-semibold leading-[1.06] tracking-tight sm:text-5xl lg:text-[4.25rem]">
              A better way to care for property.
            </h1>
            <p className="mt-6 max-w-xl text-base leading-7 text-slate-300 sm:text-lg sm:leading-8">
              Legacy Real Estate brings owners, residents and property teams into one thoughtful workspace—so the details are easier to manage and the experience feels more connected.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <a href="#homes" className="inline-flex items-center gap-2 rounded-xl bg-emerald-300 px-5 py-3 text-sm font-bold text-emerald-950 transition hover:bg-emerald-200">
                Find a home <ArrowRight size={16} />
              </a>
              <a href="#platform" className="inline-flex items-center gap-2 rounded-xl border border-white/20 bg-white/5 px-5 py-3 text-sm font-semibold text-white transition hover:bg-white/10">
                Discover the platform
              </a>
            </div>
            <div className="mt-10 flex flex-wrap gap-x-6 gap-y-3 border-t border-white/15 pt-5 text-xs text-slate-300 sm:text-sm">
              <span className="inline-flex items-center gap-2"><Check size={15} className="text-emerald-300" /> Clear property records</span>
              <span className="inline-flex items-center gap-2"><Check size={15} className="text-emerald-300" /> Connected rental finances</span>
              <span className="inline-flex items-center gap-2"><Check size={15} className="text-emerald-300" /> Access shaped by your role</span>
            </div>
          </div>
          <ProductPreview />
        </div>
        <div className="relative mx-auto max-w-7xl px-5 pb-12 sm:px-8">
          <ListingSearch />
        </div>
      </section>

      <section id="platform" className="px-5 py-20 sm:px-8 sm:py-24">
        <div className="mx-auto max-w-7xl">
          <div className="mx-auto max-w-3xl text-center">
            <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-800">One connected platform</p>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">The moving parts of property, working together.</h2>
            <p className="mt-4 text-base leading-7 text-slate-600">
              From property records to the resident experience, keep the important work organized and easier to follow.
            </p>
          </div>
          <div className="mt-10 grid gap-4 md:grid-cols-3">
            {capabilities.map(({ icon: Icon, title, description }) => (
              <article key={title} className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
                <span className="grid size-11 place-items-center rounded-xl bg-emerald-50 text-emerald-900">
                  <Icon size={21} />
                </span>
                <h3 className="mt-5 text-lg font-semibold tracking-tight">{title}</h3>
                <p className="mt-2 text-sm leading-6 text-slate-600">{description}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section id="portals" className="scroll-mt-24 border-y border-slate-200 bg-white px-5 py-20 sm:px-8 sm:py-24">
        <div className="mx-auto max-w-7xl">
          <div className="flex flex-col justify-between gap-5 lg:flex-row lg:items-end">
            <div className="max-w-2xl">
              <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-800">A workspace for you</p>
              <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">The right view for every relationship.</h2>
              <p className="mt-4 text-base leading-7 text-slate-600">
                Start with the portal that fits you. Each workspace is designed around the information and responsibilities that matter to that role.
              </p>
            </div>
            <p className="max-w-sm text-sm leading-6 text-slate-500">
              Tenant accounts can be created online. Owner and caretaker accounts are provisioned by the agency.
            </p>
          </div>

          <div className="mt-9 grid gap-5 lg:grid-cols-3">
            {roles.map(({ name, eyebrow, description, icon: Icon, features, tone }) => (
              <article key={name} className="overflow-hidden rounded-[1.6rem] border border-slate-200 bg-[#fbfcfb] shadow-sm">
                <div className={`h-1.5 ${tone === 'emerald' ? 'bg-emerald-700' : 'bg-blue-700'}`} />
                <div className="p-6 sm:p-8">
                  <div className="flex items-start justify-between gap-4">
                    <span className={`grid size-12 place-items-center rounded-2xl ${tone === 'emerald' ? 'bg-emerald-50 text-emerald-900' : 'bg-blue-50 text-blue-900'}`}>
                      <Icon size={23} />
                    </span>
                    <span className="rounded-full bg-white px-3 py-1.5 text-[10px] font-bold tracking-[0.13em] text-slate-500 ring-1 ring-slate-200">{eyebrow}</span>
                  </div>
                  <h3 className="mt-5 text-2xl font-semibold tracking-tight">{name}</h3>
                  <p className="mt-2 max-w-xl text-sm leading-6 text-slate-600">{description}</p>
                  <ul className="mt-5 grid gap-2 sm:grid-cols-3">
                    {features.map((feature) => (
                      <li key={feature} className="flex items-center gap-2 text-xs font-medium text-slate-600">
                        <Check size={14} className={tone === 'emerald' ? 'text-emerald-700' : 'text-blue-700'} />{feature}
                      </li>
                    ))}
                  </ul>
                  <div className="mt-7 rounded-xl bg-slate-100 px-4 py-3 text-xs font-medium text-slate-600">
                    Portal access is provided through an account assigned to this role.
                  </div>
                </div>
              </article>
            ))}
          </div>
          <div className="mt-6 flex flex-col gap-3 rounded-2xl bg-[#f3f6f4] px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-slate-600">
              Property owners and caretakers receive access through their agency. Residents access the account linked to their tenancy.
            </p>
          </div>
        </div>
      </section>

      <section id="approach" className="px-5 py-20 sm:px-8 sm:py-24">
        <div className="mx-auto grid max-w-7xl gap-10 lg:grid-cols-[0.9fr_1.1fr] lg:items-center">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-800">A considered approach</p>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">Good property experiences start with trust.</h2>
            <p className="mt-4 text-base leading-7 text-slate-600">
              Built for the realities of property management in Kenya, with a focus on accurate records, clear financial activity and respectful role-based access.
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {[
              { icon: ShieldCheck, title: 'Private by role', detail: 'Your workspace is shaped by your account and assigned responsibilities.' },
              { icon: KeyRound, title: 'Secure sign-in', detail: 'Protected sessions with additional verification for finance roles.' },
              { icon: ClipboardList, title: 'Traceable activity', detail: 'Important records and changes are designed for accountability.' },
              { icon: Users, title: 'People-first service', detail: 'A shared operating picture for the people behind every property.' },
            ].map(({ icon: Icon, title, detail }) => (
              <article key={title} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <Icon size={19} className="text-emerald-800" />
                <h3 className="mt-4 text-sm font-semibold">{title}</h3>
                <p className="mt-1.5 text-xs leading-5 text-slate-600">{detail}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="bg-[#102820] px-5 py-14 text-white sm:px-8">
        <div className="mx-auto flex max-w-7xl flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.17em] text-emerald-200">A more connected property experience</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">Let’s make property work feel simpler.</h2>
          </div>
        </div>
      </section>

      <footer className="border-t border-slate-200 bg-white px-5 py-6 sm:px-8">
        <div className="mx-auto flex max-w-7xl flex-col gap-3 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between">
          <Link href="/" className="font-bold tracking-tight text-slate-800">LEGACY REAL ESTATE</Link>
          <p>Property, in good hands.</p>
        </div>
      </footer>
    </main>
  );
}
