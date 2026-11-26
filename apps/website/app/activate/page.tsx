'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { AlertTriangle, CheckCircle, KeyRound } from 'lucide-react';
import ActivationPasswordFields, { isActivationPasswordStrong } from '../../components/auth/ActivationPasswordFields';

type TenantActivation = {
  full_name: string;
  email: string;
  unit_code: string;
  identity_required: boolean;
  identity_confirmation_required: boolean;
  existing_account: boolean;
  phone_verification_required: boolean;
};

export default function TenantActivationPage() {
  const [unitCode, setUnitCode] = useState('');
  const [activationCode, setActivationCode] = useState('');
  const [activation, setActivation] = useState<TenantActivation | null>(null);
  const [linkToken, setLinkToken] = useState('');
  const [fullName, setFullName] = useState('');
  const [nationalIdOrPassport, setNationalIdOrPassport] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [activating, setActivating] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get('token') || '';
    if (!token) return;
    setLinkToken(token);
    void fetch(`/api/auth/activate/context?token=${encodeURIComponent(token)}`)
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'This activation link is invalid or expired.');
        const context = {
          full_name: data.full_name,
          email: data.email,
          unit_code: data.unit_code,
          identity_required: data.identity_required === true,
          identity_confirmation_required: data.identity_confirmation_required === true,
          existing_account: data.existing_account === true,
          phone_verification_required: data.phone_verification_required === true,
        };
        setActivation(context);
        setFullName(context.full_name);
      })
      .catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : 'Unable to verify this activation link.');
      });
  }, []);

  const handleVerify = async (event: React.FormEvent) => {
    event.preventDefault();
    setVerifying(true);
    setError('');
    try {
      const response = await fetch('/api/auth/activate/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ unitCode, code: activationCode }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to verify this activation code.');
      setActivation({
        full_name: data.full_name,
        email: data.email,
        unit_code: data.unit_code,
        identity_required: data.identity_required === true,
        identity_confirmation_required: data.identity_confirmation_required === true,
        existing_account: data.existing_account === true,
        phone_verification_required: data.phone_verification_required === true,
      });
      setFullName(data.full_name);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to verify this activation code.');
    } finally {
      setVerifying(false);
    }
  };

  const handleActivate = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!activation) return;
    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    if (!activation?.existing_account && !isActivationPasswordStrong(password)) {
      setError('Choose a password that meets all the strength requirements.');
      return;
    }
    setActivating(true);
    setError('');
    try {
      const response = await fetch('/api/auth/activate/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token: linkToken || undefined,
          code: activationCode,
          unitCode: linkToken ? undefined : unitCode,
          fullName,
          nationalIdOrPassport: activation.identity_required || activation.identity_confirmation_required
            ? nationalIdOrPassport
            : undefined,
          password: activation.existing_account ? undefined : password,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to activate the tenant account.');
      setSuccess(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to activate the tenant account.');
    } finally {
      setActivating(false);
    }
  };

  if (success) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
        <div className="w-full max-w-md rounded-2xl border border-slate-100 bg-white p-10 text-center shadow-xl">
          <CheckCircle className="mx-auto mb-6 text-emerald-600" size={42} />
          <h2 className="mb-2 text-2xl font-bold text-slate-900">Account Activated!</h2>
          <p className="text-slate-500">Your account is ready. Sign in when you’re ready.</p>
          <Link href="/login?activated=1" className="mt-6 inline-flex rounded-xl bg-emerald-700 px-5 py-3 font-semibold text-white">
            Continue to sign in
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col justify-center bg-slate-50 py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <h1 className="mt-6 text-center text-3xl font-extrabold text-slate-900">Activate Your Account</h1>
        <p className="mt-2 text-center text-sm text-slate-600">
          {activation?.phone_verification_required
            ? 'Use the secure activation link and six-digit code sent by SMS to verify your phone.'
            : 'Use the secure activation link and six-digit code sent to your email.'}
        </p>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
        <div className="border border-slate-100 bg-white px-4 py-8 shadow-xl sm:rounded-2xl sm:px-10">
          {!activation && linkToken ? (
            <p className="text-center text-sm text-slate-600">{error || 'Verifying your secure activation link...'}</p>
          ) : !activation ? (
            <form onSubmit={handleVerify} className="space-y-6">
              {error && <div className="flex gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"><AlertTriangle size={18} />{error}</div>}
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">Room / Unit Number</label>
                <input required value={unitCode} onChange={(event) => setUnitCode(event.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3" placeholder="e.g. A1" />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">Six-digit activation code</label>
                <div className="relative">
                  <KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
                  <input required value={activationCode} onChange={(event) => setActivationCode(event.target.value.toUpperCase())} className="w-full rounded-xl border border-slate-300 py-3 pl-10 pr-4 font-mono uppercase tracking-widest" />
                </div>
              </div>
              <button type="submit" disabled={verifying} className="w-full rounded-xl bg-slate-900 px-4 py-3 font-bold text-white disabled:opacity-70">
                {verifying ? 'Verifying...' : 'Verify Code'}
              </button>
            </form>
          ) : (
            <form onSubmit={handleActivate} className="space-y-5">
              <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-5 text-center">
                <h2 className="mb-1 text-xl font-bold text-emerald-900">Hi {fullName.split(' ')[0] || 'Tenant'}!</h2>
                <p className="text-sm text-emerald-800">
                  {activation.unit_code && <>Your unit is <strong>{activation.unit_code}</strong>. </>}
                  {activation.existing_account
                    ? <>Confirm your identity to add this tenancy to <strong>your existing account</strong>.</>
                    : <>Create a password for <strong>{activation.email || 'your tenant account'}</strong>.</>}
                </p>
              </div>
              {error && <div className="flex gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"><AlertTriangle size={18} />{error}</div>}
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">Full Name</label>
                <input required value={fullName} onChange={(event) => setFullName(event.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3" autoComplete="name" />
              </div>
              {(activation.identity_required || activation.identity_confirmation_required) && (
                <div>
                  <label className="mb-1 block text-sm font-medium text-slate-700">
                    {activation.identity_confirmation_required
                      ? 'Confirm the National ID or Passport Number provided during onboarding'
                      : 'National ID or Passport Number'}
                  </label>
                  <input required value={nationalIdOrPassport} onChange={(event) => setNationalIdOrPassport(event.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3" autoComplete="off" />
                </div>
              )}
              {linkToken && (
                <div>
                  <label className="mb-1 block text-sm font-medium text-slate-700">
                    {activation.phone_verification_required ? 'Six-digit SMS verification code' : 'Six-digit email code'}
                  </label>
                  <input required inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={activationCode}
                    onChange={(event) => setActivationCode(event.target.value.replace(/\D/g, '').slice(0, 6))}
                    autoComplete="one-time-code"
                    className="w-full rounded-xl border border-slate-300 px-4 py-3 font-mono tracking-[0.3em]" />
                </div>
              )}
              {!activation.existing_account && (
                <ActivationPasswordFields
                  password={password}
                  confirmation={confirmPassword}
                  onPasswordChange={setPassword}
                  onConfirmationChange={setConfirmPassword}
                />
              )}
              <button type="submit" disabled={activating || (linkToken && activationCode.length !== 6) || (!activation.existing_account && (!isActivationPasswordStrong(password) || password !== confirmPassword))} className="w-full rounded-xl bg-emerald-600 px-4 py-3 font-bold text-white disabled:opacity-70">
                {activating ? 'Confirming...' : activation.existing_account ? 'Confirm identity and link account' : 'Activate Account'}
              </button>
            </form>
          )}
          {error && linkToken && !activation && <Link href="/login" className="mt-4 block text-center font-semibold text-emerald-600">Return to sign in</Link>}
        </div>
      </div>
    </div>
  );
}
