'use client';

import Image from 'next/image';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { CheckCircle, ClipboardCheck, RefreshCw } from 'lucide-react';
import { PortalLoading, PortalPagination, usePortalPagination } from '../../../components/property-officer/PortalUI';

type HandoverCondition = {
  area: string;
  status: string;
  notes: string;
};

type HandoverPhoto = {
  name: string;
  evidence_type: string | null;
  url: string;
};

type HandoverInventoryItem = {
  name: string;
  property_quantity: number;
  issued_quantity: number | null;
};

type MoveInHandover = {
  id: number;
  tenancy_id: number;
  property_name: string;
  unit_code: string;
  tenant_name: string;
  status: string;
  keys_issued: number | null;
  handover_inventory: HandoverInventoryItem[];
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
  const [propertyFilter, setPropertyFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [search, setSearch] = useState('');

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

  const propertyOptions = useMemo(
    () => [...new Set(handovers.map((handover) => handover.property_name))].sort(),
    [handovers],
  );
  const statusOptions = useMemo(
    () => [...new Set(handovers.map((handover) => handover.status))].sort(),
    [handovers],
  );
  const filteredHandovers = useMemo(() => {
    const query = search.trim().toLowerCase();
    return handovers.filter((handover) => {
      const matchesProperty = propertyFilter === 'all' || handover.property_name === propertyFilter;
      const matchesStatus = statusFilter === 'all' || handover.status === statusFilter;
      const matchesSearch = !query || [
        handover.property_name,
        handover.unit_code,
        handover.tenant_name,
      ].some((value) => value.toLowerCase().includes(query));
      return matchesProperty && matchesStatus && matchesSearch;
    });
  }, [handovers, propertyFilter, search, statusFilter]);
  const handoverPagination = usePortalPagination(filteredHandovers);

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
      <header className="flex justify-end">
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
      {!loading && !error && filteredHandovers.length === 0 && (
        <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center">
          <CheckCircle size={28} className="mx-auto text-emerald-700" />
          <h2 className="mt-3 font-semibold text-slate-900">{handovers.length ? 'No handovers match these filters' : 'No handovers awaiting confirmation'}</h2>
          <p className="mt-1 text-sm text-slate-600">{handovers.length ? 'Adjust the property, status, or search filters to see more records.' : 'Submitted caretaker move-in records will appear here.'}</p>
        </div>
      )}

      {loading ? <PortalLoading label="Loading submitted move-in handovers…" rows={4} /> : (
      <div className="space-y-4">
        <div className="grid gap-3 rounded-2xl border border-slate-200 bg-white p-4 sm:grid-cols-3">
          <label className="text-xs font-semibold text-slate-600">Search handovers
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Tenant, property, or unit" className="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm" />
          </label>
          <label className="text-xs font-semibold text-slate-600">Property
            <select value={propertyFilter} onChange={(event) => setPropertyFilter(event.target.value)} className="mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm">
              <option value="all">All properties</option>
              {propertyOptions.map((property) => <option key={property}>{property}</option>)}
            </select>
          </label>
          <label className="text-xs font-semibold text-slate-600">Handover status
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm">
              <option value="all">All statuses</option>
              {statusOptions.map((status) => <option key={status} value={status}>{status.replaceAll('_', ' ')}</option>)}
            </select>
          </label>
        </div>
        <div className="grid gap-5 xl:grid-cols-2">
        {handoverPagination.pageRecords.map((handover) => (
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

            {handover.handover_inventory.length > 0 && (
              <section className="mt-5">
                <h3 className="text-sm font-semibold text-slate-800">Handover inventory</h3>
                <div className="mt-2 divide-y divide-slate-100 rounded-xl border border-slate-200">
                  {handover.handover_inventory.map((item) => (
                    <div key={item.name} className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm">
                      <span className="font-medium text-slate-700">{item.name}</span>
                      <span className="text-slate-600">
                        Expected: {item.property_quantity} · Issued: {item.issued_quantity ?? 'Not recorded'}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            )}

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
                        alt={`${photo.evidence_type === 'water_meter' ? 'Water meter' : photo.evidence_type === 'condition' ? 'Condition' : 'Handover'} photo: ${photo.name}`}
                        width={144}
                        height={104}
                        unoptimized
                        className="h-24 w-36 object-cover"
                      />
                    </a>
                  ))}
                </div>
                <ul className="mt-2 flex flex-wrap gap-2 text-xs text-slate-600">
                  {handover.photos.map((photo) => (
                    <li key={`${photo.url}-label`} className="rounded-full bg-slate-100 px-2.5 py-1">
                      {photo.evidence_type === 'water_meter'
                        ? 'Water meter'
                        : photo.evidence_type === 'condition'
                          ? 'Condition'
                          : 'Handover'}: {photo.name}
                    </li>
                  ))}
                </ul>
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
      <PortalPagination {...handoverPagination} total={filteredHandovers.length} onPageChange={handoverPagination.setPage} onPageSizeChange={handoverPagination.setPageSize} />
      </div>
      )}
    </div>
  );
}
