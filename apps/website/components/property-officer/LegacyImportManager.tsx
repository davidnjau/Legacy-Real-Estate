'use client';

import { useEffect, useState } from 'react';
import { usePropertyOfficerPortal } from './PortalShell';

type ImportReport = {
  mode: 'preview' | 'committed';
  created: Record<string, number>;
  skipped: { sheet: string; row: number; reason: string }[];
  errors: { sheet: string; row: number; message: string }[];
  activation?: {
    prepared: number;
    sent: number;
    sms_queued?: number;
    failed: { unit_code: string; email: string; message: string }[];
  };
};

type PropertyOption = {
  id: number;
  branch_id: number;
  name: string;
  default_handover_items?: { name: string; quantity: number }[];
  has_water_utility?: boolean;
  unit_types?: {
    name: string;
    unit_count: number;
    monthly_rent: string;
    utilities?: { utility_type: string; billing_method: string }[];
  }[];
};

type PropertiesResponse = {
  properties?: PropertyOption[];
  error?: string;
  detail?: string;
};

export default function LegacyImportManager() {
  const { agency, branchIds } = usePropertyOfficerPortal();
  const [properties, setProperties] = useState<PropertyOption[]>([]);
  const [propertyId, setPropertyId] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadingProperties, setLoadingProperties] = useState(true);
  const selectedProperty = properties.find((property) => String(property.id) === propertyId);
  const hasWaterUtility = selectedProperty?.has_water_utility ?? (
    selectedProperty?.unit_types?.some((unitType) =>
      unitType.utilities?.some((utility) => utility.utility_type === 'water'),
    ) ?? false
  );
  const templates = [
    `Units: unit_code (or code), unit_type, occupancy_status (Vacant, Occupied, or Notice Given); optional unit_label, block_name, floor_name, floor_level, bedrooms, kplc_meter_no. ${
      hasWaterUtility
        ? 'Required for this property: water_meter_no, water_current_reading.'
        : 'Do not include water meter or reading columns for this property.'
    }`,
    'Tenancies: unit_code, tenant_name, tenant_phone, tenant_email, tenancy_start_date; optional expected_move_out_date (only for Notice Given units), tenant_id_number, tenant_kra_pin, tenant_type, company_registration_number, contact_person, tenancy_end_date, tenancy_type, due_day, opening_arrears, arrears_as_of.',
  ];

  useEffect(() => {
    let active = true;
    void fetch('/api/property-officer/properties', { cache: 'no-store' }).then(async (response) => {
      const payload = await response.json() as PropertiesResponse;
      if (!response.ok) throw new Error(payload.detail || payload.error || 'Unable to load properties.');
      if (active) {
        const accessible = (payload.properties ?? []).filter((property) => branchIds.has(property.branch_id));
        setProperties(accessible);
        const requestedId = new URLSearchParams(window.location.search).get('property_id');
        setPropertyId(
          requestedId && accessible.some((property) => String(property.id) === requestedId)
            ? requestedId
            : '',
        );
      }
    }).catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : 'Unable to load properties.');
    }).finally(() => {
      if (active) setLoadingProperties(false);
    });
    return () => {
      active = false;
    };
  }, [branchIds]);

  async function submit(commit: boolean) {
    if (!propertyId || !files.length) {
      setError('Select a property and at least one CSV or XLSX file.');
      return;
    }
    setBusy(true);
    setError('');
    const formData = new FormData();
    formData.set('property_id', propertyId);
    formData.set('commit', String(commit));
    for (const file of files) formData.append('files', file);
    try {
      const response = await fetch('/api/property-officer/imports', {
        method: 'POST',
        body: formData,
      });
      const payload = await response.json() as ImportReport & { error?: string; detail?: string };
      if (!response.ok) {
        if (payload.errors) setReport(payload);
        else setError(payload.detail || payload.error || 'The import request failed.');
        return;
      }
      setReport(payload);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to process the import.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-6">
      <header>
        <h1 className="text-xl font-bold text-slate-950">Import legacy records</h1>
        <p className="mt-1 text-sm text-slate-600">Import unit inventory and active tenancies into one selected property, including opening arrears and water meter baselines when configured.</p>
      </header>

      <div className="rounded-2xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-950">
        <h2 className="font-bold">Safe import behavior</h2>
        <ul className="mt-2 list-inside list-disc space-y-1">
          <li>Preview first. No database changes are made during preview.</li>
          <li>The commit is atomic and is rejected if any row is invalid.</li>
          <li>Use only the Units and Tenancies sheets. Property details and unit-type definitions are selected from the saved property.</li>
          <li>Existing unit and tenancy records are reported and skipped, never overwritten.</li>
          <li>Opening arrears are recorded as a migration balance; historical payments are not imported. Payments made through the platform are recorded by its payment flow.</li>
          <li>Occupied imported tenants need an email address. A pending tenant portal account is provisioned and receives a unique activation link and code after a successful commit.</li>
          <li>Imported tenancies are active, including units marked Notice Given. Include their tenant on the Tenancies sheet; expected_move_out_date is optional and only applies to Notice Given units.</li>
          <li>Preview does not create accounts, challenges, or send messages.</li>
        </ul>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white p-5">
        <label className="block text-sm font-semibold text-slate-700">Property
          <select value={propertyId} onChange={(event) => { setPropertyId(event.target.value); setReport(null); }} className="mt-1 block w-full max-w-md rounded-lg border border-slate-300 px-3 py-2" disabled={loadingProperties}>
            <option value="">{loadingProperties ? 'Loading properties…' : 'Select property'}</option>
            {properties.map((property) => {
              const branchName = agency?.branches?.find((branch) => branch.id === property.branch_id)?.name;
              return <option key={property.id} value={property.id}>{property.name}{branchName ? ` · ${branchName}` : ''}</option>;
            })}
          </select>
        </label>
        {selectedProperty?.unit_types?.length ? (
          <p className="mt-2 max-w-2xl text-xs text-slate-500">
            Saved unit types: {selectedProperty.unit_types.map(
              (unitType) => `${unitType.name} (${unitType.unit_count} units, KES ${Number(unitType.monthly_rent).toLocaleString()}/month)`,
            ).join(', ')}. The Units sheet must use these exact type names.
          </p>
        ) : null}
        {propertyId && (
          <p className={`mt-3 max-w-2xl rounded-lg p-3 text-xs ${hasWaterUtility ? 'bg-sky-50 text-sky-900' : 'bg-slate-50 text-slate-600'}`}>
            {hasWaterUtility
              ? 'This property has a saved water utility. Every unit row must include water_meter_no and water_current_reading; the imported reading becomes the previous-reading baseline for the caretaker’s next reading. kplc_meter_no is optional.'
              : 'No water utility is configured for this property. Do not include water meter or water reading columns; kplc_meter_no remains optional.'}
          </p>
        )}
        {selectedProperty && (
          <p className="mt-3 max-w-2xl rounded-lg bg-violet-50 p-3 text-xs text-violet-950">
            {selectedProperty.default_handover_items?.length
              ? `Each newly imported tenant will have the property's default handover items recorded as issued: ${selectedProperty.default_handover_items.map((item) => `${item.name} (${item.quantity})`).join(', ')}.`
              : 'No default handover items are configured for this property, so newly imported tenants will not be assigned handover items automatically.'}
          </p>
        )}
        <label className="mt-4 block text-sm font-semibold text-slate-700">CSV files or one XLSX workbook
          <input
            type="file"
            accept=".csv,.xlsx"
            multiple
            onChange={(event) => {
              setFiles(Array.from(event.target.files ?? []));
              setReport(null);
            }}
            className="mt-1 block w-full rounded-lg border border-slate-300 p-2 text-sm"
          />
        </label>
        {files.length > 0 && <p className="mt-2 text-xs text-slate-500">{files.map((file) => file.name).join(', ')}</p>}
        <div className="mt-4 flex gap-3">
          <button type="button" disabled={busy || !propertyId || !files.length} onClick={() => void submit(false)} className="rounded-lg border border-emerald-800 px-4 py-2 text-sm font-bold text-emerald-900 disabled:opacity-50">{busy ? 'Processing…' : 'Preview import'}</button>
          <button type="button" disabled={busy || !report || report.mode !== 'preview' || report.errors.length > 0} onClick={() => void submit(true)} className="rounded-lg bg-emerald-800 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">Commit import</button>
        </div>
      </div>

      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}
      {report && (
        <section aria-label="Import preview" className="rounded-2xl border border-slate-200 bg-white p-5">
          <h2 className="font-bold text-slate-900">Import report</h2>
          <div className="mt-3 flex flex-wrap gap-3">
            {Object.entries(report.created).map(([sheet, count]) => <span key={sheet} className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-900">{sheet.replaceAll('_', ' ')}: {count} {report.mode === 'preview' ? 'ready' : 'created'}</span>)}
          </div>
          {report.errors.length > 0 && <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"><h3 className="font-bold">Rows requiring correction</h3><ul className="mt-2 list-inside list-disc">{report.errors.map((item, index) => <li key={`${item.sheet}-${item.row}-${index}`}>{item.sheet}, row {item.row}: {item.message}</li>)}</ul></div>}
          {report.skipped.length > 0 && <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"><h3 className="font-bold">Existing records skipped</h3><ul className="mt-2 list-inside list-disc">{report.skipped.map((item, index) => <li key={`${item.sheet}-${item.row}-${index}`}>{item.sheet}, row {item.row}: {item.reason}</li>)}</ul></div>}
          {report.activation && report.activation.prepared > 0 && (
            <div className={`mt-4 rounded-lg border p-4 text-sm ${report.activation.failed.length ? 'border-amber-200 bg-amber-50 text-amber-950' : 'border-emerald-200 bg-emerald-50 text-emerald-950'}`}>
              <h3 className="font-bold">Tenant account activation</h3>
              <p className="mt-1">
                {report.mode === 'preview'
                  ? `${report.activation.prepared} occupied tenant account${report.activation.prepared === 1 ? '' : 's'} will be provisioned and sent a welcome email and SMS after commit.`
                  : `${report.activation.sent} of ${report.activation.prepared} welcome email${report.activation.prepared === 1 ? '' : 's'} sent; ${report.activation.sms_queued ?? 0} SMS message${report.activation.sms_queued === 1 ? '' : 's'} queued.`}
              </p>
              {report.activation.failed.length > 0 && (
                <ul className="mt-2 list-inside list-disc">
                  {report.activation.failed.map((item, index) => <li key={`${item.unit_code}-${item.email}-${index}`}>{item.unit_code} ({item.email}): {item.message}</li>)}
                </ul>
              )}
            </div>
          )}
        </section>
      )}

      <details className="rounded-2xl border border-slate-200 bg-white p-5">
        <summary className="cursor-pointer font-bold text-slate-900">Workbook sheets and CSV filenames</summary>
        <p className="mt-2 text-sm text-slate-600">Use these two sheet names in an XLSX workbook. For CSV, use <code>units.csv</code> and <code>tenancies.csv</code>. Occupied and Notice Given units must have a matching unit_code row in Tenancies; Vacant units must not. expected_move_out_date is optional and is only accepted for a Notice Given unit. The unit_type value must match a type saved on the selected property.</p>
        <ul className="mt-3 space-y-2 text-xs text-slate-700">{templates.map((template) => <li key={template}>{template}</li>)}</ul>
        <p className="mt-3 text-xs text-slate-500">Dates use YYYY-MM-DD. Water readings are imported as meter baselines, so no reading period or previous-reading column is needed. Opening arrears require an arrears_as_of date. Historical payments and deposits are not imported.</p>
      </details>
    </section>
  );
}
