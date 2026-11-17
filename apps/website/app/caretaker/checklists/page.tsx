'use client';

import Link from 'next/link';
import Image from 'next/image';
import { useCallback, useEffect, useState } from 'react';
import { ArrowUpRight, CheckCircle, ClipboardCheck, RefreshCw } from 'lucide-react';

type MeterRequirement = {
  utility_type: string;
  name: string;
  meter: string | null;
  configured: boolean;
};

type HandoverCondition = {
  room: string;
  area: string;
  status: '' | 'good' | 'needs_attention' | 'not_applicable';
  notes: string;
};

type HandoverPhoto = {
  name: string;
  content_type: string | null;
  size_bytes: number | null;
  url: string;
};

type MoveInHandover = {
  id: number;
  tenancy_id: number;
  unit_id: number;
  unit_code: string;
  property_name: string;
  tenant_name: string;
  status: string;
  keys_issued: number | null;
  condition_checklist: HandoverCondition[];
  meter_requirements: MeterRequirement[];
  initial_meter_readings: {
    utility_type: string;
    current_value: string;
    name?: string;
    meter?: string | null;
  }[];
  photos: HandoverPhoto[];
};

type HandoverForm = {
  keysIssued: string;
  conditions: HandoverCondition[];
  readings: Record<string, string>;
};

type HandoverPayload = {
  handovers?: MoveInHandover[];
};

type RoutineChecklistEntry = {
  title: string;
  status?: string;
  notes?: string;
};

type AssignedInspection = {
  id: number;
  title: string;
  inspection_type: string;
  property_name: string;
  unit_code: string | null;
  scheduled_for: string;
  status: string;
  checklist: RoutineChecklistEntry[];
  findings: string;
  recommendations: string;
};

type RoutineForm = {
  checklist: RoutineChecklistEntry[];
  findings: string;
  recommendations: string;
};

export default function CaretakerChecklistsPage() {
  const [handovers, setHandovers] = useState<MoveInHandover[]>([]);
  const [handoverForms, setHandoverForms] = useState<Record<number, HandoverForm>>({});
  const [handoverPhotos, setHandoverPhotos] = useState<Record<number, File[]>>({});
  const [inspections, setInspections] = useState<AssignedInspection[]>([]);
  const [routineForms, setRoutineForms] = useState<Record<number, RoutineForm>>({});
  const [loading, setLoading] = useState(true);
  const [savingHandoverId, setSavingHandoverId] = useState<number | null>(null);
  const [savingInspectionId, setSavingInspectionId] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const loadItems = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [handoversResponse, inspectionsResponse] = await Promise.all([
        fetch('/api/caretaker/checklists', { cache: 'no-store' }),
        fetch('/api/caretaker/inspections', { cache: 'no-store' }),
      ]);
      const [handoverPayload, inspectionPayload] = await Promise.all([
        handoversResponse.json() as Promise<HandoverPayload & { error?: string; detail?: string }>,
        inspectionsResponse.json() as Promise<{
          inspections?: AssignedInspection[];
          error?: string;
          detail?: string;
        }>,
      ]);
      if (!handoversResponse.ok) {
        throw new Error(handoverPayload.detail || handoverPayload.error || 'Unable to load assigned move-in handovers.');
      }
      if (!inspectionsResponse.ok) {
        throw new Error(inspectionPayload.detail || inspectionPayload.error || 'Unable to load assigned inspections.');
      }
      const assignedHandovers = Array.isArray(handoverPayload.handovers) ? handoverPayload.handovers : [];
      setHandovers(assignedHandovers);
      setHandoverForms((current) => Object.fromEntries(
        assignedHandovers.map((handover) => [
          handover.id,
          current[handover.id] ?? {
            keysIssued: handover.keys_issued === null ? '' : String(handover.keys_issued),
            conditions: handover.condition_checklist.map((condition) => ({ ...condition })),
            readings: Object.fromEntries(
              handover.initial_meter_readings.map((reading) => [
                reading.utility_type,
                reading.current_value,
              ]),
            ),
          },
        ]),
      ));
      const assignedInspections = (inspectionPayload.inspections ?? []).filter(
        (inspection) =>
          !['move_out', 'exit'].includes(inspection.inspection_type.toLowerCase())
          && ['scheduled', 'in_progress'].includes(inspection.status),
      );
      setInspections(assignedInspections);
      setRoutineForms((current) => Object.fromEntries(
        assignedInspections.map((inspection) => [
          inspection.id,
          current[inspection.id] ?? {
            checklist: inspection.checklist.map((entry) => ({ ...entry })),
            findings: inspection.findings,
            recommendations: inspection.recommendations,
          },
        ]),
      ));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load assigned move-in handovers.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadItems();
  }, [loadItems]);

  const submitHandover = async (handover: MoveInHandover) => {
    const form = handoverForms[handover.id];
    const selectedPhotos = handoverPhotos[handover.id] ?? [];
    if (!form || !/^\d+$/.test(form.keysIssued) || Number(form.keysIssued) > 32767) {
      setError('Enter the whole number of keys and access devices issued (0–32,767).');
      return;
    }
    if (handover.meter_requirements.some((requirement) => !requirement.configured)) {
      setError('A Property Officer must configure the missing meter identifier before this handover can be submitted.');
      return;
    }
    if (handover.meter_requirements.some((requirement) => !form.readings[requirement.utility_type]?.trim())) {
      setError('Enter an opening reading for every metered utility shown.');
      return;
    }
    if (form.conditions.some((condition) => !condition.status)) {
      setError('Record a condition for every listed room and fixture.');
      return;
    }
    if (form.conditions.some(
      (condition) => condition.status === 'needs_attention' && !condition.notes.trim(),
    )) {
      setError('Add a note for each condition marked as needing attention.');
      return;
    }
    if (handover.photos.length === 0 && (handoverPhotos[handover.id] ?? []).length === 0) {
      setError('Upload at least one move-in condition or meter photo.');
      return;
    }
    if (handover.photos.length + selectedPhotos.length > 10) {
      setError('A move-in handover can include no more than 10 photos.');
      return;
    }
    if (selectedPhotos.some((photo) => photo.size > 5 * 1024 * 1024)
      || selectedPhotos.reduce((total, photo) => total + photo.size, 0) > 20 * 1024 * 1024) {
      setError('Each photo must be no larger than 5 MB and selected photos must total no more than 20 MB.');
      return;
    }

    setSavingHandoverId(handover.id);
    setError('');
    setNotice('');
    try {
      let submissionHandover = handover;
      if (selectedPhotos.length > 0) {
        const formData = new FormData();
        selectedPhotos.forEach((file) => formData.append('photos', file));
        const photoResponse = await fetch(`/api/caretaker/checklists/${handover.id}/photos`, {
          method: 'POST',
          body: formData,
        });
        const photoPayload = (await photoResponse.json()) as {
          handover?: MoveInHandover;
          error?: string;
          detail?: string;
        };
        if (!photoResponse.ok || !photoPayload.handover) {
          throw new Error(photoPayload.detail || photoPayload.error || 'Unable to upload handover photos.');
        }
        const updatedHandover = photoPayload.handover;
        setHandovers((current) => current.map((item) => (
          item.id === handover.id ? updatedHandover : item
        )));
        setHandoverPhotos((current) => ({ ...current, [handover.id]: [] }));
        submissionHandover = updatedHandover;
      }

      const response = await fetch(`/api/caretaker/checklists/${submissionHandover.id}/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          keys_issued: Number(form.keysIssued),
          condition_checklist: form.conditions,
          initial_meter_readings: submissionHandover.meter_requirements.map((requirement) => ({
            utility_type: requirement.utility_type,
            current_value: form.readings[requirement.utility_type],
          })),
        }),
      });
      const payload = (await response.json()) as { error?: string; detail?: string };
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || 'Unable to submit this move-in handover.');
      }
      setHandovers((current) => current.filter((item) => item.id !== submissionHandover.id));
      setNotice(`Move-in handover submitted for ${submissionHandover.property_name}, unit ${submissionHandover.unit_code}.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to submit this move-in handover.');
    } finally {
      setSavingHandoverId(null);
    }
  };

  const updateHandoverForm = (handoverId: number, update: Partial<HandoverForm>) => {
    setHandoverForms((current) => ({
      ...current,
      [handoverId]: { ...current[handoverId], ...update },
    }));
  };

  const updateRoutineForm = (inspection: AssignedInspection, update: Partial<RoutineForm>) => {
    setRoutineForms((current) => ({
      ...current,
      [inspection.id]: {
        ...current[inspection.id],
        checklist: current[inspection.id]?.checklist ?? inspection.checklist,
        findings: current[inspection.id]?.findings ?? inspection.findings,
        recommendations: current[inspection.id]?.recommendations ?? inspection.recommendations,
        ...update,
      },
    }));
  };

  const completeRoutineInspection = async (inspection: AssignedInspection) => {
    const form = routineForms[inspection.id] ?? {
      checklist: inspection.checklist,
      findings: inspection.findings,
      recommendations: inspection.recommendations,
    };
    if (form.checklist.some((item) => !['complete', 'completed'].includes(item.status ?? ''))) {
      setError('Complete every routine inspection checklist item before submitting.');
      return;
    }
    if (form.checklist.length === 0 && !form.findings.trim()) {
      setError('Add findings before submitting an inspection that has no checklist items.');
      return;
    }
    setSavingInspectionId(inspection.id);
    setError('');
    setNotice('');
    try {
      const response = await fetch(`/api/caretaker/inspections/${inspection.id}/complete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          checklist: form.checklist,
          findings: form.findings,
          recommendations: form.recommendations,
        }),
      });
      const payload = (await response.json()) as { error?: string; detail?: string };
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || 'Unable to submit this inspection.');
      }
      setInspections((current) => current.filter((entry) => entry.id !== inspection.id));
      setNotice(`Completed “${inspection.title}” at ${inspection.property_name}.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to submit this inspection.');
    } finally {
      setSavingInspectionId(null);
    }
  };

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-emerald-700">Field operations</p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">
            Checklists & Inspections
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Complete move-in handover tasks and assigned routine inspections for your properties.
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            href="/caretaker/dashboard#move-out-inspections"
            className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700"
          >
            Move-out inspections <ArrowUpRight size={14} />
          </Link>
          <button
            type="button"
            onClick={() => void loadItems()}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </header>

      {error && (
        <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
          {notice}
        </p>
      )}
      {loading && <p className="text-sm text-slate-500">Loading assigned checklists…</p>}
      {!loading && !error && handovers.length === 0 && inspections.length === 0 && (
        <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center">
          <CheckCircle size={28} className="mx-auto text-emerald-700" />
          <h2 className="mt-3 font-semibold text-slate-900">No checklists pending</h2>
          <p className="mt-1 text-sm text-slate-600">New handover tasks and routine inspections will appear here.</p>
        </div>
      )}
      {!loading && handovers.length > 0 && (
        <section className="space-y-3" aria-label="Pending move-in handovers">
          <div>
            <h2 className="text-lg font-bold text-slate-900">Move-in handover</h2>
            <p className="text-sm text-slate-600">
              Record each unit’s condition, keys, opening meter readings, and photo evidence for Property Officer review.
            </p>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            {handovers.map((handover) => {
              const form = handoverForms[handover.id] ?? {
                keysIssued: '',
                conditions: handover.condition_checklist,
                readings: {},
              };
              const selectedPhotos = handoverPhotos[handover.id] ?? [];
              return (
                <article key={handover.id} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                  <div className="flex items-start gap-3">
                    <span className="rounded-xl bg-emerald-50 p-2 text-emerald-800">
                      <ClipboardCheck size={18} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <h2 className="font-semibold text-slate-900">{handover.property_name} · Unit {handover.unit_code}</h2>
                      <p className="mt-1 text-sm text-slate-600">Tenant: {handover.tenant_name}</p>
                    </div>
                  </div>

                  <label className="mt-5 block text-sm font-medium text-slate-700">
                    Keys and access devices issued
                    <input
                      type="number"
                      min="0"
                      max="32767"
                      step="1"
                      value={form.keysIssued}
                      onChange={(event) => updateHandoverForm(handover.id, { keysIssued: event.target.value })}
                      className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                    />
                  </label>

                  <fieldset className="mt-5 space-y-3">
                    <legend className="text-sm font-semibold text-slate-800">Room and fixture condition</legend>
                    {form.conditions.map((condition, index) => (
                      <div key={condition.area} className="rounded-xl border border-slate-200 p-3">
                        <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_180px] sm:items-center">
                          <span className="text-sm font-medium text-slate-700">{condition.room} · {condition.area}</span>
                          <select
                            aria-label={`${condition.room} ${condition.area} condition`}
                            value={condition.status}
                            onChange={(event) => {
                              const status = (
                                ['', 'good', 'needs_attention', 'not_applicable'] as const
                              ).find((value) => value === event.target.value) ?? '';
                              const conditions = form.conditions.map((entry, entryIndex) => (
                                entryIndex === index
                                  ? { ...entry, status }
                                  : entry
                              ));
                              updateHandoverForm(handover.id, { conditions });
                            }}
                            className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
                          >
                            <option value="">Select condition</option>
                            <option value="good">Good condition</option>
                            <option value="needs_attention">Needs attention</option>
                            <option value="not_applicable">Not applicable</option>
                          </select>
                        </div>
                        <textarea
                          aria-label={`${condition.room} ${condition.area} notes`}
                          maxLength={2000}
                          rows={2}
                          placeholder={condition.status === 'needs_attention' ? 'Describe the issue' : 'Optional notes'}
                          value={condition.notes}
                          onChange={(event) => {
                            const conditions = form.conditions.map((entry, entryIndex) => (
                              entryIndex === index ? { ...entry, notes: event.target.value } : entry
                            ));
                            updateHandoverForm(handover.id, { conditions });
                          }}
                          className="mt-2 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        />
                      </div>
                    ))}
                  </fieldset>

                  <fieldset className="mt-5 space-y-3">
                    <legend className="text-sm font-semibold text-slate-800">Opening meter readings</legend>
                    {handover.meter_requirements.length === 0 && (
                      <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
                        No metered water or electricity utility is configured for this tenancy.
                      </p>
                    )}
                    {handover.meter_requirements.map((requirement) => (
                      <label key={requirement.utility_type} className="block text-sm font-medium text-slate-700">
                        {requirement.name}{requirement.meter ? ` · Meter ${requirement.meter}` : ''}
                        {!requirement.configured && (
                          <span className="mt-1 block text-xs font-normal text-amber-800">
                            Meter identifier is missing. Ask a Property Officer to configure it before submission.
                          </span>
                        )}
                        <input
                          type="number"
                          min="0"
                          step="0.0001"
                          value={form.readings[requirement.utility_type] ?? ''}
                          onChange={(event) => updateHandoverForm(handover.id, {
                            readings: { ...form.readings, [requirement.utility_type]: event.target.value },
                          })}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                        />
                      </label>
                    ))}
                  </fieldset>

                  <div className="mt-5">
                    <label className="block text-sm font-semibold text-slate-800">
                      Handover photos
                      <input
                        type="file"
                        accept="image/jpeg,image/png,image/webp"
                        multiple
                        onChange={(event) => setHandoverPhotos((current) => ({
                          ...current,
                          [handover.id]: Array.from(event.target.files ?? []),
                        }))}
                        className="mt-2 block w-full text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-emerald-50 file:px-3 file:py-2 file:font-semibold file:text-emerald-800"
                      />
                    </label>
                    <p className="mt-1 text-xs text-slate-500">JPEG, PNG, or WebP; up to 5 MB per photo and 10 photos total.</p>
                    {(handover.photos.length > 0 || selectedPhotos.length > 0) && (
                      <div className="mt-3 flex flex-wrap gap-3">
                        {handover.photos.map((photo) => (
                          <a
                            key={photo.url}
                            href={photo.url}
                            target="_blank"
                            rel="noreferrer"
                            className="overflow-hidden rounded-lg border border-slate-200"
                          >
                            <Image
                              src={photo.url}
                              alt={photo.name}
                              width={112}
                              height={80}
                              unoptimized
                              className="h-20 w-28 object-cover"
                            />
                          </a>
                        ))}
                        {selectedPhotos.map((photo) => (
                          <span key={`${photo.name}-${photo.size}`} className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
                            {photo.name}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  <button
                    type="button"
                    onClick={() => void submitHandover(handover)}
                    disabled={savingHandoverId !== null}
                    className="mt-5 inline-flex items-center gap-2 rounded-lg bg-emerald-800 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                  >
                    {savingHandoverId === handover.id ? 'Submitting…' : 'Submit handover for review'}
                  </button>
                </article>
              );
            })}
          </div>
        </section>
      )}
      {!loading && inspections.length > 0 && (
        <section className="space-y-3" aria-label="Assigned routine inspections">
          <div>
            <h2 className="text-lg font-bold text-slate-900">Routine inspections</h2>
            <p className="text-sm text-slate-600">Review each checklist and add field notes before submitting.</p>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            {inspections.map((inspection) => {
              const form = routineForms[inspection.id] ?? {
                checklist: inspection.checklist,
                findings: inspection.findings,
                recommendations: inspection.recommendations,
              };
              return (
                <article key={inspection.id} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                  <h3 className="font-semibold text-slate-900">{inspection.title}</h3>
                  <p className="mt-1 text-sm text-slate-600">
                    {inspection.property_name}{inspection.unit_code ? ` · Unit ${inspection.unit_code}` : ''}
                  </p>
                  <p className="text-xs text-slate-500">
                    Scheduled {new Date(inspection.scheduled_for).toLocaleString('en-KE', {
                      dateStyle: 'medium',
                      timeStyle: 'short',
                    })}
                  </p>
                  {form.checklist.length > 0 ? (
                    <fieldset className="mt-4 space-y-2">
                      <legend className="text-sm font-semibold text-slate-700">Inspection checklist</legend>
                      {form.checklist.map((entry, index) => {
                        const complete = ['complete', 'completed'].includes(entry.status ?? '');
                        return (
                          <label key={`${entry.title}-${index}`} className="flex items-start gap-2 rounded-lg bg-slate-50 p-3 text-sm text-slate-700">
                            <input
                              type="checkbox"
                              checked={complete}
                              onChange={(event) => {
                                const checklist = form.checklist.map((row, rowIndex) =>
                                  rowIndex === index
                                    ? { ...row, status: event.target.checked ? 'complete' : 'pending' }
                                    : row,
                                );
                                updateRoutineForm(inspection, { checklist });
                              }}
                              className="mt-0.5"
                            />
                            {entry.title}
                          </label>
                        );
                      })}
                    </fieldset>
                  ) : (
                    <p className="mt-4 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
                      This inspection has no checklist items. Add your findings before submitting.
                    </p>
                  )}
                  <label className="mt-4 block text-sm font-medium text-slate-700">
                    Findings
                    <textarea
                      maxLength={10000}
                      rows={3}
                      value={form.findings}
                      onChange={(event) => updateRoutineForm(inspection, { findings: event.target.value })}
                      className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                    />
                  </label>
                  <label className="mt-3 block text-sm font-medium text-slate-700">
                    Recommendations
                    <textarea
                      maxLength={10000}
                      rows={2}
                      value={form.recommendations}
                      onChange={(event) => updateRoutineForm(inspection, { recommendations: event.target.value })}
                      className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() => void completeRoutineInspection(inspection)}
                    disabled={
                      savingInspectionId !== null
                      || form.checklist.some((entry) => !['complete', 'completed'].includes(entry.status ?? ''))
                      || (form.checklist.length === 0 && !form.findings.trim())
                    }
                    className="mt-3 inline-flex items-center gap-2 rounded-lg bg-emerald-800 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                  >
                    {savingInspectionId === inspection.id ? 'Submitting…' : 'Submit inspection'}
                  </button>
                </article>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
