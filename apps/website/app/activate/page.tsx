'use client';

import { useEffect, useState } from 'react';
import { AlertTriangle, KeyRound } from 'lucide-react';

type TenantActivation = {
  full_name: string;
  unit_code: string;
  identity_required: boolean;
  identity_confirmation_required: boolean;
  existing_account: boolean;
};

export default function TenantActivationPage() {
  const [phone, setPhone] = useState('');
  const [unitCode, setUnitCode] = useState('');
  const [activationCode, setActivationCode] = useState('');
  const [activation, setActivation] = useState<TenantActivation | null>(null);
  const [nationalIdOrPassport, setNationalIdOrPassport] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [activating, setActivating] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      const phoneParam = params.get('phone');
      const unitParam = params.get('unit') || params.get('unit_code');
      const codeParam = params.get('code');
      if (phoneParam) setPhone(phoneParam);
      if (unitParam) setUnitCode(unitParam);
      if (codeParam) setActivationCode(codeParam);
    }
  }, []);

  const handleVerify = async (event: React.FormEvent) => {
    event.preventDefault();
    setVerifying(true);
    setError('');
    try {
      const response = await fetch('/api/auth/activate/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone, unitCode, code: activationCode }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to verify this activation code.');
      setActivation({
        full_name: data.full_name,
        unit_code: data.unit_code,
        identity_required: data.identity_required === true,
        identity_confirmation_required: data.identity_confirmation_required === true,
        existing_account: data.existing_account === true,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to verify this activation code.');
    } finally {
      setVerifying(false);
    }
  };

  const handleActivate = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!activation) return;
    setActivating(true);
    setError('');
    try {
      const response = await fetch('/api/auth/activate/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone,
          unitCode,
          code: activationCode,
          nationalIdOrPassport: activation.identity_required || activation.identity_confirmation_required
            ? nationalIdOrPassport
            : undefined,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to activate the tenant account.');
      window.location.assign('/tenant/dashboard');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to activate the tenant account.');
    } finally {
      setActivating(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col justify-center bg-slate-50 py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <h1 className="mt-6 text-center text-3xl font-extrabold text-slate-900">Activate Your Account</h1>
        <p className="mt-2 text-center text-sm text-slate-600">
          Enter the phone number that received the activation SMS and the code already sent to it. After verification, you will be taken to your tenant portal.
        </p>
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
        <div className="border border-slate-100 bg-white px-4 py-8 shadow-xl sm:rounded-2xl sm:px-10">
          {!activation ? (
            <form onSubmit={handleVerify} className="space-y-6">
              {error && <div className="flex gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"><AlertTriangle size={18} />{error}</div>}
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">Phone number</label>
                <input required type="tel" value={phone} onChange={(event) => setPhone(event.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3" placeholder="+254 7XX XXX XXX" autoComplete="tel" />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">Room / Unit Number</label>
                <input required value={unitCode} onChange={(event) => setUnitCode(event.target.value)} className="w-full rounded-xl border border-slate-300 px-4 py-3" placeholder="e.g. A1" />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">Six-digit activation code</label>
                <div className="relative">
                  <KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
                  <input required inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={activationCode} onChange={(event) => setActivationCode(event.target.value.replace(/\D/g, '').slice(0, 6))} className="w-full rounded-xl border border-slate-300 py-3 pl-10 pr-4 font-mono tracking-widest" autoComplete="one-time-code" />
                </div>
              </div>
              <button type="submit" disabled={verifying || activationCode.length !== 6} className="w-full rounded-xl bg-slate-900 px-4 py-3 font-bold text-white disabled:opacity-70">
                {verifying ? 'Verifying...' : 'Verify Code'}
              </button>
            </form>
          ) : (
            <form onSubmit={handleActivate} className="space-y-5">
              <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-5 text-center">
                <h2 className="mb-1 text-xl font-bold text-emerald-900">Hi {activation.full_name.split(' ')[0] || 'Tenant'}!</h2>
                <p className="text-sm text-emerald-800">
                  Your unit is <strong>{activation.unit_code}</strong>.{' '}
                  {activation.existing_account
                    ? 'Confirm your identity to link this tenancy to your existing account.'
                    : 'Confirm your identity to activate your tenant account.'}
                </p>
              </div>
              {error && <div className="flex gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"><AlertTriangle size={18} />{error}</div>}
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
              <button type="submit" disabled={activating || ((activation.identity_required || activation.identity_confirmation_required) && !nationalIdOrPassport.trim())} className="w-full rounded-xl bg-emerald-600 px-4 py-3 font-bold text-white disabled:opacity-70">
                {activating ? 'Confirming...' : activation.existing_account ? 'Confirm identity and link account' : 'Activate Account'}
              </button>
              <button type="button" onClick={() => setActivation(null)} className="w-full rounded-xl border border-slate-200 px-4 py-3 font-semibold text-slate-700">
                Use different details
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
