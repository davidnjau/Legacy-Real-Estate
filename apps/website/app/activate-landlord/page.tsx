'use client';

import Link from 'next/link';
import { useEffect, useState, type FormEvent } from 'react';
import { Building2 } from 'lucide-react';

export default function ActivateLandlordPage() {
  const [challengeId, setChallengeId] = useState('');
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [identity, setIdentity] = useState('');
  const [existingAccount, setExistingAccount] = useState(false);
  const [contextLoading, setContextLoading] = useState(true);
  const [contextError, setContextError] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const searchParams = new URLSearchParams(window.location.search);
    const id = searchParams.get('challenge_id') || '';
    setChallengeId(id);
    setPhone(searchParams.get('phone') || '');
    setCode(searchParams.get('code') || '');
    if (!id) {
      setContextLoading(false);
      return;
    }
    const controller = new AbortController();
    let active = true;
    void fetch(`/api/auth/landlord/activate/context?challenge_id=${encodeURIComponent(id)}`, {
      cache: 'no-store',
      signal: controller.signal,
    })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || payload.error || 'This activation link is invalid or expired.');
        if (active) setExistingAccount(payload.existing_account === true);
      })
      .catch((cause: unknown) => {
        if (active && !(cause instanceof DOMException && cause.name === 'AbortError')) {
          setChallengeId('');
          setExistingAccount(false);
          setContextError(
            'This activation link cannot be verified. Enter the phone number and latest code sent by SMS, or ask your Property Officer to resend the invitation.',
          );
        }
      })
      .finally(() => {
        if (active) setContextLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    setLoading(true);
    try {
      const csrfResponse = await fetch('/api/auth/csrf', { cache: 'no-store' });
      const csrfPayload = await csrfResponse.json();
      if (!csrfResponse.ok || !csrfPayload.csrfToken) {
        throw new Error('Unable to prepare a secure activation. Please try again.');
      }
      const response = await fetch('/api/auth/landlord/activate', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfPayload.csrfToken,
        },
        body: JSON.stringify({
          challenge_id: challengeId || undefined,
          phone: challengeId ? undefined : phone,
          code,
          id_or_registration_number: existingAccount ? identity : undefined,
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || 'Unable to activate this account.');
      }
      if (payload.identity_confirmation_required === true) {
        setExistingAccount(true);
        setIdentity('');
        return;
      }
      window.location.assign('/landlord/dashboard');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to activate this account.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#f4f7f5] px-4 py-10 text-slate-900">
      <section className="w-full max-w-md rounded-3xl border border-slate-200 bg-white p-7 shadow-sm sm:p-9">
        <Link href="/" className="mb-6 inline-flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-emerald-700 text-white">
            <Building2 size={20} />
          </span>
          <span className="font-bold">Legacy Real Estate</span>
        </Link>
        <h1 className="text-2xl font-bold">Activate your landlord account</h1>
        <p className="mt-2 text-sm text-slate-600">
          {challengeId
            ? existingAccount
              ? 'Enter the one-time code and confirm the ID or registration number already recorded for your landlord account.'
              : 'Enter the one-time code sent with your activation invitation. The code expires after 48 hours.'
            : 'Enter the phone number that received the activation SMS and the code already sent to it. Do not request a new code.'}
        </p>
        {contextLoading && (
          <p role="status" className="mt-6 rounded-xl bg-slate-50 p-4 text-sm text-slate-600">
            Checking your activation link. You can enter your code while we verify it…
          </p>
        )}
        {contextError && <p role="alert" className="mt-5 rounded-xl bg-amber-50 p-3 text-sm text-amber-900">{contextError}</p>}
        {error && <p role="alert" className="mt-5 rounded-xl bg-red-50 p-3 text-sm text-red-800">{error}</p>}
        <form onSubmit={submit} className="mt-6 space-y-4">
          {!challengeId && (
            <label className="block text-sm font-semibold">
              Phone number used during onboarding
              <input
                required
                type="tel"
                autoComplete="tel"
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
                className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-3"
                placeholder="+254 7XX XXX XXX"
              />
            </label>
          )}
          <label className="block text-sm font-semibold">
            Six-digit activation code
            <input
              required
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-3 font-mono tracking-widest"
            />
          </label>
          {existingAccount && (
            <label className="block text-sm font-semibold">
              Confirm ID or registration number
              <input
                required
                autoComplete="off"
                value={identity}
                onChange={(event) => setIdentity(event.target.value)}
                className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-3"
              />
            </label>
          )}
          <button
            type="submit"
            disabled={
              loading
              || contextLoading
              || (!challengeId && !phone.trim())
              || (existingAccount && !identity.trim())
            }
            className="w-full rounded-xl bg-emerald-800 py-3.5 font-bold text-white disabled:opacity-50"
          >
            {loading ? 'Activating…' : existingAccount ? 'Confirm identity and continue' : 'Activate account'}
          </button>
        </form>
        {challengeId && (
          <p className="mt-4 text-center text-sm text-slate-600">
            Using your SMS instead?{' '}
            <Link href="/activate-landlord" className="font-semibold text-emerald-800 underline">
              Enter your phone number
            </Link>
          </p>
        )}
        <p className="mt-5 text-center text-sm text-slate-600">
          Already activated? <Link href="/login" className="font-semibold text-emerald-800 underline">Sign in</Link>
        </p>
      </section>
    </main>
  );
}
