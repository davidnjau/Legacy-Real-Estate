'use client';

import Link from 'next/link';
import { useEffect, useState, type FormEvent } from 'react';
import { Building2 } from 'lucide-react';
import ActivationPasswordFields, {
  isActivationPasswordStrong,
} from '../../components/auth/ActivationPasswordFields';

type Invitation = {
  email: string;
  full_name: string;
  phone: string;
  property_name: string;
  property_names?: string[];
};

export default function ActivateCaretakerPage() {
  const [token, setToken] = useState('');
  const [invitation, setInvitation] = useState<Invitation | null>(null);
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const propertyDescription = invitation?.property_names?.length
    ? invitation.property_names.join(', ')
    : invitation?.property_name;

  useEffect(() => {
    const invitationToken = new URLSearchParams(window.location.search).get('token') || '';
    setToken(invitationToken);
    if (!invitationToken) {
      setError('This activation link is missing its invitation token.');
      return;
    }

    void fetch(`/api/auth/caretaker-invitation/context?token=${encodeURIComponent(invitationToken)}`)
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || payload.error || 'This invitation is invalid or expired.');
        const verifiedInvitation = payload as Invitation;
        setInvitation(verifiedInvitation);
        setName(verifiedInvitation.full_name || '');
        setPhone(verifiedInvitation.phone || '');
      })
      .catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : 'Unable to verify this invitation.');
      });
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    if (password !== confirmation) {
      setError('The passwords do not match.');
      return;
    }
    if (!isActivationPasswordStrong(password)) {
      setError('Choose a password that meets all the strength requirements.');
      return;
    }

    setLoading(true);
    try {
      const response = await fetch('/api/auth/caretaker-invitation/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, code, name, phone, password }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || payload.error || 'Unable to activate this account.');
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
        <h1 className="text-2xl font-bold">Activate your caretaker account</h1>
        <p className="mt-2 text-sm text-slate-600">
          {invitation
            ? `You are invited to manage ${propertyDescription} as ${invitation.email}.`
            : 'Verify your invitation to create your caretaker portal login.'}
        </p>
        {success ? (
          <div role="status" className="mt-6 rounded-xl border border-emerald-200 bg-emerald-50 p-5 text-center">
            <p className="font-semibold text-emerald-900">Your caretaker account is activated.</p>
            <p className="mt-1 text-sm text-emerald-800">Sign in when you’re ready.</p>
            <Link href="/login?activated=1" className="mt-4 inline-flex rounded-xl bg-emerald-800 px-5 py-3 font-semibold text-white">
              Continue to sign in
            </Link>
          </div>
        ) : (
          <>
            {error && <p role="alert" className="mt-5 rounded-xl bg-red-50 p-3 text-sm text-red-800">{error}</p>}
            {invitation ? (
              <form onSubmit={submit} className="mt-6 space-y-4">
            {invitation.full_name && invitation.phone ? (
              <div className="rounded-xl bg-slate-50 p-4 text-sm">
                <p><span className="font-semibold">Name:</span> {invitation.full_name}</p>
                <p className="mt-1"><span className="font-semibold">Phone:</span> {invitation.phone}</p>
              </div>
            ) : (
              <>
                <label className="block text-sm font-semibold">
                  Full name
                  <input required value={name} onChange={(event) => setName(event.target.value)} autoComplete="name"
                    className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-3" />
                </label>
                <label className="block text-sm font-semibold">
                  Phone
                  <input required type="tel" value={phone} onChange={(event) => setPhone(event.target.value)}
                    placeholder="07XX XXX XXX" autoComplete="tel"
                    className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-3" />
                </label>
              </>
            )}
            <label className="block text-sm font-semibold">
              Six-digit email code
              <input required inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={code}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))}
                autoComplete="one-time-code"
                className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-3 font-mono tracking-[0.3em]" />
            </label>
            <ActivationPasswordFields
              password={password}
              confirmation={confirmation}
              onPasswordChange={setPassword}
              onConfirmationChange={setConfirmation}
            />
            <button type="submit"
              disabled={loading || code.length !== 6 || !isActivationPasswordStrong(password) || password !== confirmation}
              className="w-full rounded-xl bg-emerald-800 py-3.5 font-bold text-white disabled:opacity-50">
              {loading ? 'Activating…' : 'Create password'}
            </button>
              </form>
            ) : !error ? (
              <p className="mt-6 text-sm text-slate-600">Verifying invitation…</p>
            ) : null}
            <p className="mt-5 text-center text-sm text-slate-600">
              Already activated? <Link href="/login" className="font-semibold text-emerald-800 underline">Sign in</Link>
            </p>
          </>
        )}
      </section>
    </main>
  );
}
