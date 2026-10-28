'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Wrench, Eye, EyeOff, CheckCircle } from 'lucide-react';
import { createClient } from '../../lib/supabase/client';

const STRENGTH_LABELS = ['', 'Weak', 'Fair', 'Good', 'Strong', 'Very Strong'];
const STRENGTH_COLORS = ['bg-slate-200', 'bg-red-500', 'bg-orange-400', 'bg-yellow-400', 'bg-emerald-400', 'bg-emerald-600'];

function passwordStrength(p: string): number {
  if (!p) return 0;
  let score = 0;
  if (p.length >= 8) score++;
  if (/[a-z]/.test(p)) score++;
  if (/[A-Z]/.test(p)) score++;
  if (/\d/.test(p)) score++;
  if (/[^A-Za-z\d]/.test(p)) score++;
  return score;
}

export default function CaretakerSignupPage() {
  const router = useRouter();
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [inviteCode, setInviteCode] = useState('');
  const [inviteId, setInviteId] = useState('');
  const [activationReady, setActivationReady] = useState(false);
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get('inviteId') || '';
    setInviteId(id);
    if (!id) return;

    createClient().auth.getUser().then(({ data, error: authError }) => {
      if (authError || !data.user?.email) {
        setError('This activation link is invalid or expired. Ask the landlord to send another one.');
        return;
      }
      setEmail(data.user.email);
      setActivationReady(true);
    });
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    if (!/^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z\d]).{8,}$/.test(password)) {
      setError('Password must be at least 8 characters and include uppercase, lowercase, number, and special character.');
      return;
    }
    setLoading(true);
    try {
      if (inviteId) {
        const supabase = createClient();
        const { data: sessionData, error: sessionError } = await supabase.auth.getSession();
        if (sessionError || !sessionData.session) {
          throw new Error('Your activation session has expired. Ask the landlord to send another link.');
        }
        const { error: updateError } = await supabase.auth.updateUser({
          password,
          data: { full_name: fullName.trim(), phone },
        });
        if (updateError) throw new Error(updateError.message);

        const res = await fetch('/api/auth/caretaker-invite', {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${sessionData.session.access_token}`,
          },
          body: JSON.stringify({ inviteId, fullName, phone }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error);
        setSuccess(true);
        setTimeout(() => router.push('/caretaker/dashboard'), 1000);
        return;
      }

      const res = await fetch('/api/auth/register-caretaker', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fullName, email, phone, inviteCode, password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      setSuccess(true);
      const supabase = createClient();
      const { error: signInError } = await supabase.auth.signInWithPassword({ email, password });
      if (signInError) throw new Error(signInError.message);
      setTimeout(() => router.push('/caretaker/dashboard'), 1000);
    } catch (err: any) {
      setError(err.message || 'Registration failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  if (success) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
        <div className="bg-white rounded-2xl p-10 text-center shadow-xl border border-slate-100 max-w-sm w-full">
          <div className="w-16 h-16 bg-emerald-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <CheckCircle className="text-emerald-600" size={32} />
          </div>
          <h2 className="text-2xl font-bold text-slate-900 mb-2">Account Created!</h2>
          <p className="text-slate-500 text-sm">Redirecting you to the caretaker dashboard...</p>
        </div>
      </div>
    );
  }

  const strength = passwordStrength(password);

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl border border-slate-100 w-full max-w-md overflow-hidden">
        <div className="bg-slate-900 p-8 text-center text-white">
          <div className="w-12 h-12 bg-emerald-500/20 rounded-xl flex items-center justify-center mx-auto mb-4">
            <Wrench className="text-emerald-400" size={24} />
          </div>
          <h1 className="text-2xl font-bold tracking-tight">Activate Caretaker Account</h1>
          <p className="text-slate-400 mt-2 text-sm">{inviteId ? 'Complete your account setup using the landlord’s secure invitation.' : 'Use an existing invitation code to create your account.'}</p>
        </div>

        <form onSubmit={handleSubmit} className="p-8 space-y-5">
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3 rounded-lg">
              {error}
            </div>
          )}

          {!inviteId && <div>
            <label className="block text-sm font-bold text-slate-700 mb-1.5">Invite Code</label>
            <input
              type="text"
              placeholder="e.g. ABCD1234XY"
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value.toUpperCase())}
              required
              className="w-full border border-slate-300 rounded-lg px-3 py-2.5 font-mono text-sm outline-none focus:ring-2 focus:ring-emerald-500 tracking-widest uppercase"
            />
            <p className="text-xs text-slate-400 mt-1">Get this code from your landlord. It expires after 48 hours.</p>
          </div>}

          <div>
            <label className="block text-sm font-bold text-slate-700 mb-1.5">Full Name</label>
            <input type="text" placeholder="Your full name" value={fullName} onChange={(e) => setFullName(e.target.value)} required disabled={Boolean(inviteId) && !activationReady} className="w-full border border-slate-300 rounded-lg px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-emerald-500 disabled:bg-slate-100" />
          </div>
          <div>
            <label className="block text-sm font-bold text-slate-700 mb-1.5">Email Address</label>
            <input type="email" placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)} required readOnly={Boolean(inviteId)} className="w-full border border-slate-300 rounded-lg px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-emerald-500 read-only:bg-slate-100" />
          </div>
          <div>
            <label className="block text-sm font-bold text-slate-700 mb-1.5">Phone Number</label>
            <input type="tel" placeholder="+254 7XX XXX XXX" value={phone} onChange={(e) => setPhone(e.target.value)} required disabled={Boolean(inviteId) && !activationReady} className="w-full border border-slate-300 rounded-lg px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-emerald-500 disabled:bg-slate-100" />
          </div>
          <div>
            <label className="block text-sm font-bold text-slate-700 mb-1.5">Create Password</label>
            <div className="relative">
              <input
                type={showPassword ? 'text' : 'password'}
                placeholder="Min. 8 characters"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className="w-full border border-slate-300 rounded-lg px-3 py-2.5 pr-10 text-sm outline-none focus:ring-2 focus:ring-emerald-500"
              />
              <button type="button" onClick={() => setShowPassword(p => !p)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 transition">
                {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>
            {password && (
              <div className="mt-2 space-y-1">
                <div className="flex gap-1">
                  {[1, 2, 3, 4, 5].map(i => (
                    <div key={i}
                      className={`h-1 flex-1 rounded-full transition-all duration-300 ${i <= strength ? STRENGTH_COLORS[strength] : 'bg-slate-200'}`} />
                  ))}
                </div>
                <p className={`text-xs font-medium ${strength >= 4 ? 'text-emerald-600' : strength >= 3 ? 'text-yellow-600' : 'text-red-600'}`}>
                  {STRENGTH_LABELS[strength]}
                </p>
              </div>
            )}
          </div>

          <div>
            <label className="block text-sm font-bold text-slate-700 mb-1.5">Confirm Password</label>
            <div className="relative">
              <input
                type={showConfirmPassword ? 'text' : 'password'}
                placeholder="Repeat your password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                className="w-full border border-slate-300 rounded-lg px-3 py-2.5 pr-10 text-sm outline-none focus:ring-2 focus:ring-emerald-500"
              />
              <button type="button" onClick={() => setShowConfirmPassword((value) => !value)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
                {showConfirmPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>
          </div>

          <button
            type="submit"
            disabled={loading || (Boolean(inviteId) && !activationReady)}
            className="w-full bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-300 text-white font-bold py-3 rounded-xl transition-colors mt-2"
          >
            {loading ? 'Creating Account...' : inviteId ? 'Activate Caretaker Account' : 'Create Caretaker Account'}
          </button>

          <p className="text-center text-sm text-slate-500">
            Already have an account?{' '}
            <Link href="/login" className="text-emerald-600 hover:underline font-medium">Log In</Link>
          </p>
        </form>
      </div>
    </div>
  );
}
