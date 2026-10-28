'use client';

import { FormEvent, useEffect, useState } from 'react';
import { Megaphone, Send } from 'lucide-react';

type UnitOption = { id: string; unitCode: string };
type Announcement = {
  id: string;
  title: string;
  message: string;
  audience: 'property' | 'units';
  expires_at: string;
  created_at: string;
  targets: { unitId: string; unitCode: string }[];
};

function nairobiDateAfter(days: number) {
  const date = new Date(Date.now() + days * 24 * 60 * 60 * 1000);
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Nairobi',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(date);
}

export default function CaretakerAnnouncementsPage() {
  const [units, setUnits] = useState<UnitOption[]>([]);
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [title, setTitle] = useState('');
  const [message, setMessage] = useState('');
  const [audience, setAudience] = useState<'property' | 'units'>('property');
  const [selectedUnitIds, setSelectedUnitIds] = useState<string[]>([]);
  const [expiresOn, setExpiresOn] = useState(nairobiDateAfter(7));
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const loadData = async () => {
    setLoading(true);
    setError('');
    try {
      const [unitResponse, announcementResponse] = await Promise.all([
        fetch('/api/caretaker/units'),
        fetch('/api/announcements'),
      ]);
      const [unitData, announcementData] = await Promise.all([unitResponse.json(), announcementResponse.json()]);
      if (!unitResponse.ok) throw new Error(unitData.error || 'Unable to load property units.');
      if (!announcementResponse.ok) throw new Error(announcementData.error || 'Unable to load announcements.');
      setUnits((unitData.units || []).map((unit: { id: string; unitCode: string }) => ({ id: unit.id, unitCode: unit.unitCode })));
      setAnnouncements(announcementData.announcements || []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Unable to load announcements.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const toggleUnit = (unitId: string) => {
    setSelectedUnitIds((current) => current.includes(unitId)
      ? current.filter((id) => id !== unitId)
      : [...current, unitId]);
  };

  const createAnnouncement = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError('');
    setSuccess('');
    try {
      const response = await fetch('/api/announcements', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title,
          message,
          audience,
          unitIds: audience === 'units' ? selectedUnitIds : [],
          expiresAt: `${expiresOn}T23:59:59+03:00`,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to publish announcement.');
      setTitle('');
      setMessage('');
      setAudience('property');
      setSelectedUnitIds([]);
      setSuccess('Announcement published to the selected tenants.');
      await loadData();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Unable to publish announcement.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold text-slate-900">Tenant Announcements</h1>
        <p className="mt-1 text-slate-500">Send a notice to everyone or only selected units. Notices disappear after their expiry date.</p>
      </header>

      {error && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</div>}
      {success && <div role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">{success}</div>}

      <form onSubmit={createAnnouncement} className="space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <label className="block text-sm font-semibold text-slate-700">
          Title
          <input required maxLength={120} value={title} onChange={(event) => setTitle(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
        </label>
        <label className="block text-sm font-semibold text-slate-700">
          Message
          <textarea required maxLength={4000} rows={4} value={message} onChange={(event) => setMessage(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
        </label>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm font-semibold text-slate-700">
            Audience
            <select value={audience} onChange={(event) => setAudience(event.target.value as 'property' | 'units')} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
              <option value="property">Entire property</option>
              <option value="units">Selected units</option>
            </select>
          </label>
          <label className="block text-sm font-semibold text-slate-700">
            Expiry date
            <input type="date" required min={nairobiDateAfter(0)} value={expiresOn} onChange={(event) => setExpiresOn(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
          </label>
        </div>
        {audience === 'units' && (
          <fieldset className="rounded-lg border border-slate-200 p-4">
            <legend className="px-1 text-sm font-semibold text-slate-700">Select units</legend>
            <div className="grid max-h-48 grid-cols-2 gap-2 overflow-y-auto sm:grid-cols-3">
              {units.map((unit) => (
                <label key={unit.id} className="flex items-center gap-2 rounded-md px-2 py-1 text-sm text-slate-700 hover:bg-slate-50">
                  <input type="checkbox" checked={selectedUnitIds.includes(unit.id)} onChange={() => toggleUnit(unit.id)} />
                  {unit.unitCode}
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <button type="submit" disabled={saving || loading || !title.trim() || !message.trim() || (audience === 'units' && selectedUnitIds.length === 0)} className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-5 py-2.5 font-bold text-white hover:bg-emerald-700 disabled:opacity-60">
          <Send size={16} /> {saving ? 'Publishing...' : 'Publish Announcement'}
        </button>
      </form>

      <section className="space-y-3">
        <h2 className="text-xl font-bold text-slate-900">Active announcements</h2>
        {loading ? <p className="text-sm text-slate-500">Loading announcements...</p>
          : announcements.length === 0 ? <p className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-500">No active announcements.</p>
            : announcements.map((announcement) => (
              <article key={announcement.id} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-start gap-3">
                    <Megaphone size={20} className="mt-1 shrink-0 text-emerald-600" />
                    <div>
                      <h3 className="font-bold text-slate-900">{announcement.title}</h3>
                      <p className="mt-2 whitespace-pre-wrap text-sm text-slate-700">{announcement.message}</p>
                      <p className="mt-3 text-xs text-slate-500">
                        {announcement.audience === 'property'
                          ? 'Entire property'
                          : `Units: ${announcement.targets.map((unit) => unit.unitCode).join(', ')}`}
                        {' · '}Expires {new Date(announcement.expires_at).toLocaleDateString('en-KE')}
                      </p>
                    </div>
                  </div>
                </div>
              </article>
            ))}
      </section>
    </div>
  );
}
