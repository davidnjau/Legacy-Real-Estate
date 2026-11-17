'use client';

import { useCallback, useEffect, useState } from 'react';
import { Building2, RefreshCw, MapPin, Layers, Phone, ClipboardCheck } from 'lucide-react';

type PropertyUnit = {
  id: number;
  code: string;
  unit_type: string;
  monthly_rent: string | null;
  status: string;
  tenant_name: string | null;
  balance: string;
};

type InspectionReport = {
  id: number;
  title: string;
  inspection_type: string;
  scheduled_for: string;
  completed_at: string | null;
  unit_code: string | null;
  responsible_staff: string;
  checklist: { title: string; completed?: boolean; notes?: string }[];
  findings: string;
  recommendations: string;
  photos: { index: number; caption: string; content_type: string }[];
};

type PropertyRecord = {
  id: number;
  branch_id: number;
  branch_name: string;
  name: string;
  property_type: string;
  location: string;
  address: string | null;
  expected_unit_count?: number | null;
  management_agreement: {
    commission_model: string;
    commission_rate: string | null;
    fixed_fee: string | null;
    payout_day: number | null;
  } | null;
  caretakers: { id: number; name: string; phone: string; email: string }[];
  units: PropertyUnit[];
  inspection_reports: InspectionReport[];
};

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 2,
});

function money(value: string | null) {
  return value === null ? 'Not set' : currency.format(Number(value));
}

export default function PropertiesPage() {
  const [properties, setProperties] = useState<PropertyRecord[]>([]);
  const [selectedPropertyId, setSelectedPropertyId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const loadProperties = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/landlord/property-records', {
        cache: 'no-store',
        signal,
      });
      const payload = (await response.json()) as {
        properties?: PropertyRecord[];
        error?: string;
      };
      if (!response.ok) {
        throw new Error(payload.error || 'Unable to load your properties.');
      }
      if (!Array.isArray(payload.properties)) {
        throw new Error('The property response was incomplete.');
      }
      setProperties(payload.properties);
      setSelectedPropertyId((current) =>
        payload.properties?.some((property) => property.id === current)
          ? current
          : payload.properties?.[0]?.id ?? null,
      );
    } catch (loadError) {
      if (loadError instanceof Error && loadError.name === 'AbortError') return;
      setError(loadError instanceof Error ? loadError.message : 'Unable to load your properties.');
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadProperties(controller.signal);
    return () => controller.abort();
  }, [loadProperties]);

  const selectedProperty =
    properties.find((property) => property.id === selectedPropertyId) ?? null;

  return (
    <div className="max-w-6xl space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-emerald-700">Portfolio</p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">My Properties</h1>
          <p className="mt-1 text-sm text-slate-600">
            Properties linked to your landlord account.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void loadProperties()}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 shadow-2xs hover:bg-slate-50 disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </header>

      {error && (
        <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          Could not load property data: {error}
        </p>
      )}

      {/* Property Cards Grid */}
      {loading ? (
        <p role="status" className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-600">
          Loading properties…
        </p>
      ) : properties.length === 0 && !error ? (
        <p className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-600">
          No properties are linked to this landlord account.
        </p>
      ) : properties.length > 0 ? (
        <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {properties.map((prop) => (
            <button
              key={prop.id}
              type="button"
              onClick={() => setSelectedPropertyId(prop.id)}
              aria-pressed={selectedPropertyId === prop.id}
              className={`rounded-2xl border bg-white p-5 text-left shadow-xs transition hover:shadow-sm ${
                selectedPropertyId === prop.id
                  ? 'border-emerald-500 ring-2 ring-emerald-100'
                  : 'border-slate-200'
              }`}
            >
              <div className="flex items-start justify-between">
                <span className="rounded-xl bg-emerald-50 p-2.5 text-emerald-700">
                  <Building2 size={22} />
                </span>
                <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-[10px] font-bold text-slate-700">
                  {prop.branch_name}
                </span>
              </div>

              <h2 className="mt-4 text-base font-bold text-slate-900">{prop.name}</h2>
              <p className="mt-0.5 text-xs text-slate-500">{prop.property_type}</p>

              <div className="mt-4 space-y-2 border-t border-slate-100 pt-3 text-xs text-slate-600">
                <div className="flex items-center gap-2">
                  <MapPin size={14} className="text-slate-400" />
                  <span>{prop.location}</span>
                </div>
                <div className="flex items-center gap-2">
                  <Layers size={14} className="text-slate-400" />
                  <span>
                    {prop.expected_unit_count == null
                      ? 'Expected unit count not set'
                      : `${prop.expected_unit_count} expected units`}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Building2 size={14} className="text-slate-400" />
                  <span>{prop.units.length} lettable units</span>
                </div>
              </div>
            </button>
          ))}
        </section>
      ) : null}

      {selectedProperty && (
        <section className="space-y-5 rounded-2xl border border-slate-200 bg-white p-5 shadow-xs sm:p-6">
          <div className="border-b border-slate-100 pb-4">
            <p className="text-xs font-bold uppercase tracking-wider text-emerald-700">Property details</p>
            <h2 className="mt-1 text-xl font-bold text-slate-900">{selectedProperty.name}</h2>
            <p className="mt-1 text-sm text-slate-600">
              {selectedProperty.address || selectedProperty.location} · {selectedProperty.branch_name}
            </p>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <article className="rounded-xl border border-slate-200 bg-slate-50 p-4">
              <h3 className="text-sm font-bold text-slate-900">Management agreement</h3>
              {selectedProperty.management_agreement ? (
                <div className="mt-2 space-y-1 text-sm text-slate-600">
                  <p>
                    Commission:{' '}
                    {selectedProperty.management_agreement.commission_model === 'percentage'
                      ? `${selectedProperty.management_agreement.commission_rate ?? 'Not set'}%`
                      : `Fixed fee ${money(selectedProperty.management_agreement.fixed_fee)}`}
                  </p>
                  <p>
                    Payout day: {selectedProperty.management_agreement.payout_day ?? 'Not set'}
                  </p>
                  <p className="text-xs text-slate-500">
                    Agreement is landlord-level and may cover more than this property.
                  </p>
                </div>
              ) : (
                <p className="mt-2 text-sm text-slate-500">No current management agreement is linked.</p>
              )}
            </article>

            <article className="rounded-xl border border-slate-200 bg-slate-50 p-4">
              <h3 className="text-sm font-bold text-slate-900">Assigned caretakers</h3>
              {selectedProperty.caretakers.length === 0 ? (
                <p className="mt-2 text-sm text-slate-500">No active caretaker assignment.</p>
              ) : (
                <ul className="mt-2 space-y-2">
                  {selectedProperty.caretakers.map((caretaker) => (
                    <li key={caretaker.id} className="text-sm text-slate-700">
                      <span className="font-semibold">{caretaker.name || caretaker.email}</span>
                      {caretaker.phone && (
                        <a
                          href={`tel:${caretaker.phone}`}
                          className="ml-2 inline-flex items-center gap-1 text-emerald-800 hover:underline"
                        >
                          <Phone size={13} aria-hidden="true" /> {caretaker.phone}
                        </a>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </article>
          </div>

          <div className="overflow-hidden rounded-xl border border-slate-200">
            <div className="border-b border-slate-100 bg-slate-50 p-4">
              <h3 className="text-sm font-bold text-slate-900">Units &amp; current balances</h3>
            </div>
            {selectedProperty.units.length === 0 ? (
              <p className="p-4 text-sm text-slate-500">No lettable units are recorded for this property.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-slate-200 text-sm">
                  <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th scope="col" className="px-4 py-3">Unit</th>
                      <th scope="col" className="px-4 py-3">Type</th>
                      <th scope="col" className="px-4 py-3">Tenant</th>
                      <th scope="col" className="px-4 py-3">Status</th>
                      <th scope="col" className="px-4 py-3">Monthly rent</th>
                      <th scope="col" className="px-4 py-3">Current balance</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {selectedProperty.units.map((unit) => (
                      <tr key={unit.id}>
                        <th scope="row" className="px-4 py-3 text-left font-semibold text-slate-900">
                          {unit.code}
                        </th>
                        <td className="px-4 py-3">{unit.unit_type}</td>
                        <td className="px-4 py-3">{unit.tenant_name || '—'}</td>
                        <td className="px-4 py-3">{unit.status}</td>
                        <td className="whitespace-nowrap px-4 py-3">{money(unit.monthly_rent)}</td>
                        <td className="whitespace-nowrap px-4 py-3">{money(unit.balance)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="overflow-hidden rounded-xl border border-slate-200">
            <div className="border-b border-slate-100 bg-slate-50 p-4">
              <h3 className="text-sm font-bold text-slate-900">Shared inspection reports</h3>
            </div>
            {selectedProperty.inspection_reports.length === 0 ? (
              <p className="p-4 text-sm text-slate-500">No completed inspection reports have been shared.</p>
            ) : (
              <div className="divide-y divide-slate-100">
                {selectedProperty.inspection_reports.map((report) => (
                  <article key={report.id} className="space-y-2 p-4">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <h4 className="font-semibold text-slate-900">{report.title}</h4>
                        <p className="text-xs text-slate-500">
                          {report.inspection_type}
                          {report.unit_code ? ` · Unit ${report.unit_code}` : ' · Common areas'}
                          {report.completed_at ? ` · Completed ${new Date(report.completed_at).toLocaleDateString('en-KE')}` : ''}
                        </p>
                      </div>
                      <span className="inline-flex items-center gap-1 text-xs text-slate-500">
                        <ClipboardCheck size={14} aria-hidden="true" /> {report.responsible_staff}
                      </span>
                    </div>
                    {report.checklist.length > 0 && (
                      <ul className="list-inside list-disc text-sm text-slate-600">
                        {report.checklist.map((item, index) => (
                          <li key={`${report.id}-${index}`}>
                            {item.title}{item.notes ? ` — ${item.notes}` : ''}
                          </li>
                        ))}
                      </ul>
                    )}
                    {report.findings && <p className="text-sm text-slate-700"><strong>Findings:</strong> {report.findings}</p>}
                    {report.recommendations && <p className="text-sm text-slate-700"><strong>Recommendations:</strong> {report.recommendations}</p>}
                    {report.photos.length > 0 && (
                      <div className="flex flex-wrap gap-3">
                        {report.photos.map((photo) => (
                          <a
                            key={`${report.id}-${photo.index}`}
                            href={`/api/landlord/inspections/${report.id}/photos/${photo.index}`}
                            target="_blank"
                            rel="noreferrer"
                            className="text-sm font-semibold text-emerald-800 hover:underline"
                          >
                            View photo{photo.caption ? `: ${photo.caption}` : ''}
                          </a>
                        ))}
                      </div>
                    )}
                  </article>
                ))}
              </div>
            )}
          </div>
        </section>
      )}

      {/* Property Summary Table */}
      {properties.length > 0 && (
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xs">
          <div className="border-b border-slate-100 p-4">
            <h2 className="text-sm font-bold text-slate-900">Portfolio Register</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-xs">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-5 py-3 text-left font-semibold uppercase tracking-wider text-slate-600">Property</th>
                  <th className="px-5 py-3 text-left font-semibold uppercase tracking-wider text-slate-600">Branch</th>
                  <th className="px-5 py-3 text-left font-semibold uppercase tracking-wider text-slate-600">Type</th>
                  <th className="px-5 py-3 text-left font-semibold uppercase tracking-wider text-slate-600">Location</th>
                  <th className="px-5 py-3 text-right font-semibold uppercase tracking-wider text-slate-600">Expected Units</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {properties.map((property) => (
                  <tr key={property.id} className="hover:bg-slate-50/50">
                    <th scope="row" className="px-5 py-3.5 text-left font-bold text-slate-900">{property.name}</th>
                    <td className="px-5 py-3.5 text-slate-600">{property.branch_name}</td>
                    <td className="px-5 py-3.5 text-slate-600">{property.property_type}</td>
                    <td className="px-5 py-3.5 text-slate-600">{property.location}</td>
                    <td className="px-5 py-3.5 text-right font-bold text-slate-900">
                      {property.expected_unit_count ?? 'Not set'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
