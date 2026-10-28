'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  Building2, Eye, EyeOff, Lock, Mail, Phone, User,
  ShieldCheck, CheckCircle2, ArrowRight,
} from 'lucide-react';
import { createClient } from '../../lib/supabase/client';

type Field = 'fullName' | 'propertyName' | 'email' | 'phone' | 'password' | 'confirmPassword';

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

export default function LandlordSignup() {
  const router = useRouter();
  const [form, setForm] = useState<Record<Field, string>>({
    fullName: '', email: '', phone: '', propertyName: '',
    password: '', confirmPassword: '',
  });
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showWelcome, setShowWelcome] = useState(false);

  useEffect(() => {
    const dismissed = localStorage.getItem('landlord_welcome_dismissed');
    if (!dismissed) setShowWelcome(true);
  }, []);

  const dismissWelcome = () => {
    localStorage.setItem('landlord_welcome_dismissed', 'true');
    setShowWelcome(false);
  };

  const set = (field: Field) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm(prev => ({ ...prev, [field]: e.target.value }));

  const strength = passwordStrength(form.password);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    if (form.password !== form.confirmPassword) return setError('Passwords do not match.');
    if (strength < 4) return setError('Please use a stronger password (uppercase, lowercase, number, special character).');
    setLoading(true);
    try {
      const response = await fetch('/api/auth/register-landlord', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...form }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to create account.');
      const supabase = createClient();
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email: form.email,
        password: form.password,
      });
      if (signInError) throw new Error(signInError.message);
      router.push('/landlord/welcome');
      router.refresh();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {showWelcome && (
        <div className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-xl max-w-md w-full overflow-hidden animate-in fade-in zoom-in duration-200">
            <div className="p-8 text-center space-y-4">
              <div className="w-16 h-16 bg-emerald-100 text-emerald-600 rounded-full flex items-center justify-center mx-auto mb-4">
                <Building2 size={32} />
              </div>
              <h2 className="text-2xl font-bold text-slate-900">Welcome to Sunshine!</h2>
              <p className="text-slate-600 text-sm leading-relaxed">
                We're excited to have you on board. Our platform makes managing your properties, tenants, and billing completely seamless.
              </p>
              <div className="bg-slate-50 border border-slate-200 rounded-lg p-4 text-left mt-4 text-sm text-slate-600">
                <h3 className="font-bold text-slate-900 mb-1 flex items-center gap-2"><ShieldCheck size={16} className="text-emerald-600"/> Note to Landlords</h3>
                <p>This is a secure system. Please ensure you have your correct authorization code ready before proceeding with the setup.</p>
              </div>
              <button 
                onClick={dismissWelcome}
                className="w-full mt-6 bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-3 px-4 rounded-xl transition-colors flex items-center justify-center gap-2"
              >
                I Agree and Understand <CheckCircle2 size={18} />
              </button>
            </div>
          </div>
        </div>
      )}
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4 py-12">
      <div className="max-w-lg w-full bg-white rounded-2xl shadow-xl border border-slate-100 overflow-hidden">
        <div className="bg-slate-900 p-8 text-center text-white">
          <div className="w-12 h-12 bg-emerald-500/20 rounded-xl flex items-center justify-center mx-auto mb-4">
            <Building2 className="text-emerald-400" size={24} />
          </div>
          <h1 className="text-2xl font-bold tracking-tight">Create Landlord Account</h1>
          <p className="text-slate-400 mt-2 text-sm">Set up your property management workspace.</p>
        </div>

        <div className="p-8">
          <form onSubmit={submit} className="space-y-5">
            {error && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 flex items-start gap-2">
                <ShieldCheck size={16} className="shrink-0 mt-0.5" />
                {error}
              </div>
            )}

            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Full Name</label>
              <div className="relative">
                <User className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
                <input required value={form.fullName} onChange={set('fullName')}
                  className="block w-full pl-10 pr-3 py-2.5 border border-slate-300 rounded-lg outline-none focus:ring-2 focus:ring-emerald-500 transition-all text-sm"
                  placeholder="Jane Wanjiru" />
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Property Name</label>
              <div className="relative">
                <Building2 className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
                <input required value={form.propertyName} onChange={set('propertyName')}
                  className="block w-full pl-10 pr-3 py-2.5 border border-slate-300 rounded-lg outline-none focus:ring-2 focus:ring-emerald-500 transition-all text-sm"
                  placeholder="Sunshine Apartments" />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Email</label>
                <div className="relative">
                  <Mail className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
                  <input required type="email" value={form.email} onChange={set('email')}
                    className="block w-full pl-10 pr-3 py-2.5 border border-slate-300 rounded-lg outline-none focus:ring-2 focus:ring-emerald-500 transition-all text-sm"
                    placeholder="you@email.com" />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Phone</label>
                <div className="relative">
                  <Phone className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
                  <input required value={form.phone} onChange={set('phone')}
                    className="block w-full pl-10 pr-3 py-2.5 border border-slate-300 rounded-lg outline-none focus:ring-2 focus:ring-emerald-500 transition-all text-sm"
                    placeholder="07XXXXXXXX" />
                </div>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Password</label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
                <input required type={showPassword ? 'text' : 'password'} value={form.password} onChange={set('password')}
                  className="block w-full pl-10 pr-10 py-2.5 border border-slate-300 rounded-lg outline-none focus:ring-2 focus:ring-emerald-500 transition-all text-sm"
                  placeholder="8+ characters" />
                <button type="button" onClick={() => setShowPassword(v => !v)}
                  className="absolute inset-y-0 right-0 pr-3 flex items-center text-slate-400 hover:text-slate-600 transition">
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
              {form.password && (
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
              <label className="block text-sm font-medium text-slate-700 mb-1">Confirm Password</label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
                <input required type={showConfirm ? 'text' : 'password'} value={form.confirmPassword} onChange={set('confirmPassword')}
                  className={`block w-full pl-10 pr-10 py-2.5 border rounded-lg outline-none focus:ring-2 focus:ring-emerald-500 transition-all text-sm ${
                    form.confirmPassword && form.confirmPassword !== form.password
                      ? 'border-red-300 focus:ring-red-500'
                      : 'border-slate-300'
                  }`}
                  placeholder="Re-enter password" />
                <button type="button" onClick={() => setShowConfirm(v => !v)}
                  className="absolute inset-y-0 right-0 pr-3 flex items-center text-slate-400 hover:text-slate-600 transition">
                  {showConfirm ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>

            <button disabled={loading}
              className="w-full flex items-center justify-center gap-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold py-3 rounded-xl transition-all mt-2">
              {loading ? (
                <><span className="w-4 h-4 rounded-full border-2 border-white/30 border-t-white animate-spin" /> Creating account...</>
              ) : (
                <> Create Landlord Account <ArrowRight size={16} /></>
              )}
            </button>

            <p className="text-center text-sm text-slate-500">
              Already registered?{' '}
              <Link href="/login" className="font-semibold text-emerald-600 hover:text-emerald-700 transition">
                Sign in
              </Link>
            </p>
          </form>
        </div>
      </div>
    </div>
    </>
  );
}
