'use client';

import { useEffect, useState } from 'react';
import { UserPlus, Trash2, CheckCircle, Clock, Shield, RefreshCw, Mail } from 'lucide-react';
import { Pagination } from '../../../components/ui/Pagination';

type Invite = {
  id: number;
  email: string | null;
  expires_at: string;
  status: string;
  used_at: string | null;
  property_id: number;
  property_name: string;
};

type Caretaker = {
  id: number;
  full_name: string;
  email: string;
  phone: string;
  created_at: string;
  property_id: number;
  property_name: string;
};

type PropertyOption = { id: number; name: string };

export default function StaffPage() {
  const [invites, setInvites] = useState<Invite[]>([]);
  const [caretakers, setCaretakers] = useState<Caretaker[]>([]);
  const [properties, setProperties] = useState<PropertyOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [userId, setUserId] = useState<string | null>(null);
  const [selectedPropertyId, setSelectedPropertyId] = useState('');
  const [inviteEmail, setInviteEmail] = useState('');
  const [activationLink, setActivationLink] = useState('');
  const [toast, setToast] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);

  // Pagination states
  const [caretakersPage, setCaretakersPage] = useState(1);
  const [invitesPage, setInvitesPage] = useState(1);
  const ITEMS_PER_PAGE = 10;

  const showToast = (type: 'success' | 'error', msg: string) => {
    setToast({ type, msg });
    setTimeout(() => setToast(null), 4000);
  };

  useEffect(() => {
    void fetch('/api/auth/me')
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok || !payload.user?.id) {
          throw new Error(payload.error || 'Sign in as a landlord to manage caretakers.');
        }
        setUserId(String(payload.user.id));
        await loadData();
      })
      .catch((error: unknown) => {
        showToast('error', error instanceof Error ? error.message : 'Unable to verify your account.');
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (userId && selectedPropertyId) void loadData();
  }, [selectedPropertyId]);

  const loadData = async () => {
    setLoading(true);
    try {
      const query = selectedPropertyId ? `?propertyId=${encodeURIComponent(selectedPropertyId)}` : '';
      const invitesResponse = await fetch(`/api/auth/caretaker-invite${query}`);
      const inviteData = await invitesResponse.json();
      if (!invitesResponse.ok) throw new Error(inviteData.error || 'Unable to load caretaker data.');
      setInvites(inviteData.invites || []);
      setCaretakers(inviteData.caretakers || []);
      setProperties(inviteData.properties || []);
      if (!selectedPropertyId && inviteData.properties?.length) {
        setSelectedPropertyId(String(inviteData.properties[0].id));
      }
    } catch (err) {
      showToast('error', err instanceof Error ? err.message : 'Unable to load staff data.');
    } finally {
      setLoading(false);
    }
  };

  const handleGenerate = async () => {
    if (!userId) return;
    if (!selectedPropertyId) {
      showToast('error', 'Select a property before inviting a caretaker.');
      return;
    }
    if (!inviteEmail.trim()) {
      showToast('error', 'Enter the caretaker’s email address.');
      return;
    }
    setGenerating(true);
    try {
      const res = await fetch('/api/auth/caretaker-invite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: inviteEmail, propertyId: Number(selectedPropertyId) }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      setActivationLink(`${window.location.origin}/caretaker-signup?token=${encodeURIComponent(data.activation_token)}`);
      showToast('success', `Invitation link created for ${inviteEmail}. Copy and share it with the caretaker.`);
      setInviteEmail('');
      await loadData();
    } catch (err: any) {
      showToast('error', err.message || 'Failed to send activation email.');
    } finally {
      setGenerating(false);
    }
  };

  const handleResend = async (inviteId: number) => {
    if (!userId) return;
    try {
      const res = await fetch('/api/auth/caretaker-invite', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inviteId }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      setActivationLink(`${window.location.origin}/caretaker-signup?token=${encodeURIComponent(data.activation_token)}`);
      showToast('success', 'A new activation link is ready to copy and share.');
    } catch (err) {
      showToast('error', err instanceof Error ? err.message : 'Failed to resend activation email.');
    }
  };

  const handleRemoveCaretaker = async (caretakerId: number, propertyId: number, name: string) => {
    if (!userId) return;
    if (!window.confirm(`Remove ${name}'s caretaker access to this property? Their account will remain available for other properties.`)) return;
    try {
      const res = await fetch('/api/auth/caretaker-invite', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caretakerId, propertyId }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      showToast('success', `${name} has been removed.`);
      await loadData();
    } catch (err: any) {
      showToast('error', err.message || 'Failed to remove caretaker.');
    }
  };

  const statusBadge = (status: string) => {
    if (status === 'active') return 'bg-emerald-100 text-emerald-700';
    if (status === 'used') return 'bg-blue-100 text-blue-700';
    if (status === 'expired' || status === 'revoked') return 'bg-red-100 text-red-700';
    return 'bg-slate-100 text-slate-500';
  };

  return (
    <div className="space-y-10">
      {/* Toast */}
      {toast && (
        <div className={`fixed top-6 right-6 z-50 flex items-center gap-3 px-5 py-3 rounded-xl shadow-lg text-white text-sm font-medium ${
          toast.type === 'success' ? 'bg-emerald-600' : 'bg-red-500'
        }`}>
          <CheckCircle size={18} />
          {toast.msg}
        </div>
      )}

      <div className="flex justify-between items-start">
        <div>
          <h1 className="text-3xl font-bold text-slate-900">Staff & Caretakers</h1>
          <p className="text-slate-500 mt-1">Manage property access and create secure caretaker activation links.</p>
        </div>
        <form onSubmit={(event) => { event.preventDefault(); void handleGenerate(); }} className="flex items-center gap-2">
          <select
            value={selectedPropertyId}
            onChange={(event) => setSelectedPropertyId(event.target.value)}
            required
            aria-label="Property to invite caretaker to"
            className="border border-slate-300 rounded-xl px-3 py-2.5 text-sm"
          >
            {properties.map((property) => (
              <option key={property.id} value={property.id}>{property.name}</option>
            ))}
          </select>
          <input
            type="email"
            value={inviteEmail}
            onChange={(event) => setInviteEmail(event.target.value)}
            placeholder="caretaker@example.com"
            required
            className="border border-slate-300 rounded-xl px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-emerald-500"
            aria-label="Caretaker email address"
          />
          <button
            type="submit"
            disabled={generating}
            className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white px-5 py-2.5 rounded-xl font-bold transition-colors disabled:opacity-60"
          >
            <UserPlus size={18} /> {generating ? 'Creating...' : 'Invite caretaker'}
          </button>
        </form>
      </div>

      {activationLink && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
          <p className="font-semibold text-emerald-900">Share this one-time activation link with the caretaker</p>
          <div className="mt-2 flex gap-2">
            <input
              readOnly
              value={activationLink}
              aria-label="Caretaker activation link"
              className="min-w-0 flex-1 rounded-lg border border-emerald-200 bg-white px-3 py-2 text-sm text-slate-700"
            />
            <button
              type="button"
              onClick={() => void navigator.clipboard.writeText(activationLink)}
              className="rounded-lg bg-emerald-700 px-4 py-2 text-sm font-semibold text-white"
            >
              Copy link
            </button>
          </div>
          <p className="mt-2 text-xs text-emerald-800">The link expires in 48 hours and is shown only when created or regenerated.</p>
        </div>
      )}

      {/* How it works */}
      <div className="bg-slate-50 border border-slate-200 rounded-xl p-5 text-sm text-slate-600 space-y-2">
        <p className="font-bold text-slate-800">How to add a caretaker:</p>
        <ol className="list-decimal list-inside space-y-1">
          <li>Select the property, enter the caretaker’s email address, and create an invitation link.</li>
          <li>They follow the secure activation link to set their name, phone number, and password.</li>
          <li>Invitations expire after <strong>48 hours</strong>.</li>
          <li>Copy and share the generated link securely; it is not emailed automatically.</li>
        </ol>
      </div>

      {/* Active Caretakers */}
      <div>
        <h2 className="text-xl font-bold text-slate-800 mb-4 flex items-center gap-2"><Shield size={20} className="text-emerald-600" /> Active Caretakers</h2>
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
          {loading ? (
            <div className="p-8 text-center text-slate-500 animate-pulse">Loading...</div>
          ) : caretakers.length === 0 ? (
            <div className="p-8 text-center text-slate-400">No caretakers yet. Send an invitation to get started.</div>
          ) : (
            <table className="w-full text-sm text-left">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th className="p-4 font-medium text-slate-500">Name</th>
                  <th className="p-4 font-medium text-slate-500">Email</th>
                  <th className="p-4 font-medium text-slate-500">Phone</th>
                  <th className="p-4 font-medium text-slate-500">Joined</th>
                  <th className="p-4 font-medium text-slate-500 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {caretakers.slice((caretakersPage - 1) * ITEMS_PER_PAGE, caretakersPage * ITEMS_PER_PAGE).map((c) => (
                  <tr key={c.id} className="hover:bg-slate-50">
                    <td className="p-4 font-bold text-slate-900">{c.full_name}</td>
                    <td className="p-4 text-slate-600">{c.email}</td>
                    <td className="p-4 text-slate-600">{c.phone}</td>
                    <td className="p-4 text-slate-400 text-xs">{new Date(c.created_at).toLocaleDateString('en-KE', { month: 'short', day: 'numeric', year: 'numeric' })}</td>
                    <td className="p-4 text-right">
                      <button
                        onClick={() => handleRemoveCaretaker(c.id, c.property_id, c.full_name)}
                        className="p-1.5 text-red-500 hover:bg-red-50 rounded-lg border border-red-200 transition-colors"
                        title={`Remove caretaker from ${c.property_name}`}
                      >
                        <Trash2 size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {caretakers.length > 0 && (
            <Pagination
              currentPage={caretakersPage}
              totalPages={Math.ceil(caretakers.length / ITEMS_PER_PAGE)}
              onPageChange={setCaretakersPage}
            />
          )}
        </div>
      </div>

      <section className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-5">
        <h2 className="text-xl font-bold text-slate-800">Other Property Staff</h2>
        <p className="text-sm text-slate-600">
          The watchman and on-site staff roster remains on a separate property-data workflow and is not available during this account migration. Caretaker portal access above is managed through Django.
        </p>
      </section>

      {/* Caretaker invitation history */}
      <div>
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-bold text-slate-800 flex items-center gap-2"><Clock size={20} className="text-slate-500" /> Caretaker Invitations</h2>
          {userId && (
            <button onClick={() => void loadData()} className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 transition-colors">
              <RefreshCw size={14} /> Refresh
            </button>
          )}
        </div>
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
          {invites.length === 0 ? (
            <div className="p-8 text-center text-slate-400">No caretaker invitations yet.</div>
          ) : (
            <table className="w-full text-sm text-left">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th className="p-4 font-medium text-slate-500">Email address</th>
                  <th className="p-4 font-medium text-slate-500">Expires</th>
                  <th className="p-4 font-medium text-slate-500 text-center">Status</th>
                  <th className="p-4 font-medium text-slate-500 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {invites.slice((invitesPage - 1) * ITEMS_PER_PAGE, invitesPage * ITEMS_PER_PAGE).map((inv) => (
                  <tr key={inv.id} className="hover:bg-slate-50">
                    <td className="p-4 font-medium text-slate-900">{inv.email || 'Legacy code invitation'}</td>
                    <td className="p-4 text-slate-500 text-xs">{new Date(inv.expires_at).toLocaleString('en-KE', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</td>
                    <td className="p-4 text-center">
                      <span className={`px-2.5 py-1 text-xs font-bold rounded-full uppercase ${statusBadge(inv.status)}`}>
                        {inv.status}
                      </span>
                    </td>
                    <td className="p-4 text-right">
                      {inv.status === 'active' && inv.email && (
                        <button
                          onClick={() => void handleResend(inv.id)}
                          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-slate-600 hover:bg-slate-100 rounded-lg border border-slate-200 transition-colors"
                          title="Regenerate activation link"
                        >
                          <Mail size={15} /> Regenerate link
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {invites.length > 0 && (
            <Pagination
              currentPage={invitesPage}
              totalPages={Math.ceil(invites.length / ITEMS_PER_PAGE)}
              onPageChange={setInvitesPage}
            />
          )}
        </div>
      </div>
    </div>
  );
}
