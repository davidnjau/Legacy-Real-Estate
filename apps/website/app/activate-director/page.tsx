'use client';

import Link from 'next/link';
import { useEffect, useState, type FormEvent } from 'react';
import { Building2 } from 'lucide-react';
import ActivationPasswordFields, { isActivationPasswordStrong } from '../../components/auth/ActivationPasswordFields';

export default function ActivateDirectorPage() {
  const [challengeId, setChallengeId] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const passwordIsStrong = isActivationPasswordStrong(password);

  useEffect(() => {
    setChallengeId(new URLSearchParams(window.location.search).get('challenge_id') || '');
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    if (password !== confirmation) {
      setError('The passwords do not match.');
      return;
    }
    if (!passwordIsStrong) {
      setError('Choose a password that meets all the strength requirements.');
      return;
    }
    setLoading(true);
    try {
      const csrfResponse = await fetch('/api/auth/csrf', { cache: 'no-store' });
      const csrfPayload = await csrfResponse.json();
      if (!csrfResponse.ok || !csrfPayload.csrfToken) {
        throw new Error('Unable to prepare a secure activation. Please try again.');
      }
      const response = await fetch('/api/auth/director/activate', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfPayload.csrfToken,
        },
        body: JSON.stringify({ challenge_id: challengeId, code, password }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || 'Unable to activate this account.');
      }
      setSuccess(true);
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
        <h1 className="text-2xl font-bold">Activate your Director account</h1>
        <p className="mt-2 text-sm text-slate-600">
          Enter the one-time code sent to your verified email and create a password.
        </p>
        {success ? (
          <div role="status" className="mt-6 rounded-xl border border-emerald-200 bg-emerald-50 p-5 text-center">
            <p className="font-semibold text-emerald-900">Your Director account is activated.</p>
            <p className="mt-1 text-sm text-emerald-800">Sign in when you’re ready.</p>
            <Link href="/login?activated=1" className="mt-4 inline-flex rounded-xl bg-emerald-800 px-5 py-3 font-semibold text-white">
              Continue to sign in
            </Link>
          </div>
        ) : (
          <>
            {error && <p role="alert" className="mt-5 rounded-xl bg-red-50 p-3 text-sm text-red-800">{error}</p>}
            <form onSubmit={submit} className="mt-6 space-y-4">
          <label className="block text-sm font-semibold">
            Email activation code
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
          <ActivationPasswordFields
            password={password}
            confirmation={confirmation}
            onPasswordChange={setPassword}
            onConfirmationChange={setConfirmation}
          />
          <button
            type="submit"
            disabled={loading || !challengeId || !passwordIsStrong || password !== confirmation}
            className="w-full rounded-xl bg-emerald-800 py-3.5 font-bold text-white disabled:opacity-50"
          >
            {loading ? 'Activating…' : 'Activate account'}
          </button>
            </form>
            <p className="mt-5 text-center text-sm text-slate-600">
              Already activated? <Link href="/login" className="font-semibold text-emerald-800 underline">Sign in</Link>
            </p>
          </>
        )}
      </section>
    </main>
  );
}
