'use client';

import { useState } from 'react';
import { Search, X } from 'lucide-react';

export type ExistingAccount = {
  id: number;
  name: string;
  email: string;
  phone: string;
};

type Props = {
  agencyId: number;
  selectedId: number | null;
  onSelect: (account: ExistingAccount | null) => void;
};

export default function ExistingAccountPicker({
  agencyId,
  selectedId,
  onSelect,
}: Props) {
  const [query, setQuery] = useState('');
  const [accounts, setAccounts] = useState<ExistingAccount[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function search() {
    setError('');
    setLoading(true);
    try {
      const params = new URLSearchParams({
        agency_id: String(agencyId),
        q: query.trim(),
      });
      const response = await fetch(`/api/property-officer/accounts/search?${params}`, {
        cache: 'no-store',
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || 'Unable to search agency accounts.');
      }
      setAccounts(payload.accounts ?? []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to search agency accounts.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="space-y-2 rounded-xl border border-slate-200 bg-slate-50 p-3">
      <div>
        <p className="text-sm font-semibold">Link an existing agency account</p>
        <p className="mt-1 text-xs text-slate-600">
          Search by name, email, phone, or ID. Account identity details are not displayed.
        </p>
      </div>
      <div role="search" className="flex gap-2">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              if (query.trim().length >= 2 && !loading) void search();
            }
          }}
          minLength={2}
          placeholder="Search this agency"
          aria-label="Search existing agency accounts"
          className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        />
        <button
          type="button"
          onClick={() => void search()}
          disabled={loading || query.trim().length < 2}
          className="inline-flex items-center gap-1 rounded-lg bg-slate-800 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
        >
          <Search size={14} /> {loading ? 'Searching' : 'Search'}
        </button>
      </div>
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
