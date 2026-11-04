'use client';

import { FormEvent, useEffect, useState } from 'react';
import { Megaphone, Send } from 'lucide-react';

type Announcement = {
  id: string;
  title: string;
  body: string;
  property_id: number | null;
  expires_at: string | null;
  status: string;
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
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [title, setTitle] = useState('');
  const [message, setMessage] = useState('');
  const [expiresOn, setExpiresOn] = useState(nairobiDateAfter(7));
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const loadData = async () => {
    setLoading(true);
    setError('');
    try {
      const announcementResponse = await fetch('/api/announcements');
      const announcementData = await announcementResponse.json();
      if (!announcementResponse.ok) throw new Error(announcementData.error || 'Unable to load announcements.');
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
          expiresAt: `${expiresOn}T23:59:59+03:00`,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to publish announcement.');
      setTitle('');
      setMessage('');
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
        <p className="mt-1 text-slate-500">Send a notice to all tenants in your assigned property.</p>
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
        <div>
          <label className="block text-sm font-semibold text-slate-700">
            Expiry date
            <input type="date" required min={nairobiDateAfter(0)} value={expiresOn} onChange={(event) => setExpiresOn(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
          </label>
        </div>
        <button type="submit" disabled={saving || loading || !title.trim() || !message.trim()} className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-5 py-2.5 font-bold text-white hover:bg-emerald-700 disabled:opacity-60">
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
                      <p className="mt-2 whitespace-pre-wrap text-sm text-slate-700">{announcement.body}</p>
                      <p className="mt-3 text-xs text-slate-500">
                        {announcement.property_id ? 'Property notice' : 'Branch notice'}
                        {announcement.expires_at && ` · Expires ${new Date(announcement.expires_at).toLocaleDateString('en-KE')}`}
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
