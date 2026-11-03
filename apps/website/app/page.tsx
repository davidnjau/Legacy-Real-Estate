import {
  ArrowDown,
  ArrowRight,
  BadgeCheck,
  BarChart3,
  Building2,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  CloudCog,
  Container,
  Database,
  FileCheck2,
  GitBranch,
  KeyRound,
  Layers3,
  LockKeyhole,
  MessageSquareText,
  Network,
  Presentation,
  ReceiptText,
  Rocket,
  ShieldCheck,
  Smartphone,
  TestTube2,
  Users,
  WalletCards,
  Wrench,
} from 'lucide-react';

type StatusTone = 'done' | 'next' | 'pending' | 'info';

type Workstream = {
  id: string;
  title: string;
  summary: string;
  details: string;
};

const foundationWork: Workstream[] = [
  {
    id: 'W0',
    title: 'Scope, architecture, and delivery foundations',
    summary: 'The project is structured as a Django and Next.js monorepo with a versioned API contract.',
    details:
      'Authentication, MFA foundations, roles, branch-scoped access, immutable audit logging, local PostgreSQL/Redis/MinIO/Mailpit services, quality checks, and the domain dictionary are in place.',
  },
  {
    id: 'W1',
    title: 'Rental master data and tenancy foundations',
    summary: 'The platform can represent the agency, branches, landlords, properties, units, tenants, charges, reservations, and tenancy records.',
    details:
      'Records are effective-dated where history matters, access is enforced server-side, move-in workflows are supported, and the property structure is ready for operations and billing.',
  },
];

const billingWork: Workstream[] = [
  {
    id: 'W2-01 to W2-05',
    title: 'Invoices, charges, utilities, and monthly billing',
    summary: 'Invoices have branch-sequential numbers, immutable issued records, line items, recurring charges, meter-based water charges, and carried balances.',
    details:
      'Monthly invoice generation is idempotent, includes configured utilities, validates meter readings, and avoids duplicate billing for the same tenancy and period.',
  },
  {
    id: 'W2-06 to W2-08',
    title: 'Arrears, payment plans, and payment intake',
    summary: 'Arrears ageing, penalties, manager-approved waivers, demand letters, instalment plans, and M-Pesa callback processing are implemented.',
    details:
      'Callback payloads are persisted before processing, duplicate transactions are safe to retry, and payment-plan balances, missed instalments, and completion states are tracked.',
  },
  {
    id: 'W2-09 to W2-11',
    title: 'Allocation, credits, and receipts',
    summary: 'Payments allocate transactionally to the oldest outstanding invoices, with overpayments carried as tenant credit.',
    details:
      'Unmatched payments are queued for staff review, suggestions remain advisory, matched payments receive immutable receipts, and receipt data is downloadable.',
  },
  {
    id: 'W2-12 to W2-15',
    title: 'Reconciliation, offline payments, deposits, and reporting',
    summary: 'Bank CSV/XLSX imports, manual matching, cash and cheque handling, refundable deposits, top-ups, and daily reconciliation views are available.',
    details:
      'Finance actions are authorized and audited. Reports compare received, matched, unmatched, reversed, and allocated totals by date and payment channel.',
  },
];

const nextWork: Workstream[] = [
  {
    id: 'W3',
    title: 'Property operations',
    summary: 'The next planned wave connects the trusted records to day-to-day property work.',
    details:
      'The proposed scope includes maintenance requests, caretaker work queues, quotations, approvals, vendors, expenses, water-reading operations, notifications, and operational dashboards.',
  },
  {
    id: 'W4',
    title: 'Role-based portals and polished product UI',
    summary: 'Later tasks will turn the platform capabilities into focused experiences for each audience.',
    details:
      'The landlord portal will focus on oversight, the caretaker portal on assigned work, and the tenant portal on bills, payments, maintenance requests, notices, and communication.',
  },
];

const toneClasses: Record<StatusTone, string> = {
  done: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
  next: 'bg-blue-50 text-blue-800 ring-blue-200',
  pending: 'bg-amber-50 text-amber-900 ring-amber-200',
  info: 'bg-slate-100 text-slate-700 ring-slate-200',
};

function WorkCard({
  work,
  tone = 'done',
}: {
  work: Workstream;
  tone?: StatusTone;
}) {
  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
      <div className="flex items-start justify-between gap-4">
        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-[#10251f] text-[10px] font-bold text-white">
          {work.id}
        </span>
        <span className={`rounded-full px-3 py-1 text-[10px] font-bold ring-1 ${toneClasses[tone]}`}>
          {tone === 'done' ? 'Implemented' : tone === 'pending' ? 'PM approval' : 'Next'}
        </span>
      </div>
      <h3 className="mt-5 text-lg font-semibold tracking-tight">{work.title}</h3>
      <p className="mt-3 text-sm font-medium leading-6 text-slate-800">{work.summary}</p>
      <p className="mt-3 text-sm leading-6 text-slate-600">{work.details}</p>
    </article>
  );
}

function Capability({ icon: Icon, title, text }: { icon: typeof Building2; title: string; text: string }) {
  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-5">
      <span className="grid size-10 place-items-center rounded-xl bg-emerald-50 text-emerald-900">
        <Icon size={20} aria-hidden="true" />
      </span>
      <h3 className="mt-4 font-semibold">{title}</h3>
      <p className="mt-2 text-sm leading-6 text-slate-600">{text}</p>
    </article>
  );
}

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-[#f7f8f6] text-slate-950">
      <header className="border-b border-slate-200/80 bg-white">
        <nav
          aria-label="Presentation navigation"
          className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4 sm:px-8"
        >
          <a href="#top" className="flex items-center gap-3">
            <span className="grid size-10 place-items-center rounded-xl bg-emerald-900 text-white">
              <Building2 size={21} aria-hidden="true" />
            </span>
            <span>
              <span className="block text-sm font-bold tracking-tight">LEGACY REAL ESTATE</span>
              <span className="block text-xs text-slate-500">Platform progress presentation</span>
            </span>
          </a>
          <a
            href="#status"
            className="hidden items-center gap-2 rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-emerald-800 hover:text-emerald-900 sm:inline-flex"
          >
            See project status <ArrowDown size={15} aria-hidden="true" />
          </a>
        </nav>
      </header>

      <section id="top" className="relative isolate overflow-hidden bg-[#10251f] text-white">
        <div className="pointer-events-none absolute -right-32 -top-40 size-[34rem] rounded-full border border-emerald-200/10" />
        <div className="pointer-events-none absolute -right-8 -top-16 size-[25rem] rounded-full border border-emerald-200/10" />
        <div className="relative mx-auto grid max-w-7xl gap-12 px-5 py-20 sm:px-8 sm:py-28 lg:grid-cols-[1.1fr_0.9fr] lg:items-center lg:py-32">
          <div>
            <p className="mb-6 inline-flex items-center gap-2 rounded-full border border-emerald-300/25 bg-emerald-200/10 px-4 py-2 text-xs font-bold uppercase tracking-[0.16em] text-emerald-200">
              <span className="size-2 rounded-full bg-emerald-300" />
              W0-W2 complete · awaiting PM green light
            </p>
            <h1 className="max-w-3xl text-4xl font-semibold leading-[1.08] tracking-tight sm:text-5xl lg:text-7xl">
              The platform is being built behind the scenes.
            </h1>
            <p className="mt-7 max-w-2xl text-base leading-7 text-slate-300 sm:text-lg sm:leading-8">
              Legacy Real Estate is becoming a Kenya-focused property operations platform for agencies,
              landlords, caretakers, and tenants. The engineering foundation and core rental finance workflows
              are now in place. The next step is for the PMs to review the completed work and give the
              green light to proceed. W3 clarification is only needed where a workflow requires a business
              decision that has not yet been agreed.
            </p>
            <div className="mt-9 flex flex-wrap items-center gap-4">
              <a
                href="#status"
                className="inline-flex items-center gap-2 rounded-lg bg-emerald-300 px-5 py-3 text-sm font-bold text-emerald-950 transition hover:bg-emerald-200"
              >
                Explore the progress <ArrowRight size={17} aria-hidden="true" />
              </a>
              <span className="text-sm text-slate-400">First client presentation prototype coming soon</span>
            </div>
            <div className="mt-14 flex flex-wrap gap-x-8 gap-y-3 border-t border-white/15 pt-6 text-sm text-slate-300">
              <span className="inline-flex items-center gap-2"><BadgeCheck size={17} className="text-emerald-300" aria-hidden="true" />W2 validated</span>
              <span className="inline-flex items-center gap-2"><GitBranch size={17} className="text-emerald-300" aria-hidden="true" />Branch-aware</span>
              <span className="inline-flex items-center gap-2"><ShieldCheck size={17} className="text-emerald-300" aria-hidden="true" />Audited access</span>
            </div>
          </div>
          <div className="rounded-2xl border border-white/15 bg-white/[0.06] p-5 shadow-2xl backdrop-blur sm:p-7">
            <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-200">Delivery checkpoint</p>
            <div className="mt-6 space-y-3">
              {[
                ['01', 'Foundation', 'Scope, API, security, and quality'],
                ['02', 'Rental records', 'Branches, owners, properties, units, tenants'],
                ['03', 'Financial workflows', 'Invoices, payments, arrears, reconciliation'],
                ['04', 'Next decision', 'PM sign-off and W3 planning'],
              ].map(([number, title, detail], index) => (
                <div key={number} className="flex items-center gap-4 rounded-xl bg-black/15 p-4">
                  <span className="grid size-9 shrink-0 place-items-center rounded-lg border border-emerald-200/20 text-xs font-bold text-emerald-200">{number}</span>
                  <div className="min-w-0 flex-1"><p className="font-semibold">{title}</p><p className="mt-1 text-xs text-slate-400">{detail}</p></div>
                  <span className={`size-2 shrink-0 rounded-full ${index < 3 ? 'bg-emerald-300' : 'bg-amber-300'}`} />
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section id="status" className="scroll-mt-8 border-b border-amber-200 bg-amber-50 px-5 py-8 sm:px-8">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex gap-4">
            <ClipboardCheck className="mt-1 shrink-0 text-amber-800" size={22} aria-hidden="true" />
            <div>
              <p className="text-sm font-bold uppercase tracking-[0.14em] text-amber-900">What this website is today</p>
              <p className="mt-1 max-w-4xl text-sm leading-6 text-amber-950">
                This is a presentation homepage, not the finished landlord, caretaker, or tenant portal.
                Most completed work is backend and infrastructure work that users do not see yet: records,
                rules, permissions, APIs, payments, audit trails, migrations, tests, and deployment preparation.
              </p>
            </div>
          </div>
          <span className="shrink-0 rounded-full bg-white px-3 py-2 text-xs font-bold text-amber-900 ring-1 ring-amber-200">
            Prototype UI coming soon
          </span>
        </div>
      </section>

      <section className="px-5 py-20 sm:px-8 sm:py-24">
        <div className="mx-auto max-w-7xl">
          <div className="max-w-3xl">
            <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-800">The product vision</p>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">One connected workspace for property work.</h2>
            <p className="mt-4 text-base leading-7 text-slate-600">
              The long-term product brings together agency operations, property records, tenant relationships,
              bills, payments, maintenance, communication, and owner oversight—while each role sees only what it needs.
            </p>
          </div>
          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Capability icon={Building2} title="Agency workspace" text="Branches, teams, roles, properties, units, agreements, and secure access." />
            <Capability icon={ReceiptText} title="Rental finance" text="Invoices, recurring charges, water billing, payments, credits, deposits, and arrears." />
            <Capability icon={Network} title="Operational workflows" text="Maintenance, caretakers, vendors, expenses, approvals, readings, and notifications." />
            <Capability icon={Users} title="Role-based portals" text="Focused experiences for landlords, caretakers, tenants, and the agency team." />
          </div>
        </div>
      </section>

      <section className="border-y border-slate-200 bg-white px-5 py-20 sm:px-8 sm:py-24">
        <div className="mx-auto max-w-7xl">
          <div className="flex flex-col justify-between gap-5 lg:flex-row lg:items-end">
            <div>
              <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-800">Completed foundation</p>
              <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">W0 and W1 made the system trustworthy.</h2>
            </div>
            <p className="max-w-2xl text-sm leading-6 text-slate-600">
              Before adding finance and operations, the platform needed a reliable vocabulary, secure boundaries,
              and durable rental records. Those layers are now implemented.
            </p>
          </div>
          <div className="mt-10 grid gap-4 lg:grid-cols-2">{foundationWork.map((work) => <WorkCard key={work.id} work={work} />)}</div>
        </div>
      </section>

      <section className="bg-[#eaf0ec] px-5 py-20 sm:px-8 sm:py-24">
        <div className="mx-auto max-w-7xl">
          <div className="flex flex-col justify-between gap-5 lg:flex-row lg:items-end">
            <div>
              <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-800">Wave 2 completed</p>
              <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">The financial engine is in place.</h2>
            </div>
            <div className="flex items-center gap-2 text-sm font-semibold text-emerald-900">
              <TestTube2 size={18} aria-hidden="true" /> Full Django integration suite: 60 tests passed
            </div>
          </div>
          <div className="mt-10 grid gap-4 md:grid-cols-2">{billingWork.map((work) => <WorkCard key={work.id} work={work} />)}</div>
          <div className="mt-8 grid gap-4 sm:grid-cols-3">
            <div className="rounded-2xl bg-white p-5"><Database className="text-emerald-800" size={21} aria-hidden="true" /><p className="mt-3 text-sm font-semibold">PostgreSQL-ready migrations</p><p className="mt-1 text-xs leading-5 text-slate-600">Models, constraints, indexes, and immutable financial records.</p></div>
            <div className="rounded-2xl bg-white p-5"><LockKeyhole className="text-emerald-800" size={21} aria-hidden="true" /><p className="mt-3 text-sm font-semibold">Authorization and audit</p><p className="mt-1 text-xs leading-5 text-slate-600">Finance actions are permissioned, scoped, and traceable.</p></div>
            <div className="rounded-2xl bg-white p-5"><BarChart3 className="text-emerald-800" size={21} aria-hidden="true" /><p className="mt-3 text-sm font-semibold">Reconciliation visibility</p><p className="mt-1 text-xs leading-5 text-slate-600">Daily totals and channel-level reporting are available.</p></div>
          </div>
        </div>
      </section>

      <section className="border-y border-slate-200 bg-white px-5 py-20 sm:px-8 sm:py-24">
        <div className="mx-auto grid max-w-7xl gap-10 lg:grid-cols-[0.85fr_1.15fr] lg:items-start">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.18em] text-blue-800">Delivery and infrastructure</p>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">Staging is deliberately simple. Production can scale later.</h2>
            <p className="mt-4 text-sm leading-7 text-slate-600">
              The accepted architecture decision uses Docker Compose on the prepared single-VPS staging environment.
              This keeps the current deployment repeatable while the application contracts and workflows stabilize.
            </p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Capability icon={Container} title="Compose for staging" text="Django, Next.js, PostgreSQL, Redis, MinIO, and Caddy run as a repeatable VM deployment with HTTPS." />
            <Capability icon={CloudCog} title="K3s and Helm for production" text="The repository retains Kubernetes infrastructure for the production phase when scale, rollout, isolation, and resilience justify it." />
            <Capability icon={Rocket} title="Direct staging deployment" text="Commit locally and push directly to staging; GitHub Actions builds the archived commit, deploys it, and checks the public health endpoint." />
            <Capability icon={ShieldCheck} title="Safe environment boundaries" text="Staging uses demo data and VM-only secrets; production credentials and live records remain separate." />
          </div>
        </div>
        <div className="mx-auto mt-8 flex max-w-7xl items-start gap-3 rounded-2xl border border-blue-200 bg-blue-50 p-5 text-sm leading-6 text-blue-950">
          <FileCheck2 className="mt-0.5 shrink-0 text-blue-800" size={19} aria-hidden="true" />
          <p><strong>Architecture decision:</strong> Docker Compose on a VM is the right fit for current staging. K3s/Helm remains the intended production direction and can be adopted when production requirements call for multiple nodes, automated rollouts, horizontal scaling, stronger workload isolation, or high availability.</p>
        </div>
        <div className="mx-auto mt-4 flex max-w-7xl items-start gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 p-5 text-sm leading-6 text-emerald-950">
          <MessageSquareText className="mt-0.5 shrink-0 text-emerald-800" size={19} aria-hidden="true" />
          <p><strong>M-Pesa sandbox callback:</strong> <code className="break-all rounded bg-white px-2 py-1 text-xs font-semibold text-emerald-900 ring-1 ring-emerald-200">https://staging.legacyhomes.live/api/v1/payments/mpesa/callback</code></p>
        </div>
      </section>

      <section className="bg-[#10251f] px-5 py-20 text-white sm:px-8 sm:py-24">
        <div className="mx-auto max-w-7xl">
          <div className="max-w-3xl">
            <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-200">The next decision</p>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">Ready to continue with Wave 3 after PM sign-off.</h2>
            <p className="mt-4 text-sm leading-7 text-slate-300">
              W3 should connect the trusted backend records to the people and processes that keep properties running.
              The PMs are being asked to approve the completed W0-W2 foundation and give the green light to
              proceed. W3 can begin after approval; only unresolved business decisions within specific W3
              tasks should pause implementation for clarification.
            </p>
          </div>
          <div className="mt-10 grid gap-4 lg:grid-cols-2">{nextWork.map((work, index) => <WorkCard key={work.id} work={work} tone={index === 0 ? 'pending' : 'next'} />)}</div>
          <div className="mt-10 flex flex-col gap-4 rounded-2xl bg-emerald-300 p-6 text-emerald-950 sm:flex-row sm:items-center sm:justify-between sm:p-8">
            <div>
              <p className="text-xs font-bold uppercase tracking-[0.16em]">PM sign-off checkpoint</p>
              <p className="mt-2 max-w-3xl text-lg font-semibold leading-7">Review the completed W0-W2 foundation and give the green light to proceed. Any W3-specific business clarification can be handled when that task is reached.</p>
            </div>
            <ChevronRight className="hidden shrink-0 sm:block" size={34} aria-hidden="true" />
          </div>
        </div>
      </section>

      <section className="px-5 py-20 sm:px-8 sm:py-24">
        <div className="mx-auto grid max-w-7xl gap-10 lg:grid-cols-[1.1fr_0.9fr] lg:items-center">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-800">What the first prototype will show</p>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">A client-facing view is coming soon.</h2>
            <p className="mt-4 text-base leading-7 text-slate-600">
              We are intentionally building the platform behind the scenes first. The first prototype for client
              presentation will arrive as the workflow and portal tasks mature, giving stakeholders something visual
              to review without pretending that the finished product is already live.
            </p>
            <div className="mt-7 space-y-3 text-sm text-slate-700">
              {[
                'A clear agency and property operations overview',
                'Early landlord, caretaker, and tenant experience concepts',
                'Representative bills, payment, maintenance, and notification flows',
              ].map((item) => (
                <p key={item} className="flex items-center gap-3"><CheckCircle2 size={17} className="shrink-0 text-emerald-700" aria-hidden="true" />{item}</p>
              ))}
            </div>
          </div>
          <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-xl sm:p-8">
            <div className="flex items-center gap-3"><Presentation className="text-emerald-800" size={23} aria-hidden="true" /><p className="font-semibold">Presentation now, product UI later</p></div>
            <div className="mt-6 space-y-4">
              {[
                ['Today', 'Progress dashboard and project story'],
                ['Next', 'W3 operational workflow foundations'],
                ['Later', 'Interactive role-based portal prototype'],
                ['Production', 'Validated product on the appropriate deployment platform'],
              ].map(([label, text], index) => (
                <div key={label} className="flex gap-4">
                  <span className={`mt-1 size-2 shrink-0 rounded-full ${index === 0 ? 'bg-emerald-600' : 'bg-slate-300'}`} />
                  <div><p className="text-xs font-bold uppercase tracking-[0.12em] text-slate-500">{label}</p><p className="mt-1 text-sm font-medium text-slate-800">{text}</p></div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <footer className="border-t border-slate-200 bg-white px-5 py-8 sm:px-8">
        <div className="mx-auto flex max-w-7xl flex-col gap-3 text-xs leading-5 text-slate-500 sm:flex-row sm:items-center sm:justify-between">
          <p>Legacy Real Estate · Kenya-focused property operations platform</p>
          <p className="inline-flex items-center gap-2"><Smartphone size={14} aria-hidden="true" />Presentation homepage · W0-W2 complete · W3 pending approval</p>
        </div>
      </footer>
    </main>
  );
}
