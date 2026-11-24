'use client';

import { useEffect, useState } from 'react';
import {
  Home,
  CheckCircle,
  AlertCircle,
  Clock,
  Wrench,
  Droplets,
  ClipboardList,
  RefreshCw,
  Building2,
} from 'lucide-react';
import Link from 'next/link';

/* ─── Types matching Django GET /api/v1/caretaker/dashboard ─── */
type UnitPayload = {
  id: number;
  code: string;
  status: string;
  property_id: number;
  property_name: string;
  block_name: string | null;
  floor_name: string | null;
  unit_type: string;
  bedrooms: number;
  characteristics: Record<string, unknown>;
  tenant: { id: number; name: string } | null;
  has_metered_water?: boolean;
  reading_submitted_for_period: boolean;
  water_meter: string | null;
  water_meter_reading: string | null;
};

type DashboardSummary = {
  properties: number;
  units: number;
  occupied: number;
  notice_given: number;
  vacant: number;
  maintenance: number;
  readings_pending: number;
  checklists_pending: number;
  maintenance_tickets: number;
};

type PropertyInfo = {
  id: number;
  name: string;
  location: string;
  unit_count: number;
  has_paid_water?: boolean;
  has_metered_water?: boolean;
};

type ViewingPayload = {
  id: number;
  prospect_name: string;
  phone: string;
  property_name: string | null;
  unit_code: string | null;
  scheduled_for: string;
  notes: string;
};

type DashboardData = {
  period: string;
  readings_due: boolean;
  summary: DashboardSummary;
  properties: PropertyInfo[];
  units: UnitPayload[];
  viewings: ViewingPayload[];
};

type ExitInspection = {
  id: number;
  title: string;
  inspection_type: string;
  scheduled_for: string;
  property_name: string;
  unit_id: number | null;
  unit_code: string | null;
  status: string;
  checklist: { title: string; status?: string; notes?: string }[];
  photos: { storage_key: string; caption?: string }[];
  actual_move_out_date: string | null;
  final_readings: { utility_type: string; current_value: string }[];
  damage_items: { description: string; amount: string }[];
  maintenance_required: boolean;
  findings: string;
  recommendations: string;
  metered_utilities: {
    utility_type: string;
    name: string;
    meter: string | null;
    previous_reading: { current_value: string; period: string } | null;
  }[];
};

type ExitInspectionForm = {
  actual_move_out_date: string;
  checklist_confirmed: boolean;
  findings: string;
  recommendations: string;
  maintenance_required: boolean;
  readings: Record<string, string>;
  damage_items: { description: string; amount: string }[];
  draft_damage_description: string;
  draft_damage_amount: string;
};

/* ─── Status helpers ─── */
const STATUS_CONFIG: Record<string, { label: string; color: string }> = {
  draft:           { label: 'Draft',           color: 'text-slate-500   bg-slate-50   border-slate-200'   },
  occupied:        { label: 'Occupied',        color: 'text-emerald-700 bg-emerald-50 border-emerald-200' },
  notice_given:    { label: 'Moving Out',      color: 'text-amber-700   bg-amber-50   border-amber-200'   },
  vacant:          { label: 'Vacant',          color: 'text-slate-500   bg-slate-50   border-slate-200'   },
  deposit_pending: { label: 'Deposit Pending', color: 'text-purple-700  bg-purple-50  border-purple-200'  },
  maintenance:     { label: 'Maintenance',     color: 'text-red-700     bg-red-50     border-red-200'      },
  reserved:        { label: 'Reserved',        color: 'text-blue-700    bg-blue-50    border-blue-200'     },
  booked:          { label: 'Booked',          color: 'text-blue-700    bg-blue-50    border-blue-200'     },
  archived:        { label: 'Archived',        color: 'text-slate-500   bg-slate-50   border-slate-200'   },
};

function StatusBadge({ status }: { status: string }) {
  const cfg = STATUS_CONFIG[status] ?? { label: status, color: 'text-slate-500 bg-slate-100 border-slate-200' };
  return (
    <span className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-bold ${cfg.color}`}>
      {cfg.label}
    </span>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  tone = 'slate',
}: {
  icon: React.ElementType;
  label: string;
  value: number;
  tone?: 'emerald' | 'amber' | 'red' | 'blue' | 'slate';
}) {
  const tones = {
    emerald: 'bg-emerald-50 text-emerald-700',
    amber:   'bg-amber-50   text-amber-700',
    red:     'bg-red-50     text-red-700',
    blue:    'bg-blue-50    text-blue-700',
    slate:   'bg-slate-100  text-slate-600',
  };
  return (
    <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-xs font-medium text-slate-500">{label}</p>
          <p className="mt-1.5 text-2xl font-bold text-slate-900">{value}</p>
        </div>
        <span className={`rounded-lg p-2 ${tones[tone]}`}>
          <Icon size={18} aria-hidden="true" />
        </span>
      </div>
    </article>
  );
}

const EMPTY_CARETAKER_DATA: DashboardData = {
  period: '',
  readings_due: false,
  summary: {
    properties: 0,
    units: 0,
    occupied: 0,
    notice_given: 0,
    vacant: 0,
    maintenance: 0,
    readings_pending: 0,
    checklists_pending: 0,
    maintenance_tickets: 0,
  },
  properties: [],
  units: [],
  viewings: [],
};

export default function CaretakerDashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [floorFilter, setFloorFilter] = useState('all');
  const [propertyFilter, setPropertyFilter] = useState('all');
  const [blockFilter, setBlockFilter] = useState('all');
  const [unitTypeFilter, setUnitTypeFilter] = useState('all');
  const [bedroomFilter, setBedroomFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [unitSearch, setUnitSearch] = useState('');
  const [characteristicFilters, setCharacteristicFilters] = useState<Record<string, string>>({});
  const [exitInspections, setExitInspections] = useState<ExitInspection[]>([]);
  const [exitForms, setExitForms] = useState<Record<number, ExitInspectionForm>>({});
  const [inspectionPhotoFiles, setInspectionPhotoFiles] = useState<Record<number, File[]>>({});
  const [submittingInspection, setSubmittingInspection] = useState<number | null>(null);
  const [inspectionMessage, setInspectionMessage] = useState('');
  const [inspectionError, setInspectionError] = useState('');

  const loadDashboard = async (signal?: AbortSignal) => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/caretaker/dashboard', {
        cache: 'no-store',
        signal,
      });
      const payload = (await response.json()) as DashboardData & { error?: string };
      if (!response.ok) throw new Error(payload.error ?? 'Unable to load dashboard data.');
      setData(payload);
      const inspectionsResponse = await fetch('/api/caretaker/inspections', {
        cache: 'no-store',
        signal,
      });
      const inspectionsPayload = (await inspectionsResponse.json()) as {
        inspections?: ExitInspection[];
        error?: string;
      };
      if (!inspectionsResponse.ok) {
        throw new Error(inspectionsPayload.error ?? 'Unable to load assigned exit inspections.');
      }
      setExitInspections(
        (inspectionsPayload.inspections ?? []).filter(
          (inspection) =>
            ['move_out', 'exit'].includes(inspection.inspection_type.toLowerCase())
            && ['scheduled', 'in_progress'].includes(inspection.status),
        ),
      );
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') return;
      setData(null);
      setExitInspections([]);
      setError(err instanceof Error ? err.message : 'Unable to load dashboard data.');
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    void loadDashboard(controller.signal);
    return () => controller.abort();
  }, []);

  const displayData = data ?? EMPTY_CARETAKER_DATA;

  /* Derive filter options from units */
  const floors = [...new Set(displayData.units.map((u) => u.floor_name ?? '').filter(Boolean))].sort();
  const propertiesInRoster = [...new Map(
    displayData.units.map((unit) => [unit.property_id, unit.property_name]),
  )].sort((a, b) => a[1].localeCompare(b[1]));
  const blocks = [...new Set(displayData.units.map((unit) => unit.block_name ?? '').filter(Boolean))].sort();
  const unitTypes = [...new Set(displayData.units.map((unit) => unit.unit_type).filter(Boolean))].sort();
  const bedrooms = [...new Set(displayData.units.map((unit) => unit.bedrooms))]
    .sort((a, b) => a - b);
  const characteristicOptions = new Map<string, Set<string>>();
  for (const unit of displayData.units) {
    for (const [name, value] of Object.entries(unit.characteristics ?? {})) {
      if (value === null || value === undefined || typeof value === 'object') continue;
      const option = String(value).trim();
      if (!option) continue;
      const values = characteristicOptions.get(name) ?? new Set<string>();
      values.add(option);
      characteristicOptions.set(name, values);
    }
  }
  const visibleCharacteristicFilters = [...characteristicOptions.entries()]
    .filter(([, values]) => values.size > 1)
    .sort(([a], [b]) => a.localeCompare(b));
  const hasMeteredWater = displayData.properties.some((property) => property.has_metered_water);
  const hasPaidWater = displayData.properties.some((property) => property.has_paid_water);

  const filteredUnits = displayData.units.filter(
    (unit) => {
      const matchesSearch = `${unit.code} ${unit.tenant?.name ?? ''}`
        .toLowerCase()
        .includes(unitSearch.trim().toLowerCase());
      const matchesCharacteristics = Object.entries(characteristicFilters).every(
        ([name, value]) => !value || String(unit.characteristics?.[name] ?? '') === value,
      );
      return matchesSearch
        && (propertyFilter === 'all' || String(unit.property_id) === propertyFilter)
        && (blockFilter === 'all' || unit.block_name === blockFilter)
        && (floorFilter === 'all' || unit.floor_name === floorFilter)
        && (unitTypeFilter === 'all' || unit.unit_type === unitTypeFilter)
        && (bedroomFilter === 'all' || String(unit.bedrooms) === bedroomFilter)
        && (statusFilter === 'all' || unit.status === statusFilter)
        && matchesCharacteristics;
    },
  );

  const periodLabel = displayData.period
    ? new Date(`${displayData.period}T00:00:00`).toLocaleDateString('en-KE', {
        month: 'long',
        year: 'numeric',
        timeZone: 'Africa/Nairobi',
      })
    : '';

  const { summary } = displayData;
  const todayInNairobi = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Nairobi',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());

  function getExitForm(inspection: ExitInspection): ExitInspectionForm {
    return exitForms[inspection.id] ?? {
      actual_move_out_date: todayInNairobi,
      checklist_confirmed: false,
      findings: inspection.findings,
      recommendations: inspection.recommendations,
      maintenance_required: inspection.maintenance_required,
      readings: Object.fromEntries(
        inspection.final_readings.map((reading) => [reading.utility_type, reading.current_value]),
      ),
      damage_items: inspection.damage_items,
      draft_damage_description: '',
      draft_damage_amount: '',
    };
  }

  function updateExitForm(inspection: ExitInspection, update: Partial<ExitInspectionForm>) {
    setExitForms((forms) => ({
      ...forms,
      [inspection.id]: { ...getExitForm(inspection), ...update },
    }));
  }

  async function completeExitInspection(
    event: React.FormEvent<HTMLFormElement>,
    inspection: ExitInspection,
  ) {
    event.preventDefault();
    const form = getExitForm(inspection);
    setSubmittingInspection(inspection.id);
    setInspectionError('');
    setInspectionMessage('');
    try {
      if (form.draft_damage_description.trim() || form.draft_damage_amount.trim()) {
        throw new Error('Add or clear the in-progress damage item before submitting.');
      }
      let photos = inspection.photos;
      const newPhotos = inspectionPhotoFiles[inspection.id] ?? [];
      if (newPhotos.length > 0) {
        const formData = new FormData();
        newPhotos.forEach((photo) => formData.append('photos', photo));
        const uploadResponse = await fetch(
          `/api/caretaker/inspections/${inspection.id}/photos`,
          { method: 'POST', body: formData },
        );
        const uploadPayload = await uploadResponse.json();
        if (!uploadResponse.ok) {
          throw new Error(uploadPayload.error ?? 'Unable to upload inspection photos.');
        }
        photos = uploadPayload.inspection.photos as ExitInspection['photos'];
        setExitInspections((items) => items.map((item) => item.id === inspection.id ? { ...item, photos } : item));
        setInspectionPhotoFiles((files) => ({ ...files, [inspection.id]: [] }));
      }
      const response = await fetch(`/api/caretaker/inspections/${inspection.id}/complete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          actual_move_out_date: form.actual_move_out_date,
          checklist: inspection.checklist.map((item) => ({ ...item, status: 'complete' })),
          photos,
          findings: form.findings,
          recommendations: form.recommendations,
          maintenance_required: form.maintenance_required,
          final_readings: inspection.metered_utilities.map((utility) => ({
            utility_type: utility.utility_type,
            current_value: form.readings[utility.utility_type] ?? '',
          })),
          damage_items: form.damage_items,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? 'Unable to submit the exit inspection.');
      setExitInspections((items) => items.filter((item) => item.id !== inspection.id));
      setInspectionMessage(`Exit inspection for ${inspection.unit_code ?? inspection.property_name} submitted.`);
    } catch (error) {
      setInspectionError(error instanceof Error ? error.message : 'Unable to submit the exit inspection.');
    } finally {
      setSubmittingInspection(null);
    }
  }

  if (loading && !data) {
    return <p role="status" className="p-4 text-sm text-slate-600">Loading assigned properties…</p>;
  }
  if (error && !data) {
    return (
      <div role="alert" className="m-4 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
        <p>{error}</p>
        <button type="button" onClick={() => void loadDashboard()} className="mt-3 font-semibold underline">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-7">
      {error && (
        <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          {error}
          <button type="button" onClick={() => void loadDashboard()} className="ml-2 font-semibold underline">
            Retry
          </button>
        </div>
      )}
      {!loading && !error && displayData.properties.length === 0 && (
        <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          No properties are currently assigned to your account. Contact your Property Officer.
        </div>
      )}
      {/* ── Hero header ── */}
      <section className="relative overflow-hidden rounded-[1.75rem] bg-[#123b2c] px-7 py-8 text-white shadow-xl shadow-emerald-950/10 sm:px-9">
        <div className="absolute -right-12 -top-24 h-72 w-72 rounded-full border border-white/10" />
        <div className="absolute -right-2 -top-14 h-52 w-52 rounded-full border border-white/10" />
        <div className="relative flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-emerald-200">Caretaker workspace</p>
            <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">Property operations</h1>
            <p className="mt-2 text-sm text-emerald-50/75">
              {summary.properties} propert{summary.properties === 1 ? 'y' : 'ies'} · {summary.units} unit{summary.units === 1 ? '' : 's'} · {periodLabel}
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadDashboard()}
            className="inline-flex items-center gap-2 rounded-xl border border-white/20 bg-white/10 px-3 py-2.5 text-sm font-semibold text-white hover:bg-white/15"
          >
            <RefreshCw size={15} /> Refresh
          </button>
        </div>
      </section>

      {/* ── Stat cards ── */}
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-label="Summary stats">
        <StatCard icon={Building2}    label="Occupied"          value={summary.occupied}             tone="emerald" />
        <StatCard icon={Wrench}       label="Open Tickets"      value={summary.maintenance_tickets}  tone="amber"   />
        {hasMeteredWater && <StatCard icon={Droplets} label={displayData.readings_due ? 'Readings Pending' : 'Readings Due at Month-End'} value={summary.readings_pending} tone={summary.readings_pending > 0 ? 'red' : 'slate'} />}
        <StatCard icon={ClipboardList} label="Checklist Items"  value={summary.checklists_pending}   tone={summary.checklists_pending > 0 ? 'amber' : 'slate'} />
      </section>

      {/* ── Action shortcuts ── */}
      {(hasMeteredWater && summary.readings_pending > 0 || summary.maintenance_tickets > 0) && (
        <section className="flex flex-wrap gap-3" aria-label="Quick actions">
          {hasMeteredWater && summary.readings_pending > 0 && (
            <Link
              href="/caretaker/meters"
              className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-emerald-700"
            >
              <Droplets size={16} /> Submit {summary.readings_pending} water reading{summary.readings_pending === 1 ? '' : 's'}
            </Link>
          )}
          {summary.maintenance_tickets > 0 && (
            <Link
              href="/caretaker/tickets"
              className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 shadow-sm hover:bg-slate-50"
            >
              <Wrench size={16} /> View {summary.maintenance_tickets} open ticket{summary.maintenance_tickets === 1 ? '' : 's'}
            </Link>
          )}
        </section>
      )}

      <section className="grid gap-4 lg:grid-cols-2" aria-label="Caretaker daily work">
        <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="font-semibold text-slate-900">Today&apos;s schedule</h2>
              <p className="mt-1 text-sm text-slate-500">Assigned property viewings and field tasks.</p>
            </div>
            <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
              {displayData.viewings.length} viewing{displayData.viewings.length === 1 ? '' : 's'}
            </span>
          </div>
          <div className="mt-4 space-y-3">
            {displayData.viewings.map((viewing) => (
              <article key={viewing.id} className="rounded-xl bg-slate-50 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="font-semibold text-slate-900">{viewing.prospect_name}</p>
                    <p className="mt-1 text-xs text-slate-500">
                      {viewing.property_name ?? 'Assigned property'}
                      {viewing.unit_code ? ` · ${viewing.unit_code}` : ''}
                    </p>
                    <p className="mt-1 text-xs font-medium text-slate-600">
                      {new Date(viewing.scheduled_for).toLocaleTimeString('en-KE', {
                        hour: 'numeric',
                        minute: '2-digit',
                        timeZone: 'Africa/Nairobi',
                      })}
                    </p>
                  </div>
                  {viewing.phone && (
                    <a
                      href={`tel:${viewing.phone}`}
                      className="rounded-lg bg-emerald-700 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-800"
                    >
                      Call client
                    </a>
                  )}
                </div>
              </article>
            ))}
            {summary.maintenance_tickets > 0 && (
              <Link href="/caretaker/tickets" className="text-sm font-semibold text-emerald-800 hover:underline">
                Review {summary.maintenance_tickets} open maintenance ticket{summary.maintenance_tickets === 1 ? '' : 's'}
              </Link>
            )}
            {displayData.viewings.length === 0 && summary.maintenance_tickets === 0 && (
              <p className="rounded-xl bg-slate-50 p-4 text-sm text-slate-600">No viewings or open field tickets are scheduled today.</p>
            )}
          </div>
        </article>

        <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="font-semibold text-slate-900">Field actions</h2>
              <p className="mt-1 text-sm text-slate-500">Open the tools available for your assigned properties.</p>
            </div>
            <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-800">Assigned work</span>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {hasPaidWater && (
              <Link href="/caretaker/meters" className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm font-semibold text-emerald-900 hover:bg-emerald-100">
                <Droplets size={18} className="mb-2" />
                Open water meter reader
              </Link>
            )}
            <Link href="/caretaker/checklists" className="rounded-xl border border-slate-200 bg-white p-4 text-sm font-semibold text-slate-800 hover:bg-slate-50">
              <ClipboardList size={18} className="mb-2 text-emerald-700" />
              Open property checklists
            </Link>
            <Link href="/caretaker/tickets" className="rounded-xl border border-slate-200 bg-white p-4 text-sm font-semibold text-slate-800 hover:bg-slate-50">
              <Wrench size={18} className="mb-2 text-emerald-700" />
              Open on-site tickets
            </Link>
            {!hasPaidWater && (
              <p className="rounded-xl bg-slate-50 p-4 text-sm text-slate-600">
                Water meter readings are not enabled because none of your assigned properties currently has a paid water utility.
              </p>
            )}
          </div>
        </article>
      </section>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-label="Assigned properties">
        {displayData.properties.map((property) => (
          <article key={property.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="font-semibold text-slate-900">{property.name}</h2>
                <p className="mt-1 text-xs text-slate-500">{property.location}</p>
              </div>
              {property.has_paid_water && (
                <span className="rounded-full bg-sky-50 px-2.5 py-1 text-[10px] font-semibold text-sky-800">
                  Paid water
                </span>
              )}
            </div>
            <p className="mt-3 text-xs text-slate-600">{property.unit_count} assigned units</p>
          </article>
        ))}
      </section>

      <section id="move-out-inspections" className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" aria-label="Move-in and move-out checklists">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-semibold text-slate-900">Move-in &amp; move-out checklists</h2>
            <p className="mt-1 text-sm text-slate-500">Record unit condition, meter readings and damages during resident transitions.</p>
          </div>
          <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
            {summary.checklists_pending} awaiting completion
          </span>
        </div>
        {inspectionError && <p role="alert" className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-800">{inspectionError}</p>}
        {inspectionMessage && <p role="status" className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800">{inspectionMessage}</p>}
        {exitInspections.length === 0 ? (
          <p className="mt-4 rounded-xl bg-slate-50 p-4 text-sm text-slate-600">No assigned exit inspections are awaiting completion.</p>
        ) : (
          <div className="mt-4 space-y-4">
            {exitInspections.map((inspection) => {
              const form = getExitForm(inspection);
              const scheduledDate = inspection.scheduled_for.slice(0, 10);
              const inspectionDue = todayInNairobi >= scheduledDate;
              return (
                <form key={inspection.id} onSubmit={(event) => void completeExitInspection(event, inspection)} className="space-y-4 rounded-xl border border-slate-200 p-4">
                  <div>
                    <h3 className="font-semibold text-slate-900">{inspection.title} · {inspection.property_name} / {inspection.unit_code ?? 'Unit'}</h3>
                    <p className="mt-1 text-xs text-slate-500">
                      Pending · scheduled for {scheduledDate}. Complete the checklist and provide final readings and damage estimates.
                    </p>
                    <ul className="mt-2 list-inside list-disc text-xs text-slate-600">
                      {inspection.checklist.map((item, index) => <li key={`${item.title}-${index}`}>{item.title}</li>)}
                    </ul>
                  </div>
                  <label className="block text-sm font-medium text-slate-700">Actual move-out date
                    <input required type="date" min={scheduledDate} max={todayInNairobi} value={form.actual_move_out_date} onChange={(event) => updateExitForm(inspection, { actual_move_out_date: event.target.value })} className="mt-1 block w-full rounded-lg border px-3 py-2" />
                  </label>
                  <fieldset>
                    <legend className="text-sm font-semibold text-slate-700">Final meter readings</legend>
                    {inspection.metered_utilities.length === 0 && <p className="mt-2 text-sm text-slate-500">No metered utilities are configured for this unit.</p>}
                    <div className="mt-2 space-y-2">
                      {inspection.metered_utilities.map((utility) => (
                        <label key={utility.utility_type} className="grid gap-1 text-sm text-slate-700 sm:grid-cols-2">
                          <span>{utility.name} · Meter {utility.meter || 'not configured'} · Previous {utility.previous_reading?.current_value ?? 'unavailable'}</span>
                          <input required type="number" step="0.0001" min={utility.previous_reading?.current_value ?? '0'} value={form.readings[utility.utility_type] ?? ''} onChange={(event) => updateExitForm(inspection, { readings: { ...form.readings, [utility.utility_type]: event.target.value } })} className="rounded-lg border px-3 py-2" placeholder="Current reading" />
                        </label>
                      ))}
                    </div>
                  </fieldset>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="text-sm font-medium text-slate-700">Findings
                      <textarea value={form.findings} onChange={(event) => updateExitForm(inspection, { findings: event.target.value })} className="mt-1 w-full rounded-lg border px-3 py-2" rows={3} />
                    </label>
                    <label className="text-sm font-medium text-slate-700">Recommendations
                      <textarea value={form.recommendations} onChange={(event) => updateExitForm(inspection, { recommendations: event.target.value })} className="mt-1 w-full rounded-lg border px-3 py-2" rows={3} />
                    </label>
                  </div>
                  <fieldset>
                    <legend className="text-sm font-semibold text-slate-700">Itemized damage estimates</legend>
                    <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_12rem_auto]">
                      <input value={form.draft_damage_description} onChange={(event) => updateExitForm(inspection, { draft_damage_description: event.target.value })} className="rounded-lg border px-3 py-2 text-sm" maxLength={255} placeholder="Damage description" />
                      <input type="number" min="0.01" step="0.01" value={form.draft_damage_amount} onChange={(event) => updateExitForm(inspection, { draft_damage_amount: event.target.value })} className="rounded-lg border px-3 py-2 text-sm" disabled={!form.draft_damage_description.trim()} placeholder="Estimate (KES)" />
                      <button type="button" disabled={!form.draft_damage_description.trim() || !form.draft_damage_amount} onClick={() => updateExitForm(inspection, {
                        damage_items: [...form.damage_items, { description: form.draft_damage_description.trim(), amount: form.draft_damage_amount }],
                        draft_damage_description: '',
                        draft_damage_amount: '',
                      })} className="rounded-lg border px-3 py-2 text-sm font-semibold disabled:opacity-50">Add item</button>
                    </div>
                    <ul className="mt-2 space-y-1">
                      {form.damage_items.map((item, index) => (
                        <li key={`${item.description}-${index}`} className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-sm">
                          <span>{item.description} · KES {item.amount}</span>
                          <button type="button" onClick={() => updateExitForm(inspection, { damage_items: form.damage_items.filter((_, itemIndex) => itemIndex !== index) })} className="font-semibold text-red-700">Remove</button>
                        </li>
                      ))}
                    </ul>
                  </fieldset>
                  <label className="block text-sm font-medium text-slate-700">Add exit inspection photos
                    <input type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={(event) => setInspectionPhotoFiles((files) => ({ ...files, [inspection.id]: Array.from(event.target.files ?? []) }))} className="mt-1 block w-full rounded-lg border px-3 py-2 text-sm" />
                    <span className="mt-1 block text-xs font-normal text-slate-500">JPEG, PNG, or WebP; up to 5 photos per upload and 5 MB per file.</span>
                  </label>
                  <label className="flex items-center gap-2 text-sm text-slate-700">
                    <input type="checkbox" checked={form.maintenance_required} onChange={(event) => updateExitForm(inspection, { maintenance_required: event.target.checked })} />
                    Keep the unit under maintenance after the tenant moves out
                  </label>
                  <label className="flex items-start gap-2 text-sm text-slate-700">
                    <input required type="checkbox" checked={form.checklist_confirmed} onChange={(event) => updateExitForm(inspection, { checklist_confirmed: event.target.checked })} className="mt-1" />
                    I confirm that the inspection checklist and available photo evidence have been reviewed.
                  </label>
                  <button type="submit" disabled={!inspectionDue || submittingInspection === inspection.id || inspection.metered_utilities.some((utility) => !form.readings[utility.utility_type])} className="rounded-lg bg-emerald-800 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
                    {submittingInspection === inspection.id ? 'Submitting…' : 'Submit exit inspection'}
                  </button>
                </form>
              );
            })}
          </div>
        )}
      </section>

      {/* ── Unit roster ── */}
      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm" aria-label="Unit roster">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 p-5">
          <div>
            <h2 className="text-lg font-bold text-slate-900">Unit roster</h2>
            <p className="mt-0.5 text-sm text-slate-500">
              {summary.occupied} occupied · {summary.vacant} vacant · {summary.notice_given} moving out
            </p>
          </div>
          <div className="grid w-full gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <input
              type="search"
              value={unitSearch}
              onChange={(event) => setUnitSearch(event.target.value)}
              placeholder="Search unit or tenant"
              aria-label="Search units or tenants"
              className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-500"
            />
            <select
              value={propertyFilter}
              onChange={(event) => setPropertyFilter(event.target.value)}
              aria-label="Filter by property"
              className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-500"
            >
              <option value="all">All properties</option>
              {propertiesInRoster.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select>
            <select
              value={blockFilter}
              onChange={(event) => setBlockFilter(event.target.value)}
              aria-label="Filter by block"
              className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-500"
            >
              <option value="all">All blocks</option>
              {blocks.map((block) => <option key={block} value={block}>{block}</option>)}
            </select>
            <select
              value={floorFilter}
              onChange={(e) => setFloorFilter(e.target.value)}
              aria-label="Filter by floor"
              className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-500"
            >
              <option value="all">All floors</option>
              {floors.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
            <select
              value={unitTypeFilter}
              onChange={(event) => setUnitTypeFilter(event.target.value)}
              aria-label="Filter by unit type"
              className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-500"
            >
              <option value="all">All unit types</option>
              {unitTypes.map((unitType) => <option key={unitType} value={unitType}>{unitType}</option>)}
            </select>
            <select
              value={bedroomFilter}
              onChange={(event) => setBedroomFilter(event.target.value)}
              aria-label="Filter by bedrooms"
              className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-500"
            >
              <option value="all">All bedroom counts</option>
              {bedrooms.map((count) => <option key={count} value={count}>{count} bedroom{count === 1 ? '' : 's'}</option>)}
            </select>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              aria-label="Filter by unit status"
              className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-500"
            >
              <option value="all">All statuses</option>
              {[...new Set(displayData.units.map((unit) => unit.status))].sort().map((status) => (
                <option key={status} value={status}>{STATUS_CONFIG[status]?.label ?? status.replaceAll('_', ' ')}</option>
              ))}
            </select>
            {visibleCharacteristicFilters.map(([name, values]) => (
              <select
                key={name}
                value={characteristicFilters[name] ?? ''}
                onChange={(event) => setCharacteristicFilters((filters) => ({
                  ...filters,
                  [name]: event.target.value,
                }))}
                aria-label={`Filter by ${name.replaceAll('_', ' ')}`}
                className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-500"
              >
                <option value="">All {name.replaceAll('_', ' ')}</option>
                {[...values].sort().map((value) => <option key={value} value={value}>{value}</option>)}
              </select>
            ))}
          </div>
        </div>

        {filteredUnits.length === 0 ? (
          <div className="p-10 text-center">
            <Home size={36} className="mx-auto text-slate-300" />
            <p className="mt-3 font-semibold text-slate-700">No units match the selected filters</p>
            <p className="mt-1 text-sm text-slate-400">
              {displayData.units.length === 0
                ? 'No properties have been assigned to your account yet. Contact the branch manager.'
                : 'Try changing the search or unit filters.'}
            </p>
          </div>
        ) : (
          <>
          <div className="space-y-3 p-4 md:hidden">
            {filteredUnits.map((unit) => (
              <article key={unit.id} className="rounded-xl border border-slate-200 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="font-bold text-slate-900">{unit.code}</p>
                    <p className="mt-1 text-xs text-slate-500">
                      {unit.property_name}
                      {[unit.block_name, unit.floor_name].filter(Boolean).map((name) => ` · ${name}`).join('')}
                    </p>
                  </div>
                  <StatusBadge status={unit.status} />
                </div>
                <p className="mt-3 text-sm text-slate-600">{unit.tenant?.name || 'No tenant linked'}</p>
                {unit.has_metered_water && (unit.status === 'occupied' || unit.status === 'notice_given') && (
                  <div className="mt-3 border-t border-slate-100 pt-3">
                    <p className="text-xs text-slate-500">
                      Meter {unit.water_meter ?? '—'} · Active reading {unit.water_meter_reading ?? '—'} m³
                    </p>
                    {!displayData.readings_due ? (
                      <span className="mt-1 block text-xs text-slate-500">Next reading due at month-end</span>
                    ) : unit.reading_submitted_for_period ? (
                      <span className="mt-1 block text-xs font-semibold text-emerald-700">Water reading submitted</span>
                    ) : (
                      <Link href="/caretaker/meters" className="mt-1 inline-block text-xs font-semibold text-amber-700 hover:underline">Water reading pending · Record</Link>
                    )}
                  </div>
                )}
              </article>
            ))}
          </div>
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="bg-slate-50">
                <tr>
                  <th className="p-4 font-semibold text-slate-600">Unit</th>
                  <th className="p-4 font-semibold text-slate-600">Property / Floor</th>
                  <th className="p-4 font-semibold text-slate-600">Tenant</th>
                  <th className="p-4 font-semibold text-slate-600">Status</th>
                  <th className="p-4 font-semibold text-slate-600 text-center">Water reading</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredUnits.map((unit) => (
                  <tr key={unit.id} className="hover:bg-slate-50 transition-colors">
                    <td className="p-4 font-bold text-slate-900">{unit.code}</td>
                    <td className="p-4 text-slate-600">
                      <span className="block">{unit.property_name}</span>
                      {(unit.block_name || unit.floor_name) && (
                        <span className="text-xs text-slate-400">
                          {[unit.block_name, unit.floor_name].filter(Boolean).join(' · ')}
                        </span>
                      )}
                    </td>
                    <td className="p-4 text-slate-700">
                      {unit.tenant ? unit.tenant.name : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>
                    <td className="p-4">
                      <StatusBadge status={unit.status} />
                    </td>
                    <td className="p-4 text-center">
                      {unit.has_metered_water && (unit.status === 'occupied' || unit.status === 'notice_given') ? (
                        <div className="space-y-1">
                          <span className="block text-xs text-slate-600">
                            {unit.water_meter_reading ?? '—'} m³{unit.water_meter ? ` · ${unit.water_meter}` : ''}
                          </span>
                          {!displayData.readings_due ? (
                            <span className="text-xs text-slate-400">Due at month-end</span>
                          ) : unit.reading_submitted_for_period ? (
                            <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-0.5 text-xs font-bold text-emerald-700">
                              <CheckCircle size={12} /> Submitted
                            </span>
                          ) : (
                            <Link
                              href="/caretaker/meters"
                              className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-xs font-bold text-amber-700 hover:bg-amber-100"
                            >
                              <AlertCircle size={12} /> Pending
                            </Link>
                          )}
                        </div>
                      ) : (
                        <span className="text-slate-300">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </>
        )}
      </section>
    </div>
  );
}
