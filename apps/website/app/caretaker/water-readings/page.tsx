'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Camera, CheckCircle, RefreshCw, Save, Send } from 'lucide-react';

type MeterUnit = {
  unit_id: number;
  unit_code: string;
  meter: string;
  previous_value: string | null;
  current_value: string | null;
  status: 'pending' | 'draft' | 'published';
  baseline_value: string | null;
  photo_uploaded: boolean;
};

type WaterProperty = {
  id: number;
  name: string;
  period: string;
  units: MeterUnit[];
  can_publish: boolean;
  pending_count: number;
};

type WaterReadingsResponse = {
  period: string;
  readings_due: boolean;
  properties: WaterProperty[];
};

type ReadingFilter = 'pending' | 'completed';

export default function WaterReadingsPage() {
  const [data, setData] = useState<WaterReadingsResponse | null>(null);
  const [selectedPropertyId, setSelectedPropertyId] = useState<number | null>(null);
  const [readingFilter, setReadingFilter] = useState<ReadingFilter>('pending');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [photoFiles, setPhotoFiles] = useState<Record<number, File>>({});
  const [error, setError] = useState('');
  const [notice, setNotice] = useState<{ success: boolean; message: string } | null>(null);

  const loadReadings = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/caretaker/water-readings', { cache: 'no-store' });
      const payload = (await response.json()) as WaterReadingsResponse & {
        detail?: string;
        message?: string;
        error?: string;
      };
      if (!response.ok) {
        throw new Error(payload.detail || payload.message || payload.error || 'Unable to load water readings.');
      }
      setData(payload);
      setSelectedPropertyId((current) =>
        payload.properties.some((property) => property.id === current)
          ? current
          : payload.properties[0]?.id ?? null,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load water readings.');
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadReadings();
  }, [loadReadings]);

  const property = useMemo(
    () => data?.properties.find((item) => item.id === selectedPropertyId) ?? null,
    [data, selectedPropertyId],
  );
  const periodLabel = property
    ? new Date(`${property.period}T00:00:00`).toLocaleDateString('en-KE', {
        month: 'long',
        year: 'numeric',
      })
    : '';
  const visibleUnits = property?.units.filter((unit) =>
      readingFilter === 'pending' ? unit.status !== 'published' : unit.status === 'published',
  ) ?? [];
  const completedCount = property?.units.filter((unit) => unit.status === 'published').length ?? 0;

  const updateUnit = (unitId: number, field: 'previous_value' | 'current_value', value: string) => {
    setData((current) => {
      if (!current) return current;
      return {
        ...current,
        properties: current.properties.map((item) => {
          if (item.id !== selectedPropertyId) return item;
          const units = item.units.map((unit) =>
            unit.unit_id === unitId
              ? {
                  ...unit,
                  [field]: value === '' ? null : value,
                  status: 'draft' as const,
                  photo_uploaded: false,
                }
              : unit,
          );
          return {
            ...item,
            units,
            can_publish: units.length > 0 && units.every(
              (unit) =>
                unit.status === 'published' ||
                (
                  unit.previous_value !== null
                  && unit.current_value !== null
                  && unit.photo_uploaded
                ),
            ),
          };
        }),
      };
    });
  };

  const submit = async (method: 'POST' | 'PATCH') => {
    if (!property) return;
    const isPublish = method === 'PATCH';
    const readings = property.units
      .filter((unit) => unit.status !== 'published' && unit.current_value !== null)
      .map((unit) => ({
        unit_id: unit.unit_id,
        previous_value: unit.previous_value,
        current_value: unit.current_value,
      }));
    if (!isPublish && readings.length === 0) {
      setNotice({ success: false, message: 'Enter at least one current reading before saving.' });
      return;
    }
    if (isPublish && !property.can_publish) {
      setNotice({ success: false, message: 'Every occupied unit needs a valid reading before publishing.' });
      return;
    }
    isPublish ? setPublishing(true) : setSaving(true);
    setNotice(null);
    try {
      const response = await fetch('/api/caretaker/water-readings', {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          property_id: property.id,
          period: property.period,
          ...(isPublish ? {} : { readings }),
        }),
      });
      const payload = (await response.json()) as { detail?: string; message?: string; error?: string };
      if (!response.ok) {
        throw new Error(payload.detail || payload.message || payload.error || 'The water reading request failed.');
      }
      if (!isPublish) {
        const uploadedUnitIds: number[] = [];
        for (const [unitId, file] of Object.entries(photoFiles)) {
          const unit = property.units.find((item) => item.unit_id === Number(unitId));
          if (!unit || unit.status === 'published' || unit.current_value === null) continue;
          const formData = new FormData();
          formData.set('property_id', String(property.id));
          formData.set('unit_id', String(unit.unit_id));
          formData.set('period', property.period);
          formData.set('photo', file);
          const photoResponse = await fetch('/api/caretaker/water-readings/photo', {
            method: 'POST',
            body: formData,
          });
          const photoPayload = (await photoResponse.json()) as {
            detail?: string;
            message?: string;
            error?: string;
          };
          if (!photoResponse.ok) {
            throw new Error(
              photoPayload.detail
                || photoPayload.message
                || photoPayload.error
                || `Unable to upload the photo for ${unit.unit_code}.`,
            );
          }
          uploadedUnitIds.push(unit.unit_id);
        }
        setPhotoFiles((current) =>
          Object.fromEntries(
            Object.entries(current).filter(([unitId]) => !uploadedUnitIds.includes(Number(unitId))),
          ),
        );
      }
      setNotice({
        success: true,
        message: isPublish
          ? 'All readings are published. Invoice drafts are being generated.'
          : 'Reading drafts and selected meter photos are saved. Publish is enabled once every occupied unit has a reading and photo.',
      });
      await loadReadings();
    } catch (caught) {
      setNotice({
        success: false,
        message: caught instanceof Error ? caught.message : 'The water reading request failed.',
      });
    } finally {
      setSaving(false);
      setPublishing(false);
    }
  };

  const missingPrevious = property?.units.filter(
    (unit) => unit.status !== 'published' && unit.previous_value === null,
  ) ?? [];

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-emerald-700">Monthly water metering</p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">
            Water Meter Readings
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Record each occupied unit and attach a meter photo before publishing readings for invoice drafting.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void loadReadings()}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </header>

      {notice && (
        <div
          role="status"
          className={`flex items-center gap-2 rounded-xl border p-3 text-sm ${
            notice.success
              ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
              : 'border-red-200 bg-red-50 text-red-800'
          }`}
        >
          {notice.success ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}
          {notice.message}
        </div>
      )}
      {error && (
        <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          {error}
        </div>
      )}
      {loading && <p className="text-sm text-slate-500">Loading assigned property readings…</p>}
      {!loading && !error && data?.properties.length === 0 && (
        <div className="rounded-2xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
          No assigned property currently requires meter readings. Fixed-charge water utilities do not need meter readings.
        </div>
      )}

      {!loading && property && (
        <>
          <section className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white p-4">
            <label className="flex flex-col gap-1 text-xs font-semibold text-slate-600">
              Property
              <select
                value={property.id}
                onChange={(event) => setSelectedPropertyId(Number(event.target.value))}
                className="min-w-60 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
              >
                {data?.properties.map((item) => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
              </select>
            </label>
            <div className="text-sm text-slate-600">
              Billing period: <span className="font-bold text-slate-900">{periodLabel}</span>
              <span className="ml-3 text-xs">
                {data?.readings_due
                  ? `${property.pending_count} reading${property.pending_count === 1 ? '' : 's'} not yet published`
                  : 'Readings open on the 30th'}
              </span>
            </div>
            {data?.readings_due && (
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => void submit('POST')}
                  disabled={saving || property.units.every((unit) => unit.status === 'published' || unit.current_value === null)}
                  className="inline-flex items-center gap-2 rounded-xl border border-emerald-700 px-4 py-2 text-xs font-semibold text-emerald-800 disabled:opacity-50"
                >
                  <Save size={14} /> {saving ? 'Saving…' : 'Save drafts'}
                </button>
                <button
                  type="button"
                  onClick={() => void submit('PATCH')}
                  disabled={publishing || !property.can_publish}
                  className="inline-flex items-center gap-2 rounded-xl bg-emerald-700 px-4 py-2 text-xs font-semibold text-white disabled:opacity-50"
                >
                  <Send size={14} /> {publishing ? 'Publishing…' : 'Publish readings'}
                </button>
              </div>
            )}
          </section>

          {!data?.readings_due ? (
            <div className="rounded-2xl border border-sky-200 bg-sky-50 p-5 text-sm text-sky-900">
              <p className="font-semibold">Meter readings are not due yet</p>
              <p className="mt-1">You can review the active readings on the unit roster. Reading entry opens on the 30th (or the final day of shorter months).</p>
            </div>
          ) : (
          <>
          <section
            className="flex flex-wrap items-center justify-between gap-3"
            aria-label="Meter reading progress filter"
          >
            <p className="text-sm font-semibold text-slate-700">
              {property.units.length - completedCount} pending · {completedCount} completed for this billing period
            </p>
            <div className="flex rounded-xl border border-slate-200 bg-white p-1">
              {([
                ['pending', 'Pending Reading', property.pending_count],
                ['completed', 'Completed This Month', completedCount],
              ] as const).map(([filter, label, count]) => (
                <button
                  key={filter}
                  type="button"
                  aria-pressed={readingFilter === filter}
                  onClick={() => setReadingFilter(filter)}
                  className={`rounded-lg px-3 py-2 text-xs font-semibold transition-colors ${
                    readingFilter === filter
                      ? 'bg-emerald-700 text-white'
                      : 'text-slate-600 hover:bg-slate-100'
                  }`}
                >
                  {label} <span className="ml-1 opacity-80">({count})</span>
                </button>
              ))}
            </div>
          </section>

          {missingPrevious.length > 0 && (
            <div className="flex gap-2 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
              <AlertTriangle size={17} className="mt-0.5 shrink-0" />
              <p>
                {missingPrevious.length} unit(s) have no prior meter value. Enter the previous value as well as
                the current reading to establish their baseline.
              </p>
            </div>
          )}

          <section className="overflow-x-auto rounded-2xl border border-slate-200 bg-white">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-600">
                <tr>
                  <th className="px-4 py-3 text-left">Unit</th>
                  <th className="px-4 py-3 text-right">Previous reading (m³)</th>
                  <th className="px-4 py-3 text-right">Current reading (m³)</th>
                  <th className="px-4 py-3 text-right">Usage (m³)</th>
                  <th className="px-4 py-3 text-center">Meter photo</th>
                  <th className="px-4 py-3 text-center">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {visibleUnits.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-sm text-slate-500">
                      {readingFilter === 'pending'
                        ? 'All required readings are published for this billing period.'
                        : 'No readings have been published for this billing period yet.'}
                    </td>
                  </tr>
                ) : visibleUnits.map((unit) => {
                  const usage =
                    unit.current_value !== null && unit.previous_value !== null
                      ? Number(unit.current_value) - Number(unit.previous_value)
                      : null;
                  return (
                    <tr key={unit.unit_id}>
                      <td className="px-4 py-3 font-semibold text-slate-900">{unit.unit_code}</td>
                      <td className="px-4 py-3 text-right">
                        {unit.status === 'published' || unit.previous_value !== null ? (
                          <span className="text-slate-700">{unit.previous_value ?? '—'}</span>
                        ) : (
                          <input
                            type="number"
                            min="0"
                            step="0.0001"
                            aria-label={`Previous meter reading for ${unit.unit_code}`}
                            value={unit.previous_value ?? ''}
                            onChange={(event) => updateUnit(unit.unit_id, 'previous_value', event.target.value)}
                            className="w-28 rounded-lg border border-slate-300 px-2 py-1 text-right"
                          />
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {unit.status === 'published' ? (
                          <span className="text-slate-700">{unit.current_value}</span>
                        ) : (
                          <input
                            type="number"
                            min={unit.previous_value ?? 0}
                            step="0.0001"
                            aria-label={`Current meter reading for ${unit.unit_code}`}
                            value={unit.current_value ?? ''}
                            onChange={(event) => updateUnit(unit.unit_id, 'current_value', event.target.value)}
                            className="w-28 rounded-lg border border-slate-300 px-2 py-1 text-right"
                          />
                        )}
                      </td>
                      <td className="px-4 py-3 text-right text-slate-700">
                        {usage !== null && usage >= 0 ? usage.toFixed(2) : '—'}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {unit.status === 'published' ? (
                          <span className={`text-xs ${unit.photo_uploaded ? 'text-emerald-700' : 'text-amber-700'}`}>
                            {unit.photo_uploaded ? 'Evidence saved' : 'No photo on record'}
                          </span>
                        ) : (
                          <label className="inline-flex cursor-pointer flex-col items-center gap-1 text-xs font-medium text-slate-700">
                            <span className="inline-flex items-center gap-1">
                              <Camera size={14} />
                              {photoFiles[unit.unit_id]?.name ?? (unit.photo_uploaded ? 'Photo saved' : 'Add photo')}
                            </span>
                            <input
                              type="file"
                              accept="image/jpeg,image/png,image/webp"
                              capture="environment"
                              aria-label={`Meter photo for ${unit.unit_code}`}
                              onChange={(event) => {
                                const file = event.target.files?.[0];
                                if (file) {
                                  setPhotoFiles((current) => ({ ...current, [unit.unit_id]: file }));
                                }
                              }}
                              className="sr-only"
                            />
                          </label>
                        )}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                          unit.status === 'published'
                            ? 'bg-emerald-100 text-emerald-800'
                            : unit.status === 'draft'
                              ? 'bg-blue-100 text-blue-800'
                              : 'bg-amber-100 text-amber-800'
                        }`}>
                          {unit.status === 'published' ? 'Published' : unit.status === 'draft' ? 'Draft' : 'Pending'}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
          </>
          )}
        </>
      )}
    </div>
  );
}
