'use client';

import { useEffect, useState } from 'react';
import { Search, X } from 'lucide-react';

export type ExistingAccount = {
  id: number;
  name: string;
  email: string;
  phone: string;
  id_or_registration_number?: string;
};

type Props = {
  agencyId: number;
  selectedId: number | null;
  excludeRole?: string;
  onSelect: (account: ExistingAccount | null) => void;
};

export default function ExistingAccountPicker({
  agencyId,
  selectedId,
  excludeRole,
  onSelect,
}: Props) {
  const [query, setQuery] = useState('');
  const [accounts, setAccounts] = useState<ExistingAccount[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const searchQuery = query.trim();
    if (searchQuery.length < 2) {
      setAccounts([]);
      setError('');
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    setAccounts([]);
    setError('');
    setLoading(true);
    const timeout = window.setTimeout(() => {
      const searchParamsObj: Record<string, string> = {
        agency_id: String(agencyId),
        q: searchQuery,
      };
      if (excludeRole) {
        searchParamsObj.exclude_role = excludeRole;
      }
      const params = new URLSearchParams(searchParamsObj);
      void fetch(`/api/property-officer/accounts/search?${params}`, {
        cache: 'no-store',
        signal: controller.signal,
      })
        .then(async (response) => {
          const payload = await response.json();
          if (!response.ok) {
            throw new Error(payload.detail || payload.error || 'Unable to search agency accounts.');
          }
          if (!controller.signal.aborted) setAccounts(payload.accounts ?? []);
        })
        .catch((cause: unknown) => {
          if (!controller.signal.aborted) {
            setError(cause instanceof Error ? cause.message : 'Unable to search agency accounts.');
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 250);

    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [agencyId, excludeRole, query]);

  return (
    <section className="space-y-2 rounded-xl border border-slate-200 bg-slate-50 p-3">
      <div>
        <p className="text-sm font-semibold">Link an existing agency account</p>
        <p className="mt-1 text-xs text-slate-600">
          Search by name, email, phone, or ID. Account identity details are not displayed.
        </p>
      </div>
      <div role="search" className="relative">
        <Search aria-hidden="true" size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') event.preventDefault();
          }}
          minLength={2}
          placeholder="Type a name, email, phone, or ID"
          aria-label="Search existing agency accounts"
          className="w-full rounded-lg border border-slate-300 bg-white py-2 pl-9 pr-3 text-sm"
        />
      </div>
      {query.trim().length < 2 && (
        <p className="text-xs text-slate-500">Type at least two characters to see matching accounts.</p>
      )}
      {loading && <p role="status" className="text-xs text-slate-500">Searching agency accounts…</p>}
      {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
      {accounts.length > 0 && (
        <ul className="max-h-44 space-y-1 overflow-y-auto">
          {accounts.map((account) => (
            <li key={account.id}>
              <button
                type="button"
                aria-pressed={selectedId === account.id}
                onClick={() => onSelect(account)}
                className={`w-full rounded-lg border px-3 py-2 text-left text-xs ${
                  selectedId === account.id
                    ? 'border-emerald-700 bg-emerald-50'
                    : 'border-slate-200 bg-white hover:bg-slate-100'
                }`}
              >
                <span className="block font-semibold">{account.name}</span>
                <span className="mt-0.5 block text-slate-600">{account.email} · {account.phone}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {accounts.length === 0 && query.trim().length >= 2 && !loading && !error && (
        <p className="text-xs text-slate-500">No matching active accounts in this agency.</p>
      )}
      {selectedId !== null && (
        <button
          type="button"
          onClick={() => onSelect(null)}
          className="inline-flex items-center gap-1 text-xs font-semibold text-slate-600 hover:text-slate-900"
        >
          <X size={13} /> Clear account selection
        </button>
      )}
    </section>
  );
}
