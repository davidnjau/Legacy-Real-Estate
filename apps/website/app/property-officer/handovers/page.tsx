'use client';

import Image from 'next/image';
import { useCallback, useEffect, useState } from 'react';
import { CheckCircle, ClipboardCheck, RefreshCw } from 'lucide-react';

type HandoverCondition = {
  area: string;
  status: string;
  notes: string;
};

type HandoverPhoto = {
  name: string;
  url: string;
};

type MoveInHandover = {
  id: number;
  tenancy_id: number;
  property_name: string;
  unit_code: string;
  tenant_name: string;
  status: string;
  keys_issued: number | null;
  condition_checklist: HandoverCondition[];
  meter_requirements: {
    utility_type: string;
    name: string;
    meter: string | null;
    configured: boolean;
  }[];
  initial_meter_readings: {
    utility_type: string;
    current_value: string;
    name?: string;
    meter?: string | null;
  }[];
  photos: HandoverPhoto[];
  submitted_at: string | null;
};

type HandoverResponse = {
  handovers?: MoveInHandover[];
  error?: string;
  detail?: string;
};

export default function PropertyOfficerHandoversPage() {
  const [handovers, setHandovers] = useState<MoveInHandover[]>([]);
  const [loading, setLoading] = useState(true);
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const loadHandovers = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/property-officer/move-in-handovers', { cache: 'no-store' });
      const payload = (await response.json()) as HandoverResponse;
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || 'Unable to load submitted move-in handovers.');
      }
      setHandovers(Array.isArray(payload.handovers) ? payload.handovers : []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load submitted move-in handovers.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadHandovers();
  }, [loadHandovers]);

  const confirmHandover = async (handover: MoveInHandover) => {
    setConfirmingId(handover.id);
    setError('');
    setNotice('');
    try {
      const response = await fetch(`/api/property-officer/move-in-handovers/${handover.id}/confirm`, {
        method: 'POST',
      });
      const payload = (await response.json()) as HandoverResponse;
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || 'Unable to confirm this move-in handover.');
      }
      setHandovers((current) => current.filter((entry) => entry.id !== handover.id));
      setNotice(`Move-in handover confirmed for ${handover.property_name}, unit ${handover.unit_code}.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to confirm this move-in handover.');
    } finally {
      setConfirmingId(null);
    }
  };

  return (
    <div className="space-y-6 p-4 sm:p-6 lg:p-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-emerald-700">Property operations</p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">Move-in handover reviews</h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-600">
            Review the caretaker’s room condition, opening readings, keys, and private photo evidence before confirming the handover.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void loadHandovers()}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
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
      {loading && <p className="text-sm text-slate-500">Loading handovers awaiting review…</p>}
      {!loading && !error && handovers.length === 0 && (
        <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center">
          <CheckCircle size={28} className="mx-auto text-emerald-700" />
          <h2 className="mt-3 font-semibold text-slate-900">No handovers awaiting confirmation</h2>
          <p className="mt-1 text-sm text-slate-600">Submitted caretaker move-in records will appear here.</p>
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-2">
        {handovers.map((handover) => (
          <article key={handover.id} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-start gap-3">
              <span className="rounded-xl bg-emerald-50 p-2 text-emerald-800">
                <ClipboardCheck size={18} />
              </span>
              <div className="min-w-0 flex-1">
                <h2 className="font-semibold text-slate-900">{handover.property_name} · Unit {handover.unit_code}</h2>
                <p className="mt-1 text-sm text-slate-600">Tenant: {handover.tenant_name}</p>
                <p className="text-xs text-slate-500">
                  Submitted {handover.submitted_at ? new Date(handover.submitted_at).toLocaleString('en-KE') : 'recently'}
                </p>
              </div>
            </div>

            <div className="mt-5 grid gap-3 sm:grid-cols-2">
              <div className="rounded-xl bg-slate-50 p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Keys issued</p>
                <p className="mt-1 text-sm font-semibold text-slate-800">{handover.keys_issued ?? 'Not recorded'}</p>
              </div>
              <div className="rounded-xl bg-slate-50 p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Opening readings</p>
                {handover.initial_meter_readings.length > 0 ? (
                  <ul className="mt-1 space-y-1 text-sm text-slate-800">
                    {handover.initial_meter_readings.map((reading) => {
                      const requirement = handover.meter_requirements.find(
                        (item) => item.utility_type === reading.utility_type,
                      );
                      return (
                        <li key={reading.utility_type}>
                          {reading.name ?? requirement?.name ?? reading.utility_type}: {reading.current_value}
                          {(reading.meter ?? requirement?.meter) ? ` · ${reading.meter ?? requirement?.meter}` : ''}
                        </li>
                      );
                    })}
                  </ul>
                ) : <p className="mt-1 text-sm text-slate-600">No metered water or electricity utility.</p>}
              </div>
            </div>

            <section className="mt-5">
              <h3 className="text-sm font-semibold text-slate-800">Room and fixture condition</h3>
              <div className="mt-2 divide-y divide-slate-100 rounded-xl border border-slate-200">
                {handover.condition_checklist.map((condition) => (
                  <div key={condition.area} className="flex flex-wrap items-start justify-between gap-2 p-3 text-sm">
                    <span className="font-medium text-slate-700">{condition.area}</span>
                    <span className="text-right text-slate-600">
                      <span className={condition.status === 'needs_attention' ? 'font-semibold text-amber-800' : ''}>
                        {condition.status.replaceAll('_', ' ')}
                      </span>
                      {condition.notes && <span className="block text-xs">{condition.notes}</span>}
                    </span>
                  </div>
                ))}
              </div>
            </section>

            {handover.photos.length > 0 && (
              <section className="mt-5">
                <h3 className="text-sm font-semibold text-slate-800">Photo evidence</h3>
                <div className="mt-2 flex flex-wrap gap-3">
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
                        width={144}
                        height={104}
                        unoptimized
                        className="h-24 w-36 object-cover"
                      />
                    </a>
                  ))}
                </div>
              </section>
            )}

            <button
              type="button"
              onClick={() => void confirmHandover(handover)}
              disabled={confirmingId !== null}
              className="mt-5 inline-flex items-center gap-2 rounded-lg bg-emerald-800 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              {confirmingId === handover.id ? 'Confirming…' : 'Confirm move-in handover'}
            </button>
          </article>
        ))}
      </div>
    </div>
  );
}
