'use client';

import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { MapPin, Plus, RefreshCw } from 'lucide-react';
import { useDirectorPortal } from './DirectorPortalShell';

type Branch = {
  id: number;
  name: string;
  town: string;
};

type BranchPayload = {
  branches?: Branch[];
  branch?: Branch;
  csrfToken?: string;
  error?: string;
  detail?: string;
};

async function readPayload(response: Response, fallback: string): Promise<BranchPayload> {
  const payload = await response.json().catch(() => ({})) as BranchPayload;
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || fallback);
  }
  return payload;
}

export default function BranchManagement() {
  const { agency } = useDirectorPortal();
  const agencyId = agency?.id ?? null;
  const [branches, setBranches] = useState<Branch[]>([]);
  const [name, setName] = useState('');
  const [town, setTown] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const loadBranches = useCallback(async (selectedAgencyId: number) => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch(
        `/api/director/branches?agency_id=${encodeURIComponent(selectedAgencyId)}`,
        { cache: 'no-store' },
      );
      const payload = await readPayload(response, 'Unable to load agency branches.');
      if (!Array.isArray(payload.branches)) {
        throw new Error('The branch response was incomplete.');
      }
      setBranches(payload.branches);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load agency branches.');
      setBranches([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (agencyId === null) {
      setBranches([]);
      setLoading(false);
      return;
    }
    void loadBranches(agencyId);
  }, [agencyId, loadBranches]);

  async function csrfToken() {
    const response = await fetch('/api/auth/csrf', { cache: 'no-store' });
    const payload = await readPayload(response, 'Unable to prepare a secure request.');
    if (typeof payload.csrfToken !== 'string') {
      throw new Error('The server did not return a CSRF token.');
    }
    return payload.csrfToken;
  }

  async function createBranch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (agencyId === null) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const token = await csrfToken();
      const response = await fetch('/api/director/branches', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
        body: JSON.stringify({ agency_id: agencyId, name, town }),
      });
      const payload = await readPayload(response, 'Unable to create the branch.');
      if (!payload.branch) {
        throw new Error('The server did not return the created branch.');
      }
      setName('');
      setTown('');
      setNotice(`${payload.branch.name} was added and is available for staff branch assignments.`);
      await loadBranches(agencyId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to create the branch.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-[1440px] space-y-6 px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-wrap items-end justify-between gap-4 pb-1">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#728779]">
            Director workspace
          </p>
          <h1 className="mt-1.5 text-2xl font-bold tracking-tight text-[#1d3528] sm:text-[28px]">
            Agency branches
          </h1>
          <p className="mt-1.5 max-w-3xl text-sm leading-relaxed text-[#748178]">
            Manage the branch locations for {agency?.name || 'this agency'}. New branches are available for staff assignments.
          </p>
        </div>
        <button
          type="button"
          onClick={() => agencyId !== null && void loadBranches(agencyId)}
          disabled={loading || agencyId === null}
          className="inline-flex items-center gap-2 rounded-xl border border-[#dfe5df] bg-white px-3.5 py-2.5 text-xs font-semibold text-[#385343] transition hover:bg-[#f5f8f3] focus:outline-none focus:ring-4 focus:ring-[#eaf1e4] disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RefreshCw size={14} strokeWidth={1.9} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
          Refresh branches
        </button>
      </header>

      {error && (
        <p role="alert" className="rounded-xl border border-[#f0d9d4] bg-[#fcf2ef] px-4 py-3 text-sm text-[#9b5147]">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="rounded-xl border border-[#dce9d5] bg-[#f1f6ed] px-4 py-3 text-sm text-[#456b39]">
          {notice}
        </p>
      )}

      <section className="rounded-2xl border border-[#e7ebe5] bg-white p-5 shadow-[0_8px_24px_rgba(27,48,34,0.035)] sm:p-6">
        <div className="mb-5 flex items-start gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[#eef5e9] text-[#62844e]">
            <Plus size={18} strokeWidth={1.9} aria-hidden="true" />
          </span>
          <div>
            <h2 className="text-base font-bold tracking-tight text-[#243b2c]">Add a branch</h2>
            <p className="mt-1 text-xs leading-relaxed text-[#89948c]">
              Enter a branch name and the town where it operates.
            </p>
          </div>
        </div>
        <form onSubmit={createBranch} className="grid gap-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <label className="text-[11px] font-bold uppercase tracking-[0.12em] text-[#68776d]">
            Branch name
            <input
              required
              maxLength={100}
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="mt-1.5 block w-full rounded-xl border border-[#dfe5df] bg-white px-3 py-2.5 text-sm font-normal normal-case tracking-normal text-[#243b2c] outline-none transition placeholder:text-[#9aa59d] focus:border-[#89a66b] focus:ring-4 focus:ring-[#eaf1e4]"
            />
          </label>
          <label className="text-[11px] font-bold uppercase tracking-[0.12em] text-[#68776d]">
            Town
            <input
              required
              maxLength={100}
              value={town}
              onChange={(event) => setTown(event.target.value)}
              className="mt-1.5 block w-full rounded-xl border border-[#dfe5df] bg-white px-3 py-2.5 text-sm font-normal normal-case tracking-normal text-[#243b2c] outline-none transition placeholder:text-[#9aa59d] focus:border-[#89a66b] focus:ring-4 focus:ring-[#eaf1e4]"
            />
          </label>
          <button
            type="submit"
            disabled={busy || agencyId === null}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-[#163a2e] px-4 py-2.5 text-sm font-bold text-white shadow-[0_5px_14px_rgba(22,58,46,0.14)] transition hover:bg-[#234630] focus:outline-none focus:ring-4 focus:ring-[#eaf1e4] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Plus size={16} strokeWidth={2} aria-hidden="true" />
            {busy ? 'Adding...' : 'Add branch'}
          </button>
        </form>
      </section>

      <section className="overflow-hidden rounded-2xl border border-[#e7ebe5] bg-white shadow-[0_8px_24px_rgba(27,48,34,0.035)]">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#edf0eb] px-5 py-5 sm:px-6">
          <div>
            <h2 className="text-base font-bold tracking-tight text-[#243b2c]">Branch directory</h2>
            <p className="mt-1 text-xs text-[#89948c]">Branches available for this agency and its staff.</p>
          </div>
          <span className="inline-flex items-center gap-1.5 rounded-full bg-[#f3f6f1] px-3 py-1.5 text-[11px] font-semibold text-[#617565]">
            <MapPin size={13} strokeWidth={1.9} aria-hidden="true" />
            {branches.length} {branches.length === 1 ? 'branch' : 'branches'}
          </span>
        </div>
        {loading ? (
          <p role="status" className="px-5 py-8 text-center text-sm text-[#89948c]">Loading agency branches...</p>
        ) : branches.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[420px] text-left text-sm">
              <thead className="bg-[#f8faf7] text-[10px] font-bold uppercase tracking-[0.12em] text-[#87938a]">
                <tr>
                  <th className="px-5 py-3.5">Branch</th>
                  <th className="px-5 py-3.5">Town</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#f0f2ef]">
                {branches.map((branch) => (
                  <tr key={branch.id} className="transition-colors hover:bg-[#fbfcfa]">
                    <td className="px-5 py-4 font-semibold text-[#2b4333]">{branch.name}</td>
                    <td className="px-5 py-4 text-[#617067]">{branch.town}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="flex flex-col items-center px-5 py-10 text-center">
            <span className="grid h-11 w-11 place-items-center rounded-2xl bg-[#f3f6f1] text-[#78916d]">
              <MapPin size={19} strokeWidth={1.8} aria-hidden="true" />
            </span>
            <p className="mt-3 text-sm font-semibold text-[#52665a]">No branches yet</p>
            <p className="mt-1 text-xs text-[#89948c]">Add your first branch using the form above.</p>
          </div>
        )}
      </section>
    </main>
  );
}
