'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { KeyRound, Lock, CheckCircle, AlertTriangle, ArrowRight, Eye, EyeOff } from 'lucide-react';
import Link from 'next/link';
import { createClient } from '../../lib/supabase/client';

export default function TenantActivationPage() {
  const router = useRouter();
  
  // Step 1 State
  const [unitCode, setUnitCode] = useState('');
  const [activationCode, setActivationCode] = useState('');
  const [isVerifying, setIsVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState('');

  // Step 2 State
  const [verifiedTenant, setVerifiedTenant] = useState<{ claimId: string; tenantId: string; fullName: string; email: string; unitCode: string } | null>(null);
  const [activationName, setActivationName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [isActivating, setIsActivating] = useState(false);
  const [activateError, setActivateError] = useState('');
  const [activateSuccess, setActivateSuccess] = useState(false);
  const [emailMode, setEmailMode] = useState(false);
  const [emailClaimId, setEmailClaimId] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);

  const STRENGTH_LABELS = ['', 'Weak', 'Fair', 'Good', 'Strong', 'Very Strong'];
  const STRENGTH_COLORS = ['bg-slate-200', 'bg-red-500', 'bg-orange-400', 'bg-yellow-400', 'bg-emerald-400', 'bg-emerald-600'];
  const passwordStrength = (p: string) => {
    if (!p) return 0;
    let score = 0;
    if (p.length >= 8) score++;
    if (/[a-z]/.test(p)) score++;
    if (/[A-Z]/.test(p)) score++;
    if (/\d/.test(p)) score++;
    if (/[^A-Za-z\d]/.test(p)) score++;
    return score;
  };
  const strength = passwordStrength(password);

  useEffect(() => {
    const initializeEmailActivation = async () => {
      const params = new URLSearchParams(window.location.search);
      if (params.get('mode') !== 'email') return;

      setEmailMode(true);
      setEmailClaimId(params.get('claimId') || '');

      const supabase = createClient();
      const code = params.get('code');
      const tokenHash = params.get('token_hash');
      const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ''));
      if (code) {
        await supabase.auth.exchangeCodeForSession(code);
      } else if (tokenHash) {
        await supabase.auth.verifyOtp({ token_hash: tokenHash, type: 'recovery' });
      } else if (hashParams.get('access_token') && hashParams.get('refresh_token')) {
        await supabase.auth.setSession({
          access_token: hashParams.get('access_token')!,
          refresh_token: hashParams.get('refresh_token')!,
        });
      }

      if (params.get('error')) {
        setActivateError(params.get('error_description') || 'This activation link is invalid or expired.');
        return;
      }

      const { data: sessionData } = await supabase.auth.getSession();
      const { data } = await supabase.auth.getUser();
      if (data.user) {
        const contextResponse = await fetch(`/api/auth/activate/context?claimId=${encodeURIComponent(params.get('claimId') || '')}`, {
          headers: sessionData.session?.access_token
            ? { Authorization: `Bearer ${sessionData.session.access_token}` }
            : undefined,
        });
        const context = await contextResponse.json();
        if (!contextResponse.ok) {
          setActivateError(context.error || 'Unable to verify the tenant activation link.');
          return;
        }
        setVerifiedTenant({
          claimId: params.get('claimId') || '',
          tenantId: context.tenantId,
          fullName: context.fullName,
          email: context.email,
          unitCode: context.unitCode,
        });
        setActivationName(context.fullName || '');
      } else {
        setActivateError('This activation link is invalid or has expired. Please request a new activation email or use a claim code.');
      }
    };
    initializeEmailActivation();
  }, []);

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsVerifying(true);
    setVerifyError('');

    try {
      const res = await fetch('/api/auth/activate/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ unitCode, code: activationCode }),
      });
      const data = await res.json();
      
      if (!res.ok) throw new Error(data.error);
      
      setVerifiedTenant({ ...data, unitCode: unitCode.trim() });
      setActivationName(data.fullName || '');
    } catch (err: any) {
      setVerifyError(err.message);
    } finally {
      setIsVerifying(false);
    }
  };

  const handleActivate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirmPassword) {
      setActivateError("Passwords do not match.");
      return;
    }
    if (!/^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z\d]).{8,}$/.test(password)) {
      setActivateError('Password must be at least 8 characters and include uppercase, lowercase, number, and special character.');
      return;
    }

    setIsActivating(true);
    setActivateError('');

    try {
      const supabase = createClient();
      let res: Response;
      if (emailMode) {
        const { error } = await supabase.auth.updateUser({ password });
        if (error) throw new Error(error.message);
        const { data: sessionData } = await supabase.auth.getSession();
        res = await fetch('/api/auth/activate/email-complete', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(sessionData.session?.access_token
              ? { Authorization: `Bearer ${sessionData.session.access_token}` }
              : {}),
          },
          body: JSON.stringify({ claimId: emailClaimId, fullName: activationName }),
        });
      } else {
        res = await fetch('/api/auth/activate/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          tenantId: verifiedTenant?.tenantId,
          claimId: verifiedTenant?.claimId,
          fullName: activationName,
          password 
        }),
        });
      }
      const data = await res.json();
      
      if (!res.ok) throw new Error(data.error);
      
      if (!emailMode) {
        const { error: signInError } = await supabase.auth.signInWithPassword({ email: verifiedTenant?.email || '', password });
        if (signInError) throw new Error(signInError.message);
      }
      setActivateSuccess(true);
      setTimeout(() => router.push('/tenant/dashboard'), 1200);
    } catch (err: any) {
      setActivateError(err.message);
    } finally {
      setIsActivating(false);
    }
  };

  if (activateSuccess) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
        <div className="max-w-md w-full bg-white rounded-2xl shadow-xl p-10 text-center border border-slate-100">
          <div className="w-20 h-20 bg-emerald-100 rounded-full flex items-center justify-center mx-auto mb-6">
            <CheckCircle size={40} className="text-emerald-600" />
          </div>
          <h2 className="text-2xl font-bold text-slate-900 mb-2">Account Activated!</h2>
          <p className="text-slate-500 mb-6">Your password has been securely set. Redirecting you to your tenant dashboard...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <h2 className="mt-6 text-center text-3xl font-extrabold text-slate-900">
          Activate Your Account
        </h2>
        {!verifiedTenant && (
          <p className="mt-2 text-center text-sm text-slate-600">
            Use the secure activation link sent to your email address.
          </p>
        )}
      </div>

      <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
        <div className="bg-white py-8 px-4 shadow-xl sm:rounded-2xl sm:px-10 border border-slate-100">
          
          {emailMode && !verifiedTenant ? (
            <div className="space-y-4 text-center">
              <p className="text-sm text-slate-600">{activateError || 'Verifying your secure activation link...'}</p>
              {activateError && <Link href="/login" className="text-emerald-600 font-semibold">Return to sign in</Link>}
            </div>
          ) : !verifiedTenant ? (
            <form onSubmit={handleVerify} className="space-y-6">
              {verifyError && (
                <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-xl text-sm flex gap-2">
                  <AlertTriangle size={18} className="shrink-0" /> {verifyError}
                </div>
              )}
              
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Room / Unit Number
                </label>
                <input
                  type="text"
                  required
                  value={unitCode}
                  onChange={(e) => setUnitCode(e.target.value)}
                  className="w-full border border-slate-300 rounded-xl px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-emerald-500"
                  placeholder="e.g. A1"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Activation Code
                </label>
                <div className="relative">
                  <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                    <KeyRound className="h-5 w-5 text-slate-400" />
                  </div>
                  <input
                    type="text"
                    required
                    value={activationCode}
                    onChange={(e) => setActivationCode(e.target.value.toUpperCase())}
                    className="w-full border border-slate-300 rounded-xl pl-10 px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-emerald-500 uppercase tracking-widest font-mono font-bold"
                    placeholder="8-CHARACTER CODE"
                  />
                </div>
              </div>

              <button
                type="submit"
                disabled={isVerifying}
                className="w-full flex justify-center items-center gap-2 py-3 px-4 border border-transparent rounded-xl shadow-sm text-sm font-bold text-white bg-slate-900 hover:bg-slate-800 disabled:opacity-70 transition-colors"
              >
                {isVerifying ? 'Verifying...' : 'Verify Code'} <ArrowRight size={16} />
              </button>
            </form>
          ) : (
            <form onSubmit={handleActivate} className="space-y-6">
              <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-5 mb-6 text-center">
                <h3 className="text-xl font-bold text-emerald-900 mb-1">
                  Hi {verifiedTenant?.fullName.split(' ')[0] || 'Tenant'}!
                </h3>
                <p className="text-sm text-emerald-800">
                  {verifiedTenant?.unitCode ? <>Your unit is <strong>{verifiedTenant.unitCode}</strong>. </> : null}
                  Create a secure password for <strong>{verifiedTenant?.email}</strong> to complete activation.
                </p>
              </div>

              {activateError && (
                <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-xl text-sm flex gap-2">
                  <AlertTriangle size={18} className="shrink-0" /> {activateError}
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Full Name</label>
                <input
                  type="text"
                  required
                  value={activationName}
                  onChange={(e) => setActivationName(e.target.value)}
                  className="w-full border border-slate-300 rounded-xl px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-emerald-500"
                  autoComplete="name"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Email</label>
                <input
                  type="email"
                  value={verifiedTenant?.email || ''}
                  readOnly
                  className="w-full border border-slate-200 bg-slate-50 rounded-xl px-4 py-3 text-slate-600"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Create Password
                </label>
                <div className="relative">
                  <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                    <Lock className="h-5 w-5 text-slate-400" />
                  </div>
                  <input
                    type={showPassword ? 'text' : 'password'}
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full border border-slate-300 rounded-xl pl-10 px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-emerald-500"
                    placeholder="••••••••"
                  />
                  <button type="button" onClick={() => setShowPassword((value) => !value)} className="absolute inset-y-0 right-0 pr-3 flex items-center text-slate-400">
                    {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                  </button>
                </div>
                {password && (
                  <div className="mt-2 space-y-1">
                    <div className="flex gap-1">
                      {[1, 2, 3, 4, 5].map(i => (
                        <div key={i} className={`h-1 flex-1 rounded-full transition-all duration-300 ${i <= strength ? STRENGTH_COLORS[strength] : 'bg-slate-200'}`} />
                      ))}
                    </div>
                    <p className={`text-xs font-medium ${strength >= 4 ? 'text-emerald-600' : strength >= 3 ? 'text-yellow-600' : 'text-red-600'}`}>
                      {STRENGTH_LABELS[strength]}
                    </p>
                  </div>
                )}
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Confirm Password
                </label>
                <div className="relative">
                  <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                    <Lock className="h-5 w-5 text-slate-400" />
                  </div>
                  <input
                    type={showConfirmPassword ? 'text' : 'password'}
                    required
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className={`w-full border rounded-xl pl-10 px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-emerald-500 ${
                      confirmPassword && confirmPassword !== password ? 'border-red-300 focus:ring-red-500' : 'border-slate-300'
                    }`}
                    placeholder="••••••••"
                  />
                  <button type="button" onClick={() => setShowConfirmPassword((value) => !value)} className="absolute inset-y-0 right-0 pr-3 flex items-center text-slate-400">
                    {showConfirmPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                  </button>
                </div>
              </div>

              <button
                type="submit"
                disabled={isActivating}
                className="w-full flex justify-center items-center gap-2 py-3 px-4 border border-transparent rounded-xl shadow-sm text-sm font-bold text-white bg-emerald-600 hover:bg-emerald-700 disabled:opacity-70 transition-colors"
              >
                {isActivating ? 'Activating...' : 'Activate Account'}
              </button>
            </form>
          )}

          <div className="mt-6 text-center text-sm">
            <Link href="/login" className="text-emerald-600 hover:text-emerald-500 font-medium">
              Already activated? Sign in here.
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
