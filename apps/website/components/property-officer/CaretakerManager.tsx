'use client';

import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { Mail, RefreshCw, UserPlus, UserRoundX } from 'lucide-react';
import { usePropertyOfficerPortal } from './PortalShell';

type PropertyRecord = {
  id: number;
  branch_id: number;
  branch_name?: string;
  name: string;
};

type Invitation = {
  id: number;
  email: string;
  full_name: string;
  property_id: number;
  property_name: string;
  property_ids?: number[];
  property_names?: string[];
  expires_at: string;
  status: string;
};

type Caretaker = {
  id: number;
  full_name: string;
  email: string;
  phone: string;
  property_id: number;
  property_name: string;
};

type ApiPayload = {
  properties?: PropertyRecord[];
  invites?: Invitation[];
  caretakers?: Caretaker[];
  invitation?: Invitation;
  error?: string;
  detail?: string;
};

const fieldClass = 'mt-1.5 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm';

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    cache: 'no-store',
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  });
  const payload = await response.json().catch(() => null) as ApiPayload | null;
  if (!response.ok) {
    throw new Error(payload?.detail || payload?.error || `Request failed with status ${response.status}.`);
  }
  if (!payload) throw new Error('The server returned an empty response.');
  return payload as T;
}

export default function CaretakerManager() {
  const { branchIds } = usePropertyOfficerPortal();
  const [properties, setProperties] = useState<PropertyRecord[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [caretakers, setCaretakers] = useState<Caretaker[]>([]);
  const [selectedPropertyIds, setSelectedPropertyIds] = useState<number[]>([]);
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [identity, setIdentity] = useState('');
  const [kraPin, setKraPin] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reloadKey, setReloadKey] = useState(0);

  const visibleProperties = useMemo(
    () => properties.filter((property) => branchIds.has(property.branch_id)),
    [branchIds, properties],
  );
  const visibleInvitations = useMemo(() => {
    const ids = new Set(visibleProperties.map((property) => property.id));
    return invitations.filter((invitation) => (
      invitation.property_ids?.some((id) => ids.has(id)) ?? ids.has(invitation.property_id)
    ));
  }, [invitations, visibleProperties]);
  const visibleCaretakers = useMemo(() => {
    const ids = new Set(visibleProperties.map((property) => property.id));
    return caretakers.filter((caretaker) => ids.has(caretaker.property_id));
  }, [caretakers, visibleProperties]);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [propertyPayload, caretakerPayload] = await Promise.all([
        requestJson<{ properties?: PropertyRecord[] }>('/api/property-officer/properties'),
        requestJson<ApiPayload>('/api/property-officer/caretakers'),
      ]);
      setProperties(propertyPayload.properties ?? []);
      setInvitations(caretakerPayload.invites ?? []);
      setCaretakers(caretakerPayload.caretakers ?? []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load caretaker records.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  useEffect(() => {
    const visibleIds = new Set(visibleProperties.map((property) => property.id));
    setSelectedPropertyIds((current) => {
      const retained = current.filter((id) => visibleIds.has(id));
      return retained.length > 0 || visibleProperties.length === 0
        ? retained
        : [visibleProperties[0].id];
    });
  }, [visibleProperties]);

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selectedPropertyIds.length === 0) return;
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const payload = await requestJson<ApiPayload>('/api/property-officer/caretakers', {
        method: 'POST',
        body: JSON.stringify({
          property_ids: selectedPropertyIds,
          full_name: fullName,
          email,
          phone,
          id_or_registration_number: identity,
          kra_pin: kraPin,
        }),
      });
      if (!payload.invitation) throw new Error('The server did not return the invitation.');
      setNotice(`Invitation created. An activation link and one-time code were emailed to ${email}.`);
      setFullName('');
      setEmail('');
      setPhone('');
      setIdentity('');
      setKraPin('');
      setReloadKey((value) => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to create the caretaker invitation.');
      setReloadKey((value) => value + 1);
    } finally {
      setSaving(false);
    }
  }

  async function resend(invitationId: number) {
    setBusyId(invitationId);
    setError('');
    setNotice('');
    try {
      const payload = await requestJson<ApiPayload>(`/api/property-officer/caretakers/${invitationId}/resend`, {
        method: 'POST',
      });
      if (!payload.invitation) throw new Error('The server did not return the renewed invitation.');
      setNotice(`A new activation link and code were emailed to ${payload.invitation.email}. The previous credentials are no longer valid.`);
      setReloadKey((value) => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to resend the caretaker invitation.');
      setReloadKey((value) => value + 1);
    } finally {
      setBusyId(null);
    }
  }

  async function revokeInvitation(invitationId: number) {
    setBusyId(invitationId);
    setError('');
    setNotice('');
    try {
      await requestJson(`/api/property-officer/caretakers/${invitationId}/revoke`, { method: 'POST' });
      setNotice('Caretaker invitation revoked.');
      setReloadKey((value) => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to revoke the invitation.');
    } finally {
      setBusyId(null);
    }
  }

  async function removeAccess(caretaker: Caretaker) {
    setBusyId(caretaker.id);
    setError('');
    setNotice('');
    try {
      await requestJson('/api/property-officer/caretakers/revoke', {
        method: 'POST',
        body: JSON.stringify({ property_id: caretaker.property_id, caretaker_id: caretaker.id }),
      });
      setNotice(`Access removed for ${caretaker.full_name} on ${caretaker.property_name}.`);
      setReloadKey((value) => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to remove caretaker access.');
    } finally {
      setBusyId(null);
    }
  }

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-800">Property operations</p>
          <h1 className="mt-1 text-3xl font-bold text-slate-950">Caretaker onboarding</h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Invite caretakers to assigned properties and manage their property access.
          </p>
        </div>
        <button onClick={() => setReloadKey((value) => value + 1)} disabled={loading}
          className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold disabled:opacity-50">
          <RefreshCw size={16} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </header>

      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}
      {notice && <p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">{notice}</p>}

      <section className="grid gap-6 xl:grid-cols-[minmax(0,0.8fr)_minmax(0,1.4fr)]">
        <form onSubmit={invite} className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-emerald-50 text-emerald-800"><UserPlus size={19} /></span>
            <div>
              <h2 className="font-bold">Invite caretaker</h2>
              <p className="text-xs text-slate-500">A secure activation link and one-time code are emailed to the caretaker.</p>
            </div>
          </div>
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold">Properties</legend>
            <p className="text-xs text-slate-500">Select every property this caretaker will be responsible for.</p>
            <div className="max-h-48 space-y-2 overflow-y-auto rounded-xl border border-slate-200 p-3">
              {visibleProperties.map((property) => (
                <label key={property.id} className="flex items-start gap-2 text-sm font-medium">
                  <input
                    type="checkbox"
                    checked={selectedPropertyIds.includes(property.id)}
                    onChange={(event) => {
                      setSelectedPropertyIds((current) => (
                        event.target.checked
                          ? [...current, property.id]
                          : current.filter((id) => id !== property.id)
                      ));
                    }}
                    className="mt-0.5 h-4 w-4 rounded border-slate-300 text-emerald-800 focus:ring-emerald-700"
                  />
                  <span>{property.name}{property.branch_name ? ` · ${property.branch_name}` : ''}</span>
                </label>
              ))}
              {!loading && visibleProperties.length === 0 && (
                <p className="text-sm text-slate-500">No properties are available in your assigned branches.</p>
              )}
            </div>
          </fieldset>
          <label className="block text-sm font-semibold">Caretaker email
            <input required type="email" value={email} onChange={(event) => setEmail(event.target.value)}
              autoComplete="email" className={fieldClass} />
          </label>
          <label className="block text-sm font-semibold">Full name
            <input required maxLength={255} value={fullName} onChange={(event) => setFullName(event.target.value)}
              autoComplete="name" className={fieldClass} />
          </label>
          <label className="block text-sm font-semibold">Phone
            <input required type="tel" value={phone} onChange={(event) => setPhone(event.target.value)}
              placeholder="07XX XXX XXX" autoComplete="tel" className={fieldClass} />
          </label>
          <label className="block text-sm font-semibold">ID or registration number
            <input required maxLength={100} value={identity} onChange={(event) => setIdentity(event.target.value)}
              className={fieldClass} />
          </label>
          <label className="block text-sm font-semibold">KRA PIN <span className="font-normal text-slate-500">optional</span>
            <input maxLength={20} value={kraPin} onChange={(event) => setKraPin(event.target.value)}
              className={fieldClass} />
          </label>
          <button type="submit" disabled={saving || selectedPropertyIds.length === 0}
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-800 px-4 py-3 font-bold text-white disabled:opacity-50">
            <Mail size={17} /> {saving ? 'Creating invitation…' : 'Create invitation'}
          </button>
        </form>

        <section className="space-y-6">
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="font-bold">Active caretaker assignments</h2>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[560px] text-left text-sm">
                <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                  <tr><th className="px-3 py-3">Caretaker</th><th className="px-3 py-3">Property</th><th className="px-3 py-3 text-right">Action</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {visibleCaretakers.map((caretaker) => (
                    <tr key={`${caretaker.id}-${caretaker.property_id}`}>
                      <td className="px-3 py-3">
                        <p className="font-semibold">{caretaker.full_name}</p>
                        <p className="mt-1 text-xs text-slate-500">{caretaker.email} · {caretaker.phone}</p>
                      </td>
                      <td className="px-3 py-3">{caretaker.property_name}</td>
                      <td className="px-3 py-3 text-right">
                        <button onClick={() => void removeAccess(caretaker)} disabled={busyId === caretaker.id}
                          className="inline-flex items-center gap-1 rounded-lg border border-red-200 px-2.5 py-2 text-xs font-semibold text-red-800 disabled:opacity-50">
                          <UserRoundX size={14} /> Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!loading && visibleCaretakers.length === 0 && (
                    <tr><td colSpan={3} className="px-3 py-8 text-center text-sm text-slate-500">No active caretakers in the selected branches.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="font-bold">Pending and recent invitations</h2>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[560px] text-left text-sm">
                <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                  <tr><th className="px-3 py-3">Caretaker</th><th className="px-3 py-3">Property</th><th className="px-3 py-3">Status</th><th className="px-3 py-3 text-right">Action</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {visibleInvitations.map((invitation) => (
                    <tr key={invitation.id}>
                      <td className="px-3 py-3">
                        <p className="font-semibold">{invitation.full_name || invitation.email}</p>
                        <p className="mt-1 text-xs text-slate-500">{invitation.email}</p>
                      </td>
                      <td className="px-3 py-3">{invitation.property_name}</td>
                      <td className="px-3 py-3 capitalize">{invitation.status.replaceAll('_', ' ')}</td>
                      <td className="space-x-2 px-3 py-3 text-right">
                        {(invitation.status === 'pending_activation' || invitation.status === 'expired') && (
                          <>
                            <button onClick={() => void resend(invitation.id)} disabled={busyId === invitation.id}
                              className="rounded-lg border border-emerald-800 px-2.5 py-2 text-xs font-semibold text-emerald-900 disabled:opacity-50">
                              Resend
                            </button>
                            <button onClick={() => void revokeInvitation(invitation.id)} disabled={busyId === invitation.id}
                              className="rounded-lg border border-red-200 px-2.5 py-2 text-xs font-semibold text-red-800 disabled:opacity-50">
                              Revoke
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                  {!loading && visibleInvitations.length === 0 && (
                    <tr><td colSpan={4} className="px-3 py-8 text-center text-sm text-slate-500">No caretaker invitations found.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      </section>
    </main>
  );
}
