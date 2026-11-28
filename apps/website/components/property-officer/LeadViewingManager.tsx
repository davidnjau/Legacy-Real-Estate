'use client';

import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import { usePropertyOfficerPortal } from './PortalShell';
import { PortalLoading, PortalPagination, usePortalPagination } from './PortalUI';

type Viewing = {
  id: number;
  lead_id: number;
  scheduled_for: string;
  status: 'scheduled' | 'completed' | 'cancelled';
  notes: string;
  assigned_to_name: string | null;
};

type Lead = {
  id: number;
  branch_id: number;
  branch_name: string;
  property_name: string | null;
  unit_code: string | null;
  full_name: string;
  phone: string;
  email: string;
  preference: string;
  budget: string | null;
  status: 'new' | 'viewing_booked' | 'applied' | 'converted' | 'lost';
  notes: string;
  viewings: Viewing[];
};

const leadStatuses = ['new', 'viewing_booked', 'applied', 'converted', 'lost'] as const;
const viewingStatuses = ['scheduled', 'completed', 'cancelled'] as const;

async function responseError(response: Response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || payload.error || 'The request could not be completed.');
  return payload;
}

export default function LeadViewingManager() {
  const { agency, branchIds } = usePropertyOfficerPortal();
  const branches = (agency?.branches ?? []).filter((branch) => branchIds.has(branch.id));
  const [leads, setLeads] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [branchId, setBranchId] = useState('');
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [preference, setPreference] = useState('');
  const [budget, setBudget] = useState('');
  const [scheduleLead, setScheduleLead] = useState('');
  const [scheduledFor, setScheduledFor] = useState('');
  const [viewingNotes, setViewingNotes] = useState('');
  const [search, setSearch] = useState('');
  const [branchFilter, setBranchFilter] = useState('all');
  const [leadStatusFilter, setLeadStatusFilter] = useState('all');
  const [viewingFilter, setViewingFilter] = useState('all');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [leadResponse, viewingResponse] = await Promise.all([
        fetch('/api/property-officer/leads', { cache: 'no-store' }),
        fetch('/api/property-officer/viewings', { cache: 'no-store' }),
      ]);
      const leadPayload = await responseError(leadResponse);
      const viewingPayload = await responseError(viewingResponse);
      const viewings = viewingPayload.viewings as Viewing[];
      setLeads((leadPayload.leads as Lead[])
        .filter((lead) => branchIds.has(lead.branch_id))
        .map((lead) => ({
          ...lead,
          viewings: viewings.filter((viewing) => viewing.lead_id === lead.id),
        })));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load leads and viewings.');
    } finally {
      setLoading(false);
    }
  }, [branchIds]);

  useEffect(() => {
    void load();
  }, [load]);

  const filteredLeads = useMemo(() => {
    const query = search.trim().toLowerCase();
    return leads.filter((lead) => {
      const matchesBranch = branchFilter === 'all' || String(lead.branch_id) === branchFilter;
      const matchesLeadStatus = leadStatusFilter === 'all' || lead.status === leadStatusFilter;
      const matchesViewing = viewingFilter === 'all'
        || (viewingFilter === 'none'
          ? lead.viewings.length === 0
          : lead.viewings.some((viewing) => viewing.status === viewingFilter));
      const matchesSearch = !query || [
        lead.full_name,
        lead.phone,
        lead.email,
        lead.branch_name,
        lead.property_name ?? '',
        lead.unit_code ?? '',
        lead.preference,
      ].some((value) => value.toLowerCase().includes(query));
      return matchesBranch && matchesLeadStatus && matchesViewing && matchesSearch;
    });
  }, [branchFilter, leadStatusFilter, leads, search, viewingFilter]);
  const leadPagination = usePortalPagination(filteredLeads);

  async function createLead(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError('');
    try {
      const response = await fetch('/api/property-officer/leads', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          branch_id: Number(branchId),
          full_name: name,
          phone,
          email,
          preference,
          budget: budget || null,
        }),
      });
      await responseError(response);
      setName('');
      setPhone('');
      setEmail('');
      setPreference('');
      setBudget('');
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to create the lead.');
    } finally {
      setSaving(false);
    }
  }

  async function scheduleViewing(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError('');
    try {
      const response = await fetch('/api/property-officer/viewings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lead_id: Number(scheduleLead),
          scheduled_for: new Date(scheduledFor).toISOString(),
          notes: viewingNotes,
        }),
      });
      await responseError(response);
      setScheduleLead('');
      setScheduledFor('');
      setViewingNotes('');
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to schedule the viewing.');
    } finally {
      setSaving(false);
    }
  }

  async function updateLeadStatus(lead: Lead, status: Lead['status']) {
    setError('');
    try {
      const response = await fetch(`/api/property-officer/leads/${lead.id}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      await responseError(response);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to update the lead.');
    }
  }

  async function updateViewingStatus(viewing: Viewing, status: Viewing['status']) {
    setError('');
    try {
      const response = await fetch(`/api/property-officer/viewings/${viewing.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      await responseError(response);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to update the viewing.');
    }
  }

  return (
    <section className="space-y-6">
      {error && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</div>}

      <div className="grid gap-5 lg:grid-cols-2">
        <form onSubmit={createLead} className="space-y-3 rounded-2xl border border-slate-200 bg-white p-5">
          <h2 className="font-bold text-slate-900">Record a lead</h2>
          <label className="block text-xs font-semibold text-slate-700">Branch
            <select required value={branchId} onChange={(event) => setBranchId(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
              <option value="">Select branch</option>
              {branches.map((branch) => <option key={branch.id} value={branch.id}>{branch.name}</option>)}
            </select>
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <input required value={name} onChange={(event) => setName(event.target.value)} placeholder="Client name" aria-label="Client name" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input required value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="Phone" aria-label="Phone" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="Email (optional)" aria-label="Email" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <input inputMode="decimal" value={budget} onChange={(event) => setBudget(event.target.value)} placeholder="Budget (KES, optional)" aria-label="Budget" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </div>
          <input value={preference} onChange={(event) => setPreference(event.target.value)} placeholder="Property or unit preference" aria-label="Property or unit preference" className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <button disabled={saving || !branches.length} className="rounded-lg bg-emerald-800 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">{saving ? 'Saving…' : 'Save lead'}</button>
        </form>

        <form onSubmit={scheduleViewing} className="space-y-3 rounded-2xl border border-slate-200 bg-white p-5">
          <h2 className="font-bold text-slate-900">Schedule a viewing</h2>
          <select required value={scheduleLead} onChange={(event) => setScheduleLead(event.target.value)} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" aria-label="Lead">
            <option value="">Select lead</option>
            {leads.map((lead) => <option key={lead.id} value={lead.id}>{lead.full_name} · {lead.phone}</option>)}
          </select>
          <input required type="datetime-local" value={scheduledFor} onChange={(event) => setScheduledFor(event.target.value)} aria-label="Viewing date and time" className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <textarea value={viewingNotes} onChange={(event) => setViewingNotes(event.target.value)} placeholder="Appointment notes (optional)" aria-label="Appointment notes" className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" rows={3} />
          <button disabled={saving || !leads.length} className="rounded-lg bg-emerald-800 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">{saving ? 'Saving…' : 'Schedule viewing'}</button>
        </form>
      </div>

      <div data-portal-card className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div className="grid gap-3 border-b border-slate-100 p-4 sm:grid-cols-2 xl:grid-cols-4">
          <label className="text-xs font-semibold text-slate-600">Search leads
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Name, contact, property, or unit" className="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm" />
          </label>
          <label className="text-xs font-semibold text-slate-600">Branch
            <select value={branchFilter} onChange={(event) => setBranchFilter(event.target.value)} className="mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm">
              <option value="all">All assigned branches</option>
              {branches.map((branch) => <option key={branch.id} value={branch.id}>{branch.name}</option>)}
            </select>
          </label>
          <label className="text-xs font-semibold text-slate-600">Lead stage
            <select value={leadStatusFilter} onChange={(event) => setLeadStatusFilter(event.target.value)} className="mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm">
              <option value="all">All lead stages</option>
              {leadStatuses.map((status) => <option key={status} value={status}>{status.replaceAll('_', ' ')}</option>)}
            </select>
          </label>
          <label className="text-xs font-semibold text-slate-600">Viewing status
            <select value={viewingFilter} onChange={(event) => setViewingFilter(event.target.value)} className="mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm">
              <option value="all">All viewing records</option>
              <option value="none">No viewing scheduled</option>
              {viewingStatuses.map((status) => <option key={status} value={status}>{status.replaceAll('_', ' ')}</option>)}
            </select>
          </label>
        </div>
        <div className="overflow-x-auto">
        <table className="w-full min-w-[900px] text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500"><tr>
            <th className="px-4 py-3">Lead</th><th className="px-4 py-3">Branch</th><th className="px-4 py-3">Interest</th><th className="px-4 py-3">Budget</th><th className="px-4 py-3">Appointments</th><th className="px-4 py-3">Lead status</th>
          </tr></thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? <tr><td colSpan={6} className="p-4"><PortalLoading label="Loading leads and appointments…" rows={4} /></td></tr>
              : leadPagination.pageRecords.map((lead) => <tr key={lead.id} className="align-top transition-colors hover:bg-[#f8faf7]">
                <td className="px-4 py-4"><p className="font-semibold text-slate-900">{lead.full_name}</p><p className="text-xs text-slate-500">{lead.phone}{lead.email ? ` · ${lead.email}` : ''}</p></td>
                <td className="px-4 py-4">{lead.branch_name}</td>
                <td className="px-4 py-4">{lead.preference || lead.property_name || 'Not specified'}{lead.unit_code ? ` · ${lead.unit_code}` : ''}</td>
                <td className="px-4 py-4">{lead.budget ? `KES ${Number(lead.budget).toLocaleString()}` : '—'}</td>
                <td className="space-y-2 px-4 py-4">{lead.viewings.length ? lead.viewings.map((viewing) => <div key={viewing.id} className="space-y-1">
                  <p>{new Date(viewing.scheduled_for).toLocaleString()}</p>
                  {viewing.assigned_to_name && <p className="text-xs text-slate-500">{viewing.assigned_to_name}</p>}
                  <select aria-label={`Viewing status for ${lead.full_name}`} value={viewing.status} onChange={(event) => void updateViewingStatus(viewing, event.target.value as Viewing['status'])} className="rounded border border-slate-300 px-2 py-1 text-xs">
                    {viewingStatuses.map((status) => <option key={status} value={status}>{status.replace('_', ' ')}</option>)}
                  </select>
                </div>) : <span className="text-slate-400">None scheduled</span>}</td>
                <td className="px-4 py-4"><select aria-label={`Lead status for ${lead.full_name}`} value={lead.status} onChange={(event) => void updateLeadStatus(lead, event.target.value as Lead['status'])} className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs">
                  {leadStatuses.map((status) => <option key={status} value={status}>{status.replace('_', ' ')}</option>)}
                </select></td>
              </tr>)}
            {!loading && filteredLeads.length === 0 && <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">No leads match the selected filters.</td></tr>}
          </tbody>
        </table>
        </div>
        {!loading && <PortalPagination {...leadPagination} total={filteredLeads.length} onPageChange={leadPagination.setPage} onPageSizeChange={leadPagination.setPageSize} />}
      </div>
    </section>
  );
}
