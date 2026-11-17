'use client';

import { useEffect, useState } from 'react';
import { Save, Building2, User, CheckCircle2 } from 'lucide-react';

type LandlordProfile = {
  fullName: string;
  email: string;
  phone: string;
  propertyName?: string;
  branchName?: string;
};

export default function LandlordProfilePage() {
  const [profile, setProfile] = useState<LandlordProfile | null>(null);
  const [form, setForm] = useState({ fullName: '', email: '', phone: '' });
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const loadProfile = async () => {
      try {
        const profileRes = await fetch('/api/landlord/profile', { cache: 'no-store' });
        const profileData = await profileRes.json();
        if (!profileRes.ok || !profileData.profile) {
          throw new Error(profileData.error || 'Unable to load your landlord profile.');
        }

        const propRes = await fetch('/api/landlord/properties', { cache: 'no-store' });
        let propertyName: string | undefined;
        let branchName: string | undefined;
        if (propRes.ok) {
          const propData = await propRes.json();
          const property = propData.properties?.[0];
          propertyName = property?.name;
          branchName = property?.branch_id != null ? `Branch ID ${property.branch_id}` : undefined;
        } else {
          const propertyError = await propRes.json();
          setError(propertyError.error || 'Unable to load property assignments.');
        }

        const resolvedProfile: LandlordProfile = {
          fullName: profileData.profile.full_name,
          email: profileData.profile.email,
          phone: profileData.profile.phone,
          propertyName,
          branchName,
        };

        setProfile(resolvedProfile);
        setForm({
          fullName: resolvedProfile.fullName,
          email: resolvedProfile.email,
          phone: resolvedProfile.phone,
        });
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : 'Unable to load profile.');
      }
    };

    void loadProfile();
  }, []);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setMessage('');
    setError('');

    try {
      const res = await fetch('/api/landlord/profile', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to update profile.');
      setProfile((current) => (current ? { ...current, ...form } : current));
      setMessage('Profile updated successfully.');
    } catch (err: any) {
      setError(err.message || 'Unable to save profile changes.');
    } finally {
      setSaving(false);
    }
  };

  if (!profile) {
    return (
      <div className="p-8 text-sm text-slate-500">
        {error ? <p role="alert" className="text-red-700">{error}</p> : 'Loading landlord profile…'}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">
          Property Owner Profile
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Your registered landlord credentials, portfolio assignment, and management agreement details.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Left Column: Editable Contact Information */}
        <section className="lg:col-span-2 rounded-2xl border border-slate-200 bg-white p-6 shadow-xs">
          <div className="flex items-center gap-2 border-b border-slate-100 pb-4">
            <User size={18} className="text-emerald-700" />
            <h2 className="text-base font-bold text-slate-900">Personal & Contact Details</h2>
          </div>

          {message && (
            <div className="mt-4 flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs font-semibold text-emerald-800">
              <CheckCircle2 size={16} />
              <span>{message}</span>
            </div>
          )}

          {error && (
            <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs font-semibold text-red-800">
              {error}
            </div>
          )}

          <form onSubmit={save} className="mt-6 space-y-4">
            <div>
              <label className="block text-xs font-bold uppercase tracking-wider text-slate-700">
                Full Name
              </label>
              <input
                type="text"
                required
                value={form.fullName}
                onChange={(e) => setForm({ ...form, fullName: e.target.value })}
                className="mt-1.5 w-full rounded-xl border border-slate-300 bg-slate-50/50 px-3.5 py-2.5 text-sm text-slate-900 outline-none focus:border-emerald-700 focus:bg-white focus:ring-2 focus:ring-emerald-700/20"
              />
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="block text-xs font-bold uppercase tracking-wider text-slate-700">
                  Email Address
                </label>
                <input
                  type="email"
                  required
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  className="mt-1.5 w-full rounded-xl border border-slate-300 bg-slate-50/50 px-3.5 py-2.5 text-sm text-slate-900 outline-none focus:border-emerald-700 focus:bg-white focus:ring-2 focus:ring-emerald-700/20"
                />
              </div>

              <div>
                <label className="block text-xs font-bold uppercase tracking-wider text-slate-700">
                  Phone (M-Pesa / Remittance)
                </label>
                <input
                  type="tel"
                  required
                  value={form.phone}
                  onChange={(e) => setForm({ ...form, phone: e.target.value })}
                  className="mt-1.5 w-full rounded-xl border border-slate-300 bg-slate-50/50 px-3.5 py-2.5 text-sm text-slate-900 outline-none focus:border-emerald-700 focus:bg-white focus:ring-2 focus:ring-emerald-700/20"
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={saving}
              className="mt-2 inline-flex items-center gap-2 rounded-xl bg-emerald-800 px-5 py-2.5 text-xs font-bold text-white transition hover:bg-emerald-900 disabled:opacity-50"
            >
              <Save size={15} />
              <span>{saving ? 'Saving changes…' : 'Save Profile Changes'}</span>
            </button>
          </form>
        </section>

        {/* Right Column: Portfolio & Agreement Status Card */}
        <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xs space-y-5">
          <div className="flex items-center gap-2 border-b border-slate-100 pb-4">
            <Building2 size={18} className="text-emerald-700" />
            <h2 className="text-base font-bold text-slate-900">Portfolio & Agreement</h2>
          </div>

          <div>
            <p className="text-xs font-bold uppercase text-slate-500">Registered Property</p>
            <p className="mt-1 text-sm font-bold text-slate-900">{profile.propertyName || 'No properties linked'}</p>
          </div>

          <div>
            <p className="text-xs font-bold uppercase text-slate-500">Managing Agency Branch</p>
            <p className="mt-1 text-sm text-slate-700">{profile.branchName || 'Not available'}</p>
          </div>

          <div>
            <p className="text-xs font-bold uppercase text-slate-500">Agreement Type</p>
            <p className="mt-1 text-sm text-slate-700">Not available in the connected profile data.</p>
          </div>

          <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-3.5 text-xs text-emerald-900">
            <p className="font-bold">Tax and remittance details</p>
            <p className="mt-1 text-emerald-800">
              Tax and remittance settings are not included in the connected profile data.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
