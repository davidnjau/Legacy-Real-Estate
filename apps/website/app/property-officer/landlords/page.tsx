'use client';

import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { Mail, RefreshCw, Search, UserPlus } from 'lucide-react';
import { usePropertyOfficerPortal } from '../../../components/property-officer/PortalShell';

type Branch = { id: number; name: string; town: string };
type PropertyOption = {
  id: number;
  name: string;
  branch_id: number;
  branch_name: string;
  landlord_id: number | null;
  landlord_name: string | null;
};
type LandlordAccount = {
  id: number;
  name: string;
  email: string;
  phone: string;
  branch: Branch;
  account_status: 'not_invited' | 'pending_activation' | 'active';
};
type LandlordResponse = {
  landlords: LandlordAccount[];
  branches: Branch[];
  properties?: PropertyOption[];
};

const STATUS_LABELS: Record<LandlordAccount['account_status'], string> = {
  not_invited: 'Not invited',
  pending_activation: 'Pending activation',
  active: 'Active',
};

function responseError(payload: { detail?: unknown; error?: unknown }, fallback: string) {
  if (typeof payload.detail === 'string') return payload.detail;
  if (typeof payload.error === 'string') return payload.error;
  return fallback;
}

export default function LandlordOnboardingPage() {
  const { agency } = usePropertyOfficerPortal();
  const [accounts, setAccounts] = useState<LandlordAccount[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [properties, setProperties] = useState<PropertyOption[]>([]);
  const [branchId, setBranchId] = useState('');
  const [propertyId, setPropertyId] = useState('');
  const [filterBranchId, setFilterBranchId] = useState('all');
  const [search, setSearch] = useState('');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [identity, setIdentity] = useState('');
  const [kraPin, setKraPin] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [busyLandlordId, setBusyLandlordId] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const loadAccounts = useCallback(async () => {
    if (!agency) return;
    setLoading(true);
    setError('');
    try {
      const response = await fetch(
        `/api/property-officer/landlords?agency_id=${encodeURIComponent(String(agency.id))}`,
        { cache: 'no-store' },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(responseError(payload, 'Unable to load landlord accounts.'));
      const result = payload as LandlordResponse;
      setAccounts(result.landlords);
      setBranches(result.branches);
      setProperties(result.properties ?? []);
      setBranchId((current) => current || String(result.branches[0]?.id ?? ''));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load landlord accounts.');
    } finally {
      setLoading(false);
    }
  }, [agency]);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  const visibleAccounts = useMemo(() => {
    const query = search.trim().toLowerCase();
    return accounts.filter((account) => {
      const matchesBranch = filterBranchId === 'all' || String(account.branch.id) === filterBranchId;
      const matchesSearch = !query || [account.name, account.email, account.phone, account.branch.name]
        .some((value) => value.toLowerCase().includes(query));
      return matchesBranch && matchesSearch;
    });
  }, [accounts, filterBranchId, search]);
  const branchProperties = properties.filter((property) => String(property.branch_id) === branchId);

  async function getCsrfToken() {
    const response = await fetch('/api/auth/csrf', { cache: 'no-store' });
    const payload = await response.json();
    if (!response.ok || typeof payload.csrfToken !== 'string') {
      throw new Error('Unable to prepare a secure request. Please try again.');
    }
    return payload.csrfToken as string;
  }

  async function createLandlord(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    setNotice('');
    if (!branchId) {
      setError('Select one of your assigned branches.');
      return;
    }
    setSaving(true);
    try {
      const csrfToken = await getCsrfToken();
      const response = await fetch('/api/property-officer/landlords', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({
          branch_id: branchId,
          property_id: propertyId || null,
          name,
          email,
          phone,
          id_or_registration_number: identity,
          kra_pin: kraPin,
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        await loadAccounts();
        throw new Error(responseError(payload, 'Unable to create the landlord account.'));
      }
      setName('');
      setEmail('');
      setPhone('');
      setIdentity('');
      setKraPin('');
      setPropertyId('');
      setNotice(`Activation email sent to ${email.trim()}.`);
      await loadAccounts();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to create the landlord account.');
    } finally {
      setSaving(false);
    }
  }

  async function sendActivation(landlord: LandlordAccount) {
    setError('');
    setNotice('');
    setBusyLandlordId(landlord.id);
    try {
      const csrfToken = await getCsrfToken();
      const endpoint = landlord.account_status === 'not_invited'
        ? '/api/property-officer/landlords'
        : `/api/property-officer/landlords/${landlord.id}/resend-activation`;
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify(landlord.account_status === 'not_invited' ? { landlord_id: landlord.id } : {}),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(responseError(payload, 'Unable to send the activation email.'));
      setNotice(`Activation email sent to ${landlord.email}.`);
      await loadAccounts();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to send the activation email.');
    } finally {
      setBusyLandlordId(null);
    }
  }

  if (!agency) {
    return (
      <main className="p-6 lg:p-9">
        <div className="rounded-2xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900">
          No active Property Officer agency is available for this account.
        </div>
      </main>
    );
  }

  return (
    <main className="space-y-7 p-5 lg:p-9">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-800">{agency.name}</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight">Landlords & onboarding</h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Create landlord portal access for your assigned branches and resend pending activation emails.
          </p>
        </div>
        <button onClick={() => void loadAccounts()} disabled={loading}
          className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold hover:bg-slate-50 disabled:opacity-50">
          <RefreshCw size={16} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </header>

      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}
      {notice && <p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">{notice}</p>}

      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1.5fr)]">
        <form onSubmit={createLandlord} className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-emerald-50 text-emerald-800"><UserPlus size={19} /></span>
            <div>
              <h2 className="font-bold">Create landlord account</h2>
              <p className="text-xs text-slate-500">The landlord receives an email to activate their login.</p>
            </div>
          </div>
          <label className="block text-sm font-semibold">Assigned branch
            <select required value={branchId} onChange={(event) => {
              setBranchId(event.target.value);
              setPropertyId('');
            }}
              className="mt-1.5 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5">
              <option value="" disabled>Select branch</option>
              {branches.map((branch) => <option key={branch.id} value={branch.id}>{branch.name} · {branch.town}</option>)}
            </select>
          </label>
          <label className="block text-sm font-semibold">Property <span className="font-normal text-slate-500">optional</span>
            <select value={propertyId} onChange={(event) => setPropertyId(event.target.value)}
              className="mt-1.5 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5">
              <option value="">No property assignment</option>
              {branchProperties.map((property) => (
                <option key={property.id} value={property.id} disabled={property.landlord_id !== null}>
                  {property.name} · {property.branch_name}
                  {property.landlord_name ? ` · assigned to ${property.landlord_name}` : ''}
                </option>
              ))}
            </select>
            <span className="mt-1 block text-xs font-normal text-slate-500">
              Existing properties in this branch are listed; properties already assigned to another landlord cannot be selected.
            </span>
          </label>
          <label className="block text-sm font-semibold">Full name
            <input required value={name} onChange={(event) => setName(event.target.value)} autoComplete="name"
              className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-2.5" />
          </label>
          <label className="block text-sm font-semibold">Email
            <input required type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email"
              className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-2.5" />
          </label>
          <label className="block text-sm font-semibold">Phone
            <input required type="tel" value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="07XX XXX XXX"
              autoComplete="tel" className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-2.5" />
          </label>
          <label className="block text-sm font-semibold">ID or registration number
            <input required value={identity} onChange={(event) => setIdentity(event.target.value)}
              className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-2.5" />
          </label>
          <label className="block text-sm font-semibold">KRA PIN <span className="font-normal text-slate-500">optional</span>
            <input value={kraPin} onChange={(event) => setKraPin(event.target.value)}
              className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-2.5" />
          </label>
          <button type="submit" disabled={saving || !branches.length}
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-800 px-4 py-3 font-bold text-white hover:bg-emerald-900 disabled:opacity-50">
            <Mail size={17} /> {saving ? 'Creating account…' : 'Create account & send activation'}
          </button>
        </form>

        <section className="min-w-0 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-bold">Landlord accounts</h2>
              <p className="mt-1 text-xs text-slate-500">{accounts.length} records in your assigned branches</p>
            </div>
            <label className="relative min-w-48 flex-1 sm:max-w-64">
              <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input aria-label="Search landlords" value={search} onChange={(event) => setSearch(event.target.value)}
                placeholder="Search landlords" className="w-full rounded-xl border border-slate-300 py-2.5 pl-9 pr-3 text-sm" />
            </label>
          </div>
          <label className="mt-4 block max-w-xs text-xs font-semibold text-slate-600">Filter by branch
            <select value={filterBranchId} onChange={(event) => setFilterBranchId(event.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800">
              <option value="all">All assigned branches</option>
              {branches.map((branch) => <option key={branch.id} value={branch.id}>{branch.name}</option>)}
            </select>
          </label>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[690px] text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                <tr><th className="px-3 py-3 font-semibold">Landlord</th><th className="px-3 py-3 font-semibold">Branch</th><th className="px-3 py-3 font-semibold">Portal access</th><th className="px-3 py-3 text-right font-semibold">Action</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {visibleAccounts.map((account) => (
                  <tr key={account.id} className="align-top">
                    <td className="px-3 py-4">
                      <p className="font-semibold text-slate-900">{account.name}</p>
                      <p className="mt-1 text-xs text-slate-500">{account.email} · {account.phone}</p>
                    </td>
                    <td className="px-3 py-4 text-slate-600">{account.branch.name}</td>
                    <td className="px-3 py-4">
                      <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${account.account_status === 'active' ? 'bg-emerald-100 text-emerald-800' : account.account_status === 'pending_activation' ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-700'}`}>
                        {STATUS_LABELS[account.account_status]}
                      </span>
                    </td>
                    <td className="px-3 py-4 text-right">
                      {account.account_status !== 'active' && account.email && (
                        <button onClick={() => void sendActivation(account)} disabled={busyLandlordId === account.id}
                          className="rounded-lg border border-emerald-800 px-3 py-2 text-xs font-bold text-emerald-900 hover:bg-emerald-50 disabled:opacity-50">
                          {busyLandlordId === account.id ? 'Sending…' : account.account_status === 'not_invited' ? 'Invite' : 'Resend'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {!loading && visibleAccounts.length === 0 && (
                  <tr><td colSpan={4} className="px-3 py-10 text-center text-sm text-slate-500">No landlord records match this filter.</td></tr>
                )}
              </tbody>
            </table>
            {loading && <p className="py-6 text-center text-sm text-slate-500">Loading landlord accounts…</p>}
          </div>
        </section>
      </section>
    </main>
  );
}
