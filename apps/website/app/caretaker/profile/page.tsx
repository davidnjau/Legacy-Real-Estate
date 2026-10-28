'use client';

import { useEffect, useState } from 'react';
import { Save } from 'lucide-react';

export default function CaretakerProfilePage() {
  const [profile, setProfile] = useState({ full_name: '', email: '', phone: '', created_at: '' });
  const [form, setForm] = useState({ fullName: '', email: '', phone: '' });
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    fetch('/api/caretaker/profile').then(async (res) => {
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      setProfile(data.profile);
      setForm({ fullName: data.profile.full_name, email: data.profile.email, phone: data.profile.phone });
    }).catch((err) => setError(err.message));
  }, []);

  const save = async () => {
    setMessage(''); setError('');
    const res = await fetch('/api/caretaker/profile', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form) });
    const data = await res.json();
    if (!res.ok) { setError(data.error); return; }
    setProfile({ ...profile, full_name: form.fullName, email: form.email, phone: form.phone });
    setMessage(data.message);
  };

  return <div className="space-y-6"><div><h1 className="text-3xl font-bold text-slate-900">My Profile</h1><p className="text-slate-500 mt-1">Manage your caretaker account details.</p></div><section className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">{message && <p className="text-sm text-emerald-600 mb-4">{message}</p>}{error && <p className="text-sm text-red-600 mb-4">{error}</p>}<div className="grid grid-cols-1 sm:grid-cols-2 gap-5"><label>Full Name<input value={form.fullName} onChange={(e) => setForm({ ...form, fullName: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label><label>Email Address<input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label><label>Phone Number<input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label><div>Role<strong className="block mt-1">Caretaker</strong></div></div><button onClick={save} className="mt-6 inline-flex items-center gap-2 bg-emerald-600 text-white font-bold px-4 py-2.5 rounded-lg"><Save size={16} /> Save Changes</button></section></div>;
}
