// app/login/page.tsx
'use client';
import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Building2, Mail, Lock, ArrowRight, Eye, EyeOff } from 'lucide-react';
import { createClient } from '../../lib/supabase/client';

export default function LoginPage() {
  const router = useRouter();
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [credentials, setCredentials] = useState({ email: '', password: '' });
  const [propertyName, setPropertyName] = useState('...');

  useEffect(() => {
    fetch('/api/system/config')
      .then(res => res.json())
      .then(data => {
        if (data.propertyName) setPropertyName(data.propertyName);
      })
      .catch(console.error);
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setCredentials({ ...credentials, [e.target.name]: e.target.value });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');

    const supabase = createClient();
    const { data, error: signInError } = await supabase.auth.signInWithPassword({
      email: credentials.email,
      password: credentials.password,
    });

    if (signInError || !data.user) {
      setError(signInError?.message || 'Invalid email or password.');
      setIsLoading(false);
      return;
    }

    const { data: profile, error: profileError } = await supabase
      .from('users')
      .select('role, onboarding_completed')
      .eq('id', data.user.id)
      .maybeSingle();

    if (profileError) {
      console.error('Profile fetch error:', profileError);
      setError('Failed to fetch your profile. Please try again.');
      setIsLoading(false);
      return;
    }

    if (!profile) {
      setError('Your account profile could not be found in our system. Please contact the landlord.');
      setIsLoading(false);
      return;
    }

    const normalizedRole = String(profile.role || '').trim().toLowerCase();

    if (normalizedRole === 'landlord') {
      router.push(profile.onboarding_completed ? '/landlord/dashboard' : '/landlord/welcome');
    } else if (normalizedRole === 'caretaker') {
      router.push('/caretaker/dashboard');
    } else {
      router.push('/tenant/dashboard');
    }

    setIsLoading(false);
  };

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
      <div className="max-w-md w-full bg-white rounded-2xl shadow-xl border border-slate-100 overflow-hidden">
        <div className="bg-slate-900 p-8 text-center">
          <Building2 className="w-12 h-12 text-emerald-400 mx-auto mb-4" />
          <h1 className="text-2xl font-bold text-white tracking-tight">{propertyName === '...' ? 'Welcome' : propertyName}</h1>
          <p className="text-slate-400 mt-2 text-sm">Welcome back. Please sign in to your account.</p>
        </div>

        <div className="p-8">
          <form onSubmit={handleSubmit} className="space-y-6">
            {error && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {error}
              </div>
            )}
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-2">Email Address</label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <Mail size={18} className="text-slate-400" />
                </div>
                <input
                  type="email"
                  name="email"
                  value={credentials.email}
                  onChange={handleChange}
                  required
                  className="block w-full pl-10 pr-3 py-2.5 border border-slate-300 rounded-lg outline-none focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500 transition-all text-sm"
                  placeholder="you@example.com"
                />
              </div>
            </div>

            <div>
              <div className="flex justify-between items-center mb-2">
                <label className="block text-sm font-medium text-slate-700">Password</label>
                <Link href="/forgot-password" className="text-sm font-medium text-emerald-600 hover:text-emerald-500">
                  Forgot password?
                </Link>
              </div>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <Lock size={18} className="text-slate-400" />
                </div>
                <input
                  type={showPassword ? 'text' : 'password'}
                  name="password"
                  value={credentials.password}
                  onChange={handleChange}
                  required
                  className="block w-full pl-10 pr-10 py-2.5 border border-slate-300 rounded-lg outline-none focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500 transition-all text-sm"
                  placeholder="••••••••"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute inset-y-0 right-0 pr-3 flex items-center text-slate-400 hover:text-slate-600"
                >
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={isLoading}
              className="w-full flex items-center justify-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-3 px-4 rounded-lg transition-colors disabled:opacity-70"
            >
              {isLoading ? 'Signing in...' : 'Sign In'}
              {!isLoading && <ArrowRight size={18} />}
            </button>
          </form>

          <div className="mt-8 text-center">

            <p className="text-sm text-slate-600 mb-2">
              Are you a caretaker?{' '}
              <Link href="/caretaker-signup" className="font-bold text-emerald-600 hover:text-emerald-500 transition-colors">
                Join with invite code
              </Link>
            </p>
            <p className="text-sm text-slate-600 mb-2">
              New tenant?{' '}
              <Link href="/register" className="font-bold text-emerald-600 hover:text-emerald-500 transition-colors">
                Claim your unit
              </Link>
            </p>
            <p className="text-sm text-slate-600 mt-2">
              Existing tenant?{' '}
              <Link href="/existing-tenant" className="font-bold text-amber-700 hover:text-amber-600 transition-colors">
                Activate your account
              </Link>
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}