'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Camera, CheckCircle, RefreshCw, Send } from 'lucide-react';

type MeterUnit = {
  unit_id: number;
  unit_code: string;
  meter: string;
  previous_value: string | null;
  current_value: string | null;
  status: 'pending' | 'draft' | 'published';
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
  readings_open_date: string;
  readings_due: boolean;
  properties: WaterProperty[];
};

type ApiMessage = {
  detail?: string;
  message?: string;
  error?: string;
};

function isComplete(unit: MeterUnit) {
  return unit.status === 'published'
    || (
      unit.previous_value !== null
      && unit.current_value !== null
      && unit.photo_uploaded
    );
}

export default function WaterReadingsPage() {
  const [data, setData] = useState<WaterReadingsResponse | null>(null);
  const [selectedPropertyId, setSelectedPropertyId] = useState<number | null>(null);
  const [selectedUnitId, setSelectedUnitId] = useState<number | null>(null);
  const [previousValue, setPreviousValue] = useState('');
  const [currentValue, setCurrentValue] = useState('');
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState<{ success: boolean; message: string } | null>(null);

  const loadReadings = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/caretaker/water-readings', { cache: 'no-store' });
      const payload = (await response.json()) as WaterReadingsResponse & ApiMessage;
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
  const nextUnit = useMemo(() => {
    if (!property) return null;
    if (selectedUnitId !== null) {
      const selected = property.units.find(
        (unit) => unit.unit_id === selectedUnitId && unit.status !== 'published',
      );
      if (selected) return selected;
    }
    return property.units.find((unit) => !isComplete(unit)) ?? null;
  }, [property, selectedUnitId]);
  const completedCount = property?.units.filter(isComplete).length ?? 0;
  const allPublished = Boolean(
    property?.units.length && property.units.every((unit) => unit.status === 'published'),
  );
  const periodLabel = property
    ? new Date(`${property.period}T00:00:00`).toLocaleDateString('en-KE', {
        month: 'long',
        year: 'numeric',
      })
    : '';
  const readingsOpenDateLabel = data
    ? new Date(`${data.readings_open_date}T00:00:00`).toLocaleDateString('en-KE', {
        day: 'numeric',
        month: 'long',
      })
    : '';

  useEffect(() => {
    setSelectedUnitId(null);
    setPreviousValue('');
    setCurrentValue('');
    setPhotoFile(null);
  }, [selectedPropertyId]);

  useEffect(() => {
    setPreviousValue(nextUnit?.previous_value ?? '');
    setCurrentValue(nextUnit?.current_value ?? '');
    setPhotoFile(null);
  }, [nextUnit?.unit_id, nextUnit?.previous_value, nextUnit?.current_value]);

  const saveUnit = async () => {
    if (!property || !nextUnit || !data?.readings_due) return;
    const current = currentValue.trim();
    const previous = (nextUnit.previous_value ?? previousValue).trim();
    if (!current) {
      setNotice({ success: false, message: 'Enter the current meter reading first.' });
      return;
    }
    if (!previous) {
      setNotice({ success: false, message: 'Enter the previous reading to establish this meter baseline.' });
      return;
    }
    if (!photoFile) {
      setNotice({ success: false, message: 'Take a clear photo of this unit’s meter before continuing.' });
      return;
    }

    setSaving(true);
    setNotice(null);
    try {
      const draftResponse = await fetch('/api/caretaker/water-readings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          property_id: property.id,
          period: property.period,
          readings: [{
            unit_id: nextUnit.unit_id,
            previous_value: previous,
            current_value: current,
          }],
        }),
      });
      const draftPayload = (await draftResponse.json()) as ApiMessage;
      if (!draftResponse.ok) {
        throw new Error(draftPayload.detail || draftPayload.message || draftPayload.error || 'Unable to save this reading.');
      }

      const formData = new FormData();
      formData.set('property_id', String(property.id));
      formData.set('unit_id', String(nextUnit.unit_id));
      formData.set('period', property.period);
      formData.set('photo', photoFile);
      const photoResponse = await fetch('/api/caretaker/water-readings/photo', {
        method: 'POST',
        body: formData,
      });
      const photoPayload = (await photoResponse.json()) as ApiMessage;
      if (!photoResponse.ok) {
        throw new Error(
          photoPayload.detail
            || photoPayload.message
            || photoPayload.error
            || `Unable to save the meter photo for ${nextUnit.unit_code}.`,
        );
      }

      setNotice({
        success: true,
        message: `Reading and camera photo saved for ${nextUnit.unit_code}.`,
      });
      setSelectedUnitId(null);
      setPhotoFile(null);
      await loadReadings();
    } catch (caught) {
      setNotice({
        success: false,
        message: caught instanceof Error ? caught.message : 'Unable to save this reading and photo.',
      });
      await loadReadings();
    } finally {
      setSaving(false);
    }
  };

  const publish = async () => {
    if (!property || !property.can_publish || !data?.readings_due) return;
    const confirmation = window.confirm(
      `Publish all ${property.units.length} readings for ${property.name} (${periodLabel}) and start invoice drafting?`,
    );
    if (!confirmation) return;
    setPublishing(true);
    setNotice(null);
    try {
      const response = await fetch('/api/caretaker/water-readings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ property_id: property.id, period: property.period }),
      });
      const payload = (await response.json()) as ApiMessage;
      if (!response.ok) {
        throw new Error(payload.detail || payload.message || payload.error || 'Unable to publish readings.');
      }
      setNotice({
        success: true,
        message: payload.message || 'All readings are published. Invoice drafts are being generated.',
      });
      await loadReadings();
    } catch (caught) {
      setNotice({
        success: false,
        message: caught instanceof Error ? caught.message : 'Unable to publish readings.',
      });
      await loadReadings();
    } finally {
      setPublishing(false);
    }
  };

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-emerald-700">Monthly water metering</p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">
            Water Meter Readings
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Review previous meter readings anytime. Current-period entry unlocks on the scheduled reading date.
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
                {completedCount} of {property.units.length} units recorded
              </span>
            </div>
          </section>

          {!data?.readings_due && (
            <div className="rounded-2xl border border-sky-200 bg-sky-50 p-5 text-sm text-sky-900">
              <p className="font-semibold">Readings are visible, but entry is locked</p>
              <p className="mt-1">
                Previous readings are available below. Current-period fields unlock on {readingsOpenDateLabel}.
              </p>
            </div>
          )}

          {nextUnit ? (
            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700">
                    Unit {property.units.findIndex((unit) => unit.unit_id === nextUnit.unit_id) + 1} of {property.units.length}
                  </p>
                  <h2 className="mt-1 text-xl font-bold text-slate-900">{nextUnit.unit_code}</h2>
                  <p className="mt-1 text-sm text-slate-600">Meter: {nextUnit.meter}</p>
                </div>
                {nextUnit.status === 'draft' && (
                  <span className="rounded-full bg-blue-100 px-3 py-1 text-xs font-semibold text-blue-800">
                    Saved draft — photo or correction needed
                  </span>
                )}
              </div>

              <div className="mt-5 grid gap-4 sm:grid-cols-2">
                <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
                  Previous month’s reading (m³)
                  <input
                    type="number"
                    min="0"
                    step="0.0001"
                    value={nextUnit.previous_value ?? previousValue}
                    disabled={!data?.readings_due || nextUnit.previous_value !== null}
                    onChange={(event) => setPreviousValue(event.target.value)}
                    aria-label={`Previous meter reading for ${nextUnit.unit_code}`}
                    className="rounded-lg border border-slate-300 px-3 py-2 disabled:bg-slate-50"
                  />
                </label>
                <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
                  Current reading (m³)
                  <input
                    type="number"
                    min={nextUnit.previous_value ?? (previousValue || '0')}
                    step="0.0001"
                    value={currentValue}
                    disabled={!data?.readings_due}
                    onChange={(event) => setCurrentValue(event.target.value)}
                    aria-label={`Current meter reading for ${nextUnit.unit_code}`}
                    className="rounded-lg border border-slate-300 px-3 py-2"
                  />
                </label>
              </div>

              <label className="mt-5 flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed border-emerald-300 bg-emerald-50 p-6 text-center">
                <Camera size={24} className="text-emerald-700" />
                <span className="text-sm font-semibold text-slate-800">
                  {photoFile ? photoFile.name : 'Take a photo of this unit’s water meter'}
                </span>
                <span className="text-xs text-slate-600">
                  Use the device camera and make sure the meter number and reading are visible.
                </span>
                <input
                  key={nextUnit.unit_id}
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  capture="environment"
                  disabled={!data?.readings_due}
                  aria-label={`Take meter photo for ${nextUnit.unit_code}`}
                  onChange={(event) => {
                    const file = event.currentTarget.files?.[0] ?? null;
                    event.currentTarget.value = '';
                    setPhotoFile(file);
                  }}
                  className="sr-only"
                />
              </label>

              <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
                {property.units.some((unit) => unit.status === 'draft' && unit.unit_id !== nextUnit.unit_id) ? (
                  <label className="text-xs font-medium text-slate-600">
                    Review/edit saved unit
                    <select
                      value=""
                      disabled={!data?.readings_due}
                      onChange={(event) => {
                        const unitId = Number(event.target.value);
                        if (unitId) setSelectedUnitId(unitId);
                      }}
                      className="ml-2 rounded-lg border border-slate-300 bg-white px-2 py-2 text-slate-800"
                    >
                      <option value="">Choose unit</option>
                      {property.units
                        .filter((unit) => unit.status === 'draft')
                        .map((unit) => (
                          <option key={unit.unit_id} value={unit.unit_id}>{unit.unit_code}</option>
                        ))}
                    </select>
                  </label>
                ) : <span />}
                <button
                  type="button"
                  onClick={() => void saveUnit()}
                  disabled={saving || !data?.readings_due}
                  className="inline-flex items-center gap-2 rounded-xl bg-emerald-700 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
                >
                  <Camera size={16} />
                  {saving ? 'Saving reading and photo…' : 'Save unit and continue'}
                </button>
              </div>
            </section>
          ) : (
            <section className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5">
              <div className="flex items-start gap-3">
                <CheckCircle size={20} className="mt-0.5 text-emerald-700" />
                <div>
                  <h2 className="font-bold text-emerald-900">All unit readings are ready</h2>
                  <p className="mt-1 text-sm text-emerald-800">
                    {!data?.readings_due
                      ? `${completedCount} of ${property.units.length} readings are already saved. Entry unlocks on ${readingsOpenDateLabel}.`
                      : allPublished
                      ? `All ${property.units.length} readings for ${periodLabel} are already published.`
                      : `${completedCount} of ${property.units.length} units have readings and meter photos for ${periodLabel}. Review any saved drafts below, then publish to start invoice drafting.`}
                  </p>
                </div>
              </div>
            </section>
          )}

          <section className="rounded-2xl border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-bold text-slate-800">Property reading progress</h2>
            <ul className="mt-3 divide-y divide-slate-100">
              {property.units.map((unit) => (
                <li key={unit.unit_id} className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm">
                  <span className="font-semibold text-slate-800">{unit.unit_code}</span>
                  <span className="flex flex-col text-slate-600">
                    <span>Previous month: {unit.previous_value ?? 'Not recorded'} m³</span>
                    <span>
                      {unit.status === 'published'
                        ? `Published · ${unit.current_value} m³`
                        : unit.current_value !== null
                          ? `Draft · ${unit.current_value} m³ · ${unit.photo_uploaded ? 'photo saved' : 'photo needed'}`
                          : 'Current reading needed'}
                    </span>
                  </span>
                  {unit.status === 'draft' && (
                    <button
                      type="button"
                      onClick={() => setSelectedUnitId(unit.unit_id)}
                      disabled={!data?.readings_due}
                      className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 disabled:opacity-50"
                    >
                      Edit
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </section>

          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => void publish()}
              disabled={publishing || !property.can_publish || !data?.readings_due}
              className="inline-flex items-center gap-2 rounded-xl bg-emerald-700 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
            >
              <Send size={16} />
              {publishing
                ? 'Publishing…'
                : allPublished
                  ? 'Readings already published'
                  : `Confirm and publish ${property.units.length} readings`}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
