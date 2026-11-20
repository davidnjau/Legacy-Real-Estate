'use client';

import { useState, type FormEvent } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { QRCodeSVG } from 'qrcode.react';
import {
  ArrowRight,
  Building2,
  Eye,
  EyeOff,
  KeyRound,
  ShieldCheck,
  Smartphone,
} from 'lucide-react';
import { getDjangoHomePath } from '../../lib/django-routing';

type AuthResponse = {
  detail?: string;
  error?: string;
  user?: { roles?: string[] };
  mfa_required?: boolean;
  mfa_method?: 'sms' | 'totp';
  challenge_id?: string | number;
  setup_required?: boolean;
  secret?: string;
  provisioning_uri?: string;
  recovery_codes?: string[];
};

function requireJsonResponse(response: Response) {
  const contentType = response.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase();
  if (contentType !== 'application/json' && !contentType?.endsWith('+json')) {
    throw new Error(
      `The sign-in service returned an unexpected response (HTTP ${response.status}). Please try again or contact support.`,
    );
  }
}

export default function LoginPage() {
  const router = useRouter();
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [challengeId, setChallengeId] = useState<string | number | null>(null);
  const [verificationCode, setVerificationCode] = useState('');
  const [totpSetup, setTotpSetup] = useState<{ secret: string; uri: string } | null>(null);
  const [mfaMethod, setMfaMethod] = useState<'sms' | 'totp'>('sms');
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [recoveryRoles, setRecoveryRoles] = useState<string[]>([]);

  const getCsrfToken = async () => {
    const response = await fetch('/api/auth/csrf', { cache: 'no-store' });
    requireJsonResponse(response);
    const payload = await response.json();
    if (!response.ok || !payload.csrfToken) {
      throw new Error(payload.detail || payload.error || 'Unable to prepare a secure sign-in. Please try again.');
    }
    return payload.csrfToken as string;
  };

  const finishSignIn = (roles: string[]) => {
    const destination = getDjangoHomePath(roles);
    if (destination === '/login') {
      throw new Error('Your account does not have an assigned portal role yet. Contact your administrator.');
    }
    router.replace(destination);
    router.refresh();
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsLoading(true);
    setError('');

    try {
      const csrfToken = await getCsrfToken();
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({ identifier, password }),
      });
      requireJsonResponse(response);
      const payload: AuthResponse = await response.json();
      if (
        response.status === 202
        && payload.mfa_required === true
        && (typeof payload.challenge_id === 'number' || typeof payload.challenge_id === 'string')
      ) {
        setChallengeId(payload.challenge_id);
        setMfaMethod(payload.mfa_method === 'totp' ? 'totp' : 'sms');
        setTotpSetup(
          payload.setup_required && typeof payload.secret === 'string' && typeof payload.provisioning_uri === 'string'
            ? { secret: payload.secret, uri: payload.provisioning_uri }
            : null,
        );
        setRecoveryCodes([]);
        setRecoveryRoles([]);
        setVerificationCode('');
        return;
      }
      if (!response.ok || !payload.user) {
        throw new Error(payload.detail || payload.error || 'Invalid credentials or account locked.');
      }
      finishSignIn(payload.user.roles || []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to sign in. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleVerifyCode = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (challengeId === null) return;
    setIsLoading(true);
    setError('');

    try {
      const csrfToken = await getCsrfToken();
      const response = await fetch(
        mfaMethod === 'totp' ? '/api/auth/mfa/totp/verify' : '/api/auth/mfa/verify',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify({ challenge_id: challengeId, code: verificationCode }),
        },
      );
      requireJsonResponse(response);
      const payload: AuthResponse = await response.json();
      if (!response.ok || !payload.user) {
        throw new Error(payload.detail || payload.error || 'The verification code was invalid or expired.');
      }
      if (Array.isArray(payload.recovery_codes) && payload.recovery_codes.length > 0) {
        setRecoveryCodes(payload.recovery_codes);
        setRecoveryRoles(payload.user.roles || []);
        return;
      }
      finishSignIn(payload.user.roles || []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to verify sign-in code.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-[#f4f7f5] text-slate-900">
      <div className="mx-auto grid min-h-screen max-w-6xl items-center gap-10 px-4 py-10 lg:grid-cols-[1fr_0.85fr] lg:px-8">
        <section className="hidden rounded-3xl bg-[#102820] p-10 text-white shadow-xl lg:block">
          <Link href="/" className="inline-flex items-center gap-3" aria-label="Legacy Real Estate home">
            <span className="grid h-11 w-11 place-items-center rounded-2xl bg-emerald-400 text-[#102820]">
              <Building2 size={23} />
            </span>
            <span>
              <span className="block text-sm font-bold tracking-wide">LEGACY</span>
              <span className="block text-[10px] uppercase tracking-[0.24em] text-emerald-100/65">Real Estate Agency</span>
            </span>
          </Link>
          <div className="mt-20 max-w-lg">
            <span className="inline-flex items-center gap-2 rounded-full border border-emerald-400/30 bg-emerald-400/10 px-3.5 py-1 text-xs font-semibold text-emerald-300">
              <ShieldCheck size={14} /> Secure, role-based access
            </span>
            <h1 className="mt-6 text-4xl font-bold tracking-tight">One account. The right workspace.</h1>
            <p className="mt-4 text-sm leading-7 text-emerald-50/80">
              Sign in with your assigned account. Your role determines which portal you can access.
              Directors and finance roles complete authenticator verification before entry.
            </p>
          </div>
          <p className="mt-20 border-t border-white/10 pt-5 text-xs text-emerald-100/60">
            © 2026 Legacy Real Estate Agency · Nairobi & Nakuru
          </p>
        </section>

        <section className="mx-auto w-full max-w-md">
          <div className="mb-6 text-center lg:hidden">
            <Link href="/" className="inline-flex items-center gap-3">
              <span className="grid h-10 w-10 place-items-center rounded-xl bg-[#123b2c] text-emerald-200">
                <Building2 size={21} />
              </span>
              <span className="text-sm font-bold">LEGACY REAL ESTATE</span>
            </Link>
          </div>
          <div className="rounded-3xl border border-slate-200/80 bg-white p-7 shadow-sm sm:p-9">
            {recoveryCodes.length > 0 ? (
              <div className="space-y-5">
                <div>
                  <h1 className="text-2xl font-bold">Save your recovery codes</h1>
                  <p className="mt-2 text-sm text-slate-600">
                    These one-time codes can replace your authenticator if you lose access. They will not be shown again.
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-2 rounded-xl bg-slate-50 p-4 font-mono text-sm">
                  {recoveryCodes.map((code) => <span key={code}>{code}</span>)}
                </div>
                <button
                  type="button"
                  onClick={() => finishSignIn(recoveryRoles)}
                  className="w-full rounded-xl bg-emerald-800 py-3.5 text-sm font-bold text-white hover:bg-emerald-900"
                >
                  Continue to workspace
                </button>
              </div>
            ) : challengeId !== null ? (
              <form onSubmit={handleVerifyCode} className="space-y-5">
                <div>
                  <span className="inline-flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-emerald-800">
                    <Smartphone size={15} /> Two-factor verification
                  </span>
                  <h1 className="mt-2 text-2xl font-bold">
                    {totpSetup ? 'Set up your authenticator' : 'Verify your sign-in'}
                  </h1>
                  <p className="mt-2 text-sm text-slate-600">
                    {totpSetup
                      ? 'Scan this QR code with your authenticator app, then enter its current six-digit code.'
                      : 'Enter the current code from your authenticator app, or use an unused recovery code.'}
                  </p>
                </div>

                {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</p>}

                {totpSetup && (
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                    <div className="mx-auto w-fit rounded-xl bg-white p-3">
                      <QRCodeSVG value={totpSetup.uri} size={192} level="M" includeMargin title="Authenticator app setup QR code" />
                    </div>
                    <p className="mt-3 text-center text-xs font-semibold text-slate-700">Can’t scan? Enter this setup key manually:</p>
                    <code className="mt-1 block break-all text-center font-mono text-sm text-emerald-900">{totpSetup.secret}</code>
                  </div>
                )}

                <label className="block text-xs font-bold uppercase tracking-wider text-slate-700">
                  {mfaMethod === 'totp' ? 'Authenticator or recovery code' : 'Verification code'}
                  <input
                    type="text"
                    required
                    maxLength={16}
                    autoComplete="one-time-code"
                    value={verificationCode}
                    onChange={(event) => setVerificationCode(event.target.value)}
                    placeholder={totpSetup ? '123456' : '6-digit code or recovery code'}
                    className="mt-1.5 w-full rounded-xl border border-slate-300 bg-slate-50 px-3.5 py-3 text-center font-mono text-xl tracking-widest outline-none focus:border-emerald-700 focus:ring-2 focus:ring-emerald-700/20"
                  />
                </label>

                <button type="submit" disabled={isLoading} className="w-full rounded-xl bg-emerald-800 py-3.5 text-sm font-bold text-white hover:bg-emerald-900 disabled:opacity-50">
                  {isLoading ? 'Verifying…' : 'Verify and continue'}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setChallengeId(null);
                    setTotpSetup(null);
                    setMfaMethod('sms');
                    setVerificationCode('');
                    setError('');
                  }}
                  className="w-full text-center text-xs font-semibold text-slate-500 hover:text-slate-800"
                >
                  Back to sign in
                </button>
              </form>
            ) : (
              <>
                <div className="mb-6">
                  <span className="inline-flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-emerald-800">
                    <KeyRound size={14} /> Account access
                  </span>
                  <h1 className="mt-2 text-2xl font-bold">Sign in to your account</h1>
                  <p className="mt-2 text-sm text-slate-600">
                    Your account role determines your portal after sign-in.
                  </p>
                </div>

                {error && <p role="alert" className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</p>}

                <form onSubmit={handleSubmit} className="space-y-4">
                  <label className="block text-xs font-bold uppercase tracking-wider text-slate-700">
                    Email or phone number
                    <input
                      type="text"
                      required
                      autoComplete="username"
                      value={identifier}
                      onChange={(event) => setIdentifier(event.target.value)}
                      placeholder="Email address or Kenyan phone number"
                      className="mt-1.5 w-full rounded-xl border border-slate-300 bg-slate-50/50 px-3.5 py-3 text-sm outline-none transition focus:border-emerald-700 focus:bg-white focus:ring-2 focus:ring-emerald-700/20"
                    />
                  </label>
                  <label className="block text-xs font-bold uppercase tracking-wider text-slate-700">
                    Password
                    <span className="relative mt-1.5 block">
                      <input
                        type={showPassword ? 'text' : 'password'}
                        required
                        autoComplete="current-password"
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                        className="w-full rounded-xl border border-slate-300 bg-slate-50/50 px-3.5 py-3 pr-12 text-sm outline-none transition focus:border-emerald-700 focus:bg-white focus:ring-2 focus:ring-emerald-700/20"
                      />
                      <button
                        type="button"
                        onClick={() => setShowPassword((visible) => !visible)}
                        aria-label={showPassword ? 'Hide password' : 'Show password'}
                        aria-pressed={showPassword}
                        className="absolute inset-y-0 right-0 grid w-12 place-items-center text-slate-500 hover:text-slate-900"
                      >
                        {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
                      </button>
                    </span>
                  </label>
                  <div className="flex justify-end">
                    <Link href="/forgot-password" className="text-xs font-semibold text-emerald-800 hover:underline">
                      Forgot password?
                    </Link>
                  </div>
                  <button
                    type="submit"
                    disabled={isLoading}
                    className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[#123b2c] py-3.5 text-sm font-bold text-white shadow-md hover:bg-[#0e3024] disabled:opacity-50"
                  >
                    {isLoading ? 'Signing in…' : 'Sign in'}
                    <ArrowRight size={16} />
                  </button>
                </form>
              </>
            )}
          </div>
          <p className="mt-5 text-center text-xs text-slate-500">
            Need an account? Contact your agency or property manager.
            <span className="mx-2">·</span>
            <Link href="/" className="font-semibold text-emerald-800 hover:underline">Home</Link>
          </p>
        </section>
      </div>
    </main>
  );
}
