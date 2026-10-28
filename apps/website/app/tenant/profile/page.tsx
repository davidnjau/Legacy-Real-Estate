'use client';

import { useEffect, useState } from 'react';
import { Save } from 'lucide-react';

type Profile = { fullName: string; email: string; phone: string; deposit: number; securityDeposit: number; waterDeposit: number; moveInDate: string | null; unitCode: string | null; floor: string | null };

export default function TenantProfilePage() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [form, setForm] = useState({ fullName: '', email: '', phone: '' });
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    fetch('/api/tenant/profile').then(async (res) => {
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      setProfile(data.profile);
      setForm({ fullName: data.profile.fullName, email: data.profile.email, phone: data.profile.phone });
    }).catch((err) => setError(err.message));
  }, []);

  const save = async () => {
    setMessage(''); setError('');
    const res = await fetch('/api/tenant/profile', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form) });
    const data = await res.json();
    if (!res.ok) { setError(data.error); return; }
    setProfile((current) => current ? { ...current, ...form } : current);
    setMessage(data.message);
  };

  if (!profile) return <div className="text-slate-500">{error || 'Loading profile...'}</div>;
  return <div className="space-y-6">
    <div><h1 className="text-3xl font-bold text-slate-900">My Profile</h1><p className="text-slate-500 mt-1">Manage your contact details and view your tenancy information.</p></div>
    <section className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
      {message && <p className="text-sm text-emerald-600 mb-4">{message}</p>}
      {error && <p className="text-sm text-red-600 mb-4">{error}</p>}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
        <label>Full Name<input value={form.fullName} onChange={(e) => setForm({ ...form, fullName: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>
        <label>Email Address<input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>
        <label>Phone Number<input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>
        <div>Room<strong className="block mt-1">{profile.unitCode || '-'}</strong></div>
        <div>Floor<strong className="block mt-1">{profile.floor || '-'}</strong></div>
        <div>Security Deposit<strong className="block mt-1">KES {profile.securityDeposit.toLocaleString()}</strong></div>
        <div>Water Deposit<strong className="block mt-1">KES {profile.waterDeposit.toLocaleString()}</strong></div>
        <div>Move-In Date<strong className="block mt-1">{profile.moveInDate || '-'}</strong></div>
      </div>
      <button onClick={save} className="mt-6 inline-flex items-center gap-2 bg-emerald-600 text-white font-bold px-4 py-2.5 rounded-lg"><Save size={16} /> Save Changes</button>
    </section>
  </div>;
}
