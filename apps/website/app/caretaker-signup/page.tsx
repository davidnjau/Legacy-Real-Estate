'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { CheckCircle, Wrench } from 'lucide-react';

export default function CaretakerSignupPage() {
  const router = useRouter();
  const [token, setToken] = useState('');
  const [propertyName, setPropertyName] = useState('');
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    const invitationToken = new URLSearchParams(window.location.search).get('token') || '';
    setToken(invitationToken);
    if (!invitationToken) {
      setError('An activation link is required. Ask the landlord to create a new invitation.');
      return;
    }

    void fetch(`/api/auth/caretaker-invite?token=${encodeURIComponent(invitationToken)}`)
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'This invitation is invalid or expired.');
        setEmail(data.email);
        setPropertyName(data.property_name);
        setReady(true);
      })
      .catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : 'Unable to verify this invitation.');
      });
  }, []);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    if (!/^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z\d]).{12,}$/.test(password)) {
      setError('Password must be at least 12 characters and include uppercase, lowercase, number, and special character.');
      return;
    }

    setLoading(true);
    try {
      const response = await fetch('/api/auth/register-caretaker', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, fullName, phone, password }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Caretaker registration failed.');
      setSuccess(true);
      setTimeout(() => router.push('/caretaker/dashboard'), 1000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Registration failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  if (success) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
        <div className="w-full max-w-sm rounded-2xl border border-slate-100 bg-white p-10 text-center shadow-xl">
          <CheckCircle className="mx-auto mb-4 text-emerald-600" size={40} />
          <h2 className="mb-2 text-2xl font-bold text-slate-900">Account Created!</h2>
          <p className="text-sm text-slate-500">Redirecting you to the caretaker dashboard...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
      <div className="w-full max-w-md overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-xl">
        <div className="bg-slate-900 p-8 text-center text-white">
          <Wrench className="mx-auto mb-4 text-emerald-400" size={30} />
          <h1 className="text-2xl font-bold tracking-tight">Activate Caretaker Account</h1>
          <p className="mt-2 text-sm text-slate-300">
            {propertyName ? `Complete your account setup for ${propertyName}.` : 'Complete your account setup using the landlord’s secure invitation.'}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5 p-8">
          {error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
          <div>
            <label className="mb-1.5 block text-sm font-bold text-slate-700">Full Name</label>
            <input value={fullName} onChange={(event) => setFullName(event.target.value)} required disabled={!ready} className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm disabled:bg-slate-100" />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-bold text-slate-700">Email Address</label>
            <input type="email" value={email} readOnly required className="w-full rounded-lg border border-slate-300 bg-slate-100 px-3 py-2.5 text-sm" />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-bold text-slate-700">Phone Number</label>
            <input type="tel" placeholder="07XX XXX XXX or 2547XXXXXXXX" value={phone} onChange={(event) => setPhone(event.target.value)} required disabled={!ready} className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm disabled:bg-slate-100" />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-bold text-slate-700">Create Password</label>
            <input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} required disabled={!ready} className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm disabled:bg-slate-100" />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-bold text-slate-700">Confirm Password</label>
            <input type="password" autoComplete="new-password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} required disabled={!ready} className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm disabled:bg-slate-100" />
          </div>
          <button type="submit" disabled={loading || !ready} className="w-full rounded-xl bg-emerald-600 py-3 font-bold text-white transition-colors hover:bg-emerald-700 disabled:bg-slate-300">
            {loading ? 'Creating Account...' : 'Activate Caretaker Account'}
          </button>
          <p className="text-center text-sm text-slate-500">
            Already have an account? <Link href="/login" className="font-medium text-emerald-600 hover:underline">Log in</Link>
          </p>
        </form>
      </div>
    </div>
  );
}
