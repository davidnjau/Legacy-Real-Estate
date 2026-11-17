'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { LogOut } from 'lucide-react';

export default function SignOutButton() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function signOut() {
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || 'Unable to sign out. Please try again.');
      }
      router.replace('/login');
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to sign out. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={() => void signOut()}
        disabled={loading}
        className="inline-flex items-center gap-2 rounded-xl border border-[#dfe5df] bg-white px-3 py-2.5 text-xs font-semibold text-[#385343] transition hover:border-[#cbd8c8] hover:bg-[#f5f8f3] disabled:cursor-wait disabled:opacity-60"
      >
        <LogOut size={14} /> {loading ? 'Signing out…' : 'Sign out'}
      </button>
      {error && <span role="alert" className="max-w-56 text-xs text-red-700">{error}</span>}
    </div>
  );
}
