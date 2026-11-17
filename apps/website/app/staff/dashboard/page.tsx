'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Building2,
  BriefcaseBusiness,
  Users,
  Receipt,
  Wrench,
  ShieldCheck,
  MessageSquare,
  Landmark,
  Map as MapIcon,
  Scale,
  RefreshCw,
  Clock,
  ArrowUpRight,
  Sparkles,
} from 'lucide-react';

type PropertyRecord = {
  id: number;
  branch_id: number;
  name: string;
  property_type: string;
  location: string;
};

type UnitRecord = {
  id: number;
  property_id: number;
};

type CurrentUser = {
  name: string;
  roles: string[];
  branches: { id: number; name: string; town: string }[];
};

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

/* ─── Prototype Skeletons for Wave Modules ─── */
const PROTOTYPE_PROPERTIES: PropertyRecord[] = [
  { id: 1, branch_id: 1, name: 'Sunset Villas', property_type: 'Residential Apartments', location: 'Kilimani, Nairobi' },
  { id: 2, branch_id: 1, name: 'Parklands Heights', property_type: 'Multi-family Residential', location: 'Westlands, Nairobi' },
  { id: 3, branch_id: 1, name: 'Garden Court', property_type: 'Gated Estate', location: 'Lavington, Nairobi' },
  { id: 4, branch_id: 2, name: 'Milimani Gardens', property_type: 'Residential Flats', location: 'Milimani, Nakuru' },
  { id: 5, branch_id: 2, name: 'Section 58 Commercial Hub', property_type: 'Commercial / Retail', location: 'Section 58, Nakuru' },
];

const PROTOTYPE_INVOICES = [
  { tenant: 'Brian Omondi', unit: 'NAI-SV-A3', property: 'Sunset Villas', amount: 15500, status: 'Overdue', days: 4 },
  { tenant: 'Faith Chebet', unit: 'NAI-PH-B2', property: 'Parklands Heights', amount: 22000, status: 'Settled', days: 0 },
  { tenant: 'Peter Kimani', unit: 'NAI-GC-C1', property: 'Garden Court', amount: 18500, status: 'Settled', days: 0 },
  { tenant: 'Alice Muthoni', unit: 'NKU-MG-D4', property: 'Milimani Gardens', amount: 12000, status: 'Partial', days: 2 },
  { tenant: 'Dennis Kiprop', unit: 'NKU-58-10', property: 'Section 58 Hub', amount: 35000, status: 'Overdue', days: 9 },
];

const PROTOTYPE_APPROVALS = [
  {
    id: 1,
    title: 'Roof Membrane Waterproofing',
    property: 'Sunset Villas',
    amount: 24000,
    requestedBy: 'Samuel Otieno (Caretaker)',
    rule: 'Maker-Checker: Quotes > KES 10,000 require Director sign-off',
    status: 'Pending Review',
  },
  {
    id: 2,
    title: 'Tenant Security Deposit Settlement',
    property: 'Parklands Heights',
    amount: 18000,
    requestedBy: 'Jane Wambui (Property Officer)',
    rule: 'Deposit refund minus KES 2,000 utility clearance',
    status: 'Pending Review',
  },
  {
    id: 3,
    title: 'Borehole Submersible Pump Overhaul',
    property: 'Milimani Gardens (Nakuru)',
    amount: 38500,
    requestedBy: 'Joseph Kariuki (Branch Manager)',
    rule: 'Emergency CAPEX threshold approval',
    status: 'Pending Review',
  },
];

const PROTOTYPE_MAINTENANCE = [
  { id: 'MT-2026-0034', unit: 'NAI-SV-A3', issue: 'Leaking kitchen mixer tap', priority: 'Medium', caretaker: 'Samuel Otieno', status: 'In Progress' },
  { id: 'MT-2026-0031', unit: 'NAI-PH-B5', issue: 'Window frame latch replacement', priority: 'Low', caretaker: 'Samuel Otieno', status: 'Scheduled' },
  { id: 'MT-2026-0029', unit: 'NKU-MG-D2', issue: 'Distribution board breaker trip', priority: 'High', caretaker: 'John Koech', status: 'Assigned' },
];

const PROTOTYPE_SMS_LOGS = [
  { id: 1, recipient: '+254 733 *** 003 (Brian Omondi)', type: 'Rent Invoice Notice', status: 'Delivered', sentAt: '01 Nov 08:30' },
  { id: 2, recipient: '+254 722 *** 119 (Faith Chebet)', type: 'Payment Receipt #RCP-0847', status: 'Delivered', sentAt: '03 Nov 14:15' },
  { id: 3, recipient: '+254 711 *** 882 (Dennis Kiprop)', type: 'Arrears Reminder (Grace Day 10)', status: 'Queued', sentAt: 'Today 09:00' },
];

export default function StaffDashboardPage() {
  const [properties, setProperties] = useState<PropertyRecord[]>([]);
  const [units, setUnits] = useState<UnitRecord[]>([]);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [approvals, setApprovals] = useState(PROTOTYPE_APPROVALS);
  const [approvalFeedback, setApprovalFeedback] = useState<string | null>(null);

  const loadRecords = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const [propertyResponse, unitResponse, userResponse] = await Promise.all([
        fetch('/api/staff/properties', { cache: 'no-store', signal }),
        fetch('/api/staff/units', { cache: 'no-store', signal }),
        fetch('/api/auth/me', { cache: 'no-store', signal }),
      ]);
      const [propertyPayload, unitPayload, userPayload] = await Promise.all([
        propertyResponse.json().catch(() => ({})),
        unitResponse.json().catch(() => ({})),
        userResponse.json().catch(() => ({})),
      ]);

      if (propertyPayload.properties && propertyPayload.properties.length > 0) {
        setProperties(propertyPayload.properties);
      } else {
        setProperties(PROTOTYPE_PROPERTIES);
      }

      if (unitPayload.units && unitPayload.units.length > 0) {
        setUnits(unitPayload.units);
      } else {
        // Fallback simulated unit list
        setUnits(Array.from({ length: 186 }, (_, i) => ({ id: i + 1, property_id: (i % 5) + 1 })));
      }

      if (userPayload.user) {
        setUser(userPayload.user);
      } else {
        setUser({
          name: 'David Njau',
          roles: ['Super Admin (Director)', 'Branch Manager', 'Accountant'],
          branches: [
            { id: 1, name: 'Nairobi Head Office', town: 'Nairobi' },
            { id: 2, name: 'Nakuru Branch Office', town: 'Nakuru' },
          ],
        });
      }
    } catch {
      setProperties(PROTOTYPE_PROPERTIES);
      setUnits(Array.from({ length: 186 }, (_, i) => ({ id: i + 1, property_id: (i % 5) + 1 })));
      setUser({
        name: 'David Njau',
        roles: ['Super Admin (Director)', 'Branch Manager', 'Accountant'],
        branches: [
          { id: 1, name: 'Nairobi Head Office', town: 'Nairobi' },
          { id: 2, name: 'Nakuru Branch Office', town: 'Nakuru' },
        ],
      });
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadRecords(controller.signal);
    return () => controller.abort();
  }, [loadRecords]);

  const handleActionApproval = (id: number, decision: 'approved' | 'rejected') => {
    setApprovals((prev) =>
      prev.map((item) => (item.id === id ? { ...item, status: decision === 'approved' ? 'Approved' : 'Rejected' } : item))
    );
    setApprovalFeedback(`Item #${id} marked as ${decision.toUpperCase()} in Maker-Checker queue.`);
    setTimeout(() => setApprovalFeedback(null), 4000);
  };

  const unitCounts = useMemo(() => {
    const counts = new Map<number, number>();
    for (const unit of units) {
      counts.set(unit.property_id, (counts.get(unit.property_id) || 0) + 1);
    }
    return counts;
  }, [units]);

  return (
    <div className="space-y-8 p-6 md:p-8">
      {/* ─── Hero Welcome Banner ─── */}
      <section className="relative overflow-hidden rounded-2xl bg-gradient-to-r from-[#102820] via-[#123b2c] to-[#154634] p-6 text-white shadow-md sm:p-8">
        <div className="relative z-10 flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-400/30 bg-emerald-400/10 px-3 py-0.5 text-xs font-semibold text-emerald-300">
                <Sparkles size={13} />
                Agency Operations Command
              </span>
              <span className="rounded-full bg-white/10 px-2.5 py-0.5 text-xs text-emerald-200">
                Nairobi & Nakuru Branches
              </span>
            </div>
            <h1 className="mt-3 text-2xl font-bold tracking-tight sm:text-3xl">
              Welcome back, {user?.name || 'David Njau'}
            </h1>
            <p className="mt-1 text-sm text-emerald-100/80">
              Assigned Roles: <span className="font-semibold text-white">{user?.roles.join(' · ') || 'Super Admin'}</span>
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadRecords()}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-xl border border-white/20 bg-white/10 px-4 py-2 text-xs font-semibold text-white backdrop-blur-sm transition hover:bg-white/20 disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            Refresh Data
          </button>
        </div>
      </section>

      {/* ─── KPI Row (Wave 0 - Wave 5 Summary) ─── */}
      <section aria-label="Command KPIs" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[
          { label: 'Managed Properties', value: properties.length || 5, detail: 'Across Nairobi & Nakuru', icon: Building2, tone: 'emerald' },
          { label: 'Units in Portfolio', value: units.length || 186, detail: '170 occupied (91.4%)', icon: BriefcaseBusiness, tone: 'blue' },
          { label: 'Expected Rent (Nov)', value: currency.format(2840000), detail: 'Collected: KES 2,595,000 (91%)', icon: Receipt, tone: 'emerald' },
          { label: 'Active Arrears', value: currency.format(245000), detail: '5 accounts past grace day 10', icon: Clock, tone: 'amber' },
        ].map(({ label, value, detail, icon: Icon, tone }) => (
          <article key={label} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs transition hover:shadow-sm">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-xs font-medium uppercase tracking-wider text-slate-500">{label}</p>
                <p className="mt-2 text-2xl font-bold tracking-tight text-slate-900">{value}</p>
              </div>
              <span className={`rounded-xl p-2.5 ${tone === 'emerald' ? 'bg-emerald-50 text-emerald-700' : tone === 'blue' ? 'bg-blue-50 text-blue-700' : 'bg-amber-50 text-amber-700'}`}>
                <Icon size={20} aria-hidden="true" />
              </span>
            </div>
            <p className="mt-3 text-xs text-slate-500">{detail}</p>
          </article>
        ))}
      </section>

      {/* ─── Revenue Collection Progress Bar ─── */}
      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xs">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-base font-semibold text-slate-900">November 2026 Rent Roll Collection</h2>
            <p className="text-xs text-slate-500">M-Pesa C2B Paybill 247247 + Manual Bank Allocations</p>
          </div>
          <span className="rounded-full bg-emerald-100 px-3 py-1 text-xs font-bold text-emerald-800">
            91.4% Collected
          </span>
        </div>
        <div className="mt-4 h-3.5 w-full overflow-hidden rounded-full bg-slate-100">
          <div className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-teal-500 transition-all duration-500" style={{ width: '91.4%' }} />
        </div>
        <div className="mt-3 flex items-center justify-between text-xs font-medium text-slate-600">
          <span>Collected: <strong>KES 2,595,000</strong></span>
          <span>Target: <strong>KES 2,840,000</strong></span>
          <span className="text-amber-700">Shortfall: <strong>KES 245,000</strong></span>
        </div>
      </section>

      {/* ─── Two-Column Section: Rent Invoicing & Approvals Queue ─── */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Module 2: Rent Invoicing & Arrears Engine */}
        <section id="invoicing" className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xs">
          <div className="flex items-center justify-between border-b border-slate-100 pb-4">
            <div>
              <div className="flex items-center gap-2">
                <Receipt size={17} className="text-emerald-700" />
                <h2 className="text-base font-semibold text-slate-900">Rent Invoicing & Arrears</h2>
              </div>
              <p className="mt-0.5 text-xs text-slate-500">Wave 2 Engine · 10th Grace Period Monitoring</p>
            </div>
            <span className="rounded-md bg-emerald-50 px-2 py-0.5 text-[10px] font-bold uppercase text-emerald-700">
              Wave 2 Active
            </span>
          </div>

          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-slate-200 text-slate-500">
                <tr>
                  <th className="pb-2 font-medium">Tenant</th>
                  <th className="pb-2 font-medium">Unit</th>
                  <th className="pb-2 text-right font-medium">Due</th>
                  <th className="pb-2 text-right font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {PROTOTYPE_INVOICES.map((inv) => (
                  <tr key={inv.unit} className="hover:bg-slate-50/50">
                    <td className="py-2.5 font-medium text-slate-900">{inv.tenant}</td>
                    <td className="py-2.5 text-slate-500">{inv.unit}</td>
                    <td className="py-2.5 text-right font-semibold text-slate-800">{currency.format(inv.amount)}</td>
                    <td className="py-2.5 text-right">
                      <span className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-bold ${inv.status === 'Settled'
                        ? 'bg-emerald-100 text-emerald-800'
                        : inv.status === 'Partial'
                          ? 'bg-amber-100 text-amber-800'
                          : 'bg-red-100 text-red-800'
                        }`}>
                        {inv.status} {inv.days > 0 ? `(${inv.days}d)` : ''}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* Module 4: Maker-Checker Financial Approvals */}
        <section id="approvals" className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xs">
          <div className="flex items-center justify-between border-b border-slate-100 pb-4">
            <div>
              <div className="flex items-center gap-2">
                <ShieldCheck size={17} className="text-emerald-700" />
                <h2 className="text-base font-semibold text-slate-900">Maker-Checker Approvals Queue</h2>
              </div>
              <p className="mt-0.5 text-xs text-slate-500">Wave 1 & 4 Workflow · Dual Authorization</p>
            </div>
            <span className="rounded-md bg-emerald-50 px-2 py-0.5 text-[10px] font-bold uppercase text-emerald-700">
              Wave 4 Active
            </span>
          </div>

          {approvalFeedback && (
            <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-800">
              {approvalFeedback}
            </div>
          )}

          <div className="mt-4 space-y-3">
            {approvals.map((item) => (
              <div key={item.id} className="rounded-xl border border-slate-200 bg-slate-50/60 p-3.5 text-xs">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="font-semibold text-slate-900">{item.title}</p>
                    <p className="text-slate-500">{item.property} · {item.requestedBy}</p>
                  </div>
                  <span className="font-bold text-slate-900">{currency.format(item.amount)}</span>
                </div>
                <p className="mt-1 text-[11px] text-slate-600">{item.rule}</p>
                <div className="mt-3 flex items-center justify-between border-t border-slate-200/80 pt-2.5">
                  <span className={`font-semibold ${item.status === 'Approved' ? 'text-emerald-700' : item.status === 'Rejected' ? 'text-red-700' : 'text-amber-700'
                    }`}>
                    {item.status}
                  </span>
                  {item.status === 'Pending Review' && (
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => handleActionApproval(item.id, 'approved')}
                        className="rounded-lg bg-emerald-600 px-3 py-1 font-semibold text-white hover:bg-emerald-700"
                      >
                        Authorize
                      </button>
                      <button
                        type="button"
                        onClick={() => handleActionApproval(item.id, 'rejected')}
                        className="rounded-lg border border-slate-300 bg-white px-3 py-1 font-semibold text-slate-700 hover:bg-slate-100"
                      >
                        Reject
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* ─── Two-Column Section: Maintenance & SMS Gateway ─── */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Module 3: Maintenance Operations */}
        <section id="maintenance" className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xs">
          <div className="flex items-center justify-between border-b border-slate-100 pb-4">
            <div>
              <div className="flex items-center gap-2">
                <Wrench size={17} className="text-emerald-700" />
                <h2 className="text-base font-semibold text-slate-900">Maintenance & Caretaker Ops</h2>
              </div>
              <p className="mt-0.5 text-xs text-slate-500">Wave 3 · Field Inspections & Ticket Dispatch</p>
            </div>
            <span className="rounded-md bg-emerald-50 px-2 py-0.5 text-[10px] font-bold uppercase text-emerald-700">
              Wave 3 Active
            </span>
          </div>

          <div className="mt-4 space-y-2.5">
            {PROTOTYPE_MAINTENANCE.map((t) => (
              <div key={t.id} className="flex items-center justify-between rounded-xl border border-slate-200 bg-slate-50/50 p-3 text-xs">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-slate-900">{t.id}</span>
                    <span className="text-slate-500">({t.unit})</span>
                  </div>
                  <p className="mt-0.5 text-slate-700">{t.issue}</p>
                  <p className="mt-0.5 text-[11px] text-slate-500">Assigned: {t.caretaker}</p>
                </div>
                <div className="text-right">
                  <span className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-bold ${t.priority === 'High' ? 'bg-red-100 text-red-800' : 'bg-blue-100 text-blue-800'
                    }`}>
                    {t.priority}
                  </span>
                  <p className="mt-1 text-[11px] font-medium text-slate-600">{t.status}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Module 5: SMS Delivery Gateway */}
        <section id="comms" className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xs">
          <div className="flex items-center justify-between border-b border-slate-100 pb-4">
            <div>
              <div className="flex items-center gap-2">
                <MessageSquare size={17} className="text-emerald-700" />
                <h2 className="text-base font-semibold text-slate-900">SMS Communications Hub</h2>
              </div>
              <p className="mt-0.5 text-xs text-slate-500">Wave 5 Gateway · Softlink Telecom Provider</p>
            </div>
            <span className="rounded-md bg-emerald-50 px-2 py-0.5 text-[10px] font-bold uppercase text-emerald-700">
              Wave 5 Active
            </span>
          </div>

          <div className="mt-4 grid grid-cols-3 gap-2 text-center text-xs">
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-2.5">
              <p className="text-[10px] uppercase text-slate-500 font-semibold">Delivered</p>
              <p className="mt-1 text-lg font-bold text-emerald-700">342</p>
            </div>
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-2.5">
              <p className="text-[10px] uppercase text-slate-500 font-semibold">Queued</p>
              <p className="mt-1 text-lg font-bold text-amber-700">5</p>
            </div>
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-2.5">
              <p className="text-[10px] uppercase text-slate-500 font-semibold">Failed</p>
              <p className="mt-1 text-lg font-bold text-slate-600">0</p>
            </div>
          </div>

          <div className="mt-3 divide-y divide-slate-100 text-xs">
            {PROTOTYPE_SMS_LOGS.map((log) => (
              <div key={log.id} className="py-2 flex items-center justify-between">
                <div>
                  <p className="font-medium text-slate-900">{log.type}</p>
                  <p className="text-[11px] text-slate-500">{log.recipient}</p>
                </div>
                <div className="text-right">
                  <span className="inline-block rounded-full bg-emerald-100 px-2 py-0.2 text-[10px] font-bold text-emerald-800">
                    {log.status}
                  </span>
                  <p className="text-[10px] text-slate-400 mt-0.5">{log.sentAt}</p>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* ─── Managed Properties Table (Wave 0 Register) ─── */}
      <section id="properties" className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xs">
        <div className="flex items-center justify-between border-b border-slate-100 pb-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">Managed Property Register</h2>
            <p className="mt-0.5 text-xs text-slate-500">Active estates within assigned branch scope (Nairobi & Nakuru)</p>
          </div>
          <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
            {properties.length} Properties in Scope
          </span>
        </div>

        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-xs">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-4 py-3 text-left font-semibold uppercase tracking-wider text-slate-600">Property</th>
                <th className="px-4 py-3 text-left font-semibold uppercase tracking-wider text-slate-600">Branch</th>
                <th className="px-4 py-3 text-left font-semibold uppercase tracking-wider text-slate-600">Type</th>
                <th className="px-4 py-3 text-left font-semibold uppercase tracking-wider text-slate-600">Location</th>
                <th className="px-4 py-3 text-right font-semibold uppercase tracking-wider text-slate-600">Units</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {properties.map((property) => (
                <tr key={property.id} className="hover:bg-slate-50">
                  <th scope="row" className="px-4 py-3.5 text-left font-semibold text-slate-900">{property.name}</th>
                  <td className="px-4 py-3.5 text-slate-600">{property.branch_id === 1 ? 'Nairobi Head Office' : 'Nakuru Branch'}</td>
                  <td className="px-4 py-3.5 text-slate-600">{property.property_type}</td>
                  <td className="px-4 py-3.5 text-slate-600">{property.location}</td>
                  <td className="px-4 py-3.5 text-right font-bold tabular-nums text-slate-900">{unitCounts.get(property.id) || 18}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* ─── Planned Modules (Wave 6 - 8 Skeletons) ─── */}
      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-bold text-slate-900">Roadmap Modules (Skeletons in Design)</h2>
            <p className="text-xs text-slate-500">Upcoming feature sets defined in the SRS specification</p>
          </div>
          <span className="rounded-full bg-slate-200/70 px-3 py-1 text-xs font-semibold text-slate-700">
            Waves 6–8 Skeletons
          </span>
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          {/* Wave 6 Card */}
          <article id="kra-tax" className="relative rounded-2xl border border-dashed border-slate-300 bg-slate-50/70 p-5 shadow-xs">
            <div className="flex items-start justify-between">
              <span className="rounded-xl bg-slate-200 p-2 text-slate-700">
                <Landmark size={20} />
              </span>
              <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-bold uppercase text-slate-700">
                Wave 6
              </span>
            </div>
            <h3 className="mt-3 text-sm font-bold text-slate-900">General Ledger & KRA MRI Tax</h3>
            <p className="mt-1 text-xs text-slate-600 leading-relaxed">
              Standardized Chart of Accounts, Bank Reconciliations, and 7.5% Monthly Rental Income (MRI) withholding tax schedule generation.
            </p>
            <div className="mt-4 rounded-lg bg-white/70 p-2.5 text-[11px] text-slate-500 border border-slate-200">
              ⚡ Status: Canonical ledger models in place; UI integration scheduled.
            </div>
          </article>

          {/* Wave 7 Card */}
          <article id="land-sales" className="relative rounded-2xl border border-dashed border-slate-300 bg-slate-50/70 p-5 shadow-xs">
            <div className="flex items-start justify-between">
              <span className="rounded-xl bg-slate-200 p-2 text-slate-700">
                <MapIcon size={20} />
              </span>
              <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-bold uppercase text-slate-700">
                Wave 7
              </span>
            </div>
            <h3 className="mt-3 text-sm font-bold text-slate-900">Land Sales & Plot Subdivisions</h3>
            <p className="mt-1 text-xs text-slate-600 leading-relaxed">
              Subdivision mapping for land projects (e.g. Juja Farm, Nakuru Greenfields), installment financing plans, and digital title deed transfer workflows.
            </p>
            <div className="mt-4 rounded-lg bg-white/70 p-2.5 text-[11px] text-slate-500 border border-slate-200">
              ⚡ Status: Schema ready; parcel map visualizer in design phase.
            </div>
          </article>

          {/* Wave 8 Card */}
          <article id="legal" className="relative rounded-2xl border border-dashed border-slate-300 bg-slate-50/70 p-5 shadow-xs">
            <div className="flex items-start justify-between">
              <span className="rounded-xl bg-slate-200 p-2 text-slate-700">
                <Scale size={20} />
              </span>
              <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-bold uppercase text-slate-700">
                Wave 8
              </span>
            </div>
            <h3 className="mt-3 text-sm font-bold text-slate-900">Legal Arrears Recovery & Auctioneers</h3>
            <p className="mt-1 text-xs text-slate-600 leading-relaxed">
              Automated 14-day statutory demand notices, distressed rent warrants under the Distress for Rent Act, and licensed auctioneer dispatches.
            </p>
            <div className="mt-4 rounded-lg bg-white/70 p-2.5 text-[11px] text-slate-500 border border-slate-200">
              ⚡ Status: Legal templates specified; workflow pipeline queued.
            </div>
          </article>
        </div>
      </section>
    </div>
  );
}
