'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowLeft, ArrowRight, Building2 } from 'lucide-react';

export default function TenantSignupPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    const form = new FormData(event.currentTarget);
    const password = String(form.get('password') || '');
    if (password.length < 12) {
      setError('Use a password with at least 12 characters.');
      setBusy(false);
      return;
    }

    try {
      const response = await fetch('/api/auth/register-tenant', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: form.get('name'),
          phone: form.get('phone'),
          email: form.get('email'),
          password,
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Unable to create your account.');
      router.push('/tenant/dashboard');
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to create your account.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#f5f7f5] px-4 py-10 text-slate-950 sm:px-6">
      <div className="mx-auto max-w-xl">
        <Link href="/" className="inline-flex items-center gap-2 text-sm font-medium text-slate-600 hover:text-emerald-900">
          <ArrowLeft size={16} /> Back to homes
        </Link>
        <section className="mt-6 rounded-3xl border border-slate-200 bg-white p-6 shadow-sm sm:p-9">
          <div className="grid size-12 place-items-center rounded-2xl bg-emerald-50 text-emerald-900"><Building2 size={23} /></div>
          <p className="mt-6 text-xs font-bold uppercase tracking-[0.16em] text-emerald-800">Resident account</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight">Create your account</h1>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            Register as a resident. Your agency will link your account to a home after your move-in is approved.
          </p>
          <form onSubmit={submit} className="mt-7 space-y-4">
            <label className="block text-sm font-medium text-slate-700">
              Full name
              <input name="name" autoComplete="name" required maxLength={255} className="mt-1.5 w-full rounded-xl border border-slate-300 px-3.5 py-3 outline-none focus:border-emerald-700 focus:ring-2 focus:ring-emerald-100" />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              Phone (M-Pesa number)
              <input name="phone" type="tel" autoComplete="tel" required placeholder="+254 7XX XXX XXX" className="mt-1.5 w-full rounded-xl border border-slate-300 px-3.5 py-3 outline-none focus:border-emerald-700 focus:ring-2 focus:ring-emerald-100" />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              Email
              <input name="email" type="email" autoComplete="email" required className="mt-1.5 w-full rounded-xl border border-slate-300 px-3.5 py-3 outline-none focus:border-emerald-700 focus:ring-2 focus:ring-emerald-100" />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              Password
              <input name="password" type="password" autoComplete="new-password" minLength={12} required className="mt-1.5 w-full rounded-xl border border-slate-300 px-3.5 py-3 outline-none focus:border-emerald-700 focus:ring-2 focus:ring-emerald-100" />
              <span className="mt-1 block text-xs font-normal text-slate-500">At least 12 characters.</span>
            </label>
            {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800">{error}</p>}
            <button disabled={busy} className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-900 px-4 py-3.5 text-sm font-semibold text-white hover:bg-emerald-950 disabled:opacity-60">
              {busy ? 'Creating account…' : 'Create resident account'} <ArrowRight size={16} />
            </button>
          </form>
          <p className="mt-5 text-center text-sm text-slate-600">
            Already registered? <Link href="/login?role=tenant" className="font-semibold text-emerald-900 hover:underline">Sign in</Link>
          </p>
        </section>
      </div>
    </main>
  );
}
