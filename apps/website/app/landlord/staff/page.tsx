'use client';

import { useEffect, useState } from 'react';
import { UserPlus, Trash2, CheckCircle, Clock, Shield, RefreshCw, Mail } from 'lucide-react';
import { createClient } from '../../../lib/supabase/client';
import { Pagination } from '../../../components/ui/Pagination';

type Invite = {
  id: string;
  email: string | null;
  expires_at: string;
  status: string;
  used_at: string | null;
};

type Caretaker = {
  id: string;
  full_name: string;
  email: string;
  phone: string;
  created_at: string;
};

type PropertyStaff = {
  id: string;
  full_name: string;
  email: string | null;
  phone: string | null;
  staff_role: string;
  created_at: string;
};

export default function StaffPage() {
  const [invites, setInvites] = useState<Invite[]>([]);
  const [caretakers, setCaretakers] = useState<Caretaker[]>([]);
  const [propertyStaff, setPropertyStaff] = useState<PropertyStaff[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [addingStaff, setAddingStaff] = useState(false);
  const [staffForm, setStaffForm] = useState({ fullName: '', email: '', phone: '', staffRole: 'Watchman' });
  const [userId, setUserId] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState('');
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
    const supabase = createClient();
    supabase.auth.getUser().then(({ data }) => {
      if (data.user?.id) {
        setUserId(data.user.id);
        void loadData();
      }
    });
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [invitesResponse, staffResponse] = await Promise.all([
        fetch('/api/auth/caretaker-invite'),
        fetch('/api/property-staff'),
      ]);
      const [inviteData, staffData] = await Promise.all([invitesResponse.json(), staffResponse.json()]);
      if (!invitesResponse.ok) throw new Error(inviteData.error || 'Unable to load caretaker data.');
      if (!staffResponse.ok) throw new Error(staffData.error || 'Unable to load other staff.');
      setInvites(inviteData.invites || []);
      setCaretakers(inviteData.caretakers || []);
      setPropertyStaff(staffData.staff || []);
    } catch (err) {
      showToast('error', err instanceof Error ? err.message : 'Unable to load staff data.');
    } finally {
      setLoading(false);
    }
  };

  const handleGenerate = async () => {
    if (!userId) return;
    if (!inviteEmail.trim()) {
      showToast('error', 'Enter the caretaker’s email address.');
      return;
    }
    setGenerating(true);
    try {
      const res = await fetch('/api/auth/caretaker-invite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: inviteEmail }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      showToast('success', `Activation email sent to ${data.email}.`);
      setInviteEmail('');
      await loadData();
    } catch (err: any) {
      showToast('error', err.message || 'Failed to send activation email.');
    } finally {
      setGenerating(false);
    }
  };

  const handleAddStaff = async (event: React.FormEvent) => {
    event.preventDefault();
    setAddingStaff(true);
    try {
      const res = await fetch('/api/property-staff', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(staffForm),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      setStaffForm({ fullName: '', email: '', phone: '', staffRole: 'Watchman' });
      showToast('success', `${data.staff.full_name} added to the property staff roster.`);
      await loadData();
    } catch (err) {
      showToast('error', err instanceof Error ? err.message : 'Failed to add staff member.');
    } finally {
      setAddingStaff(false);
    }
  };

  const handleRemoveStaff = async (staff: PropertyStaff) => {
    if (!window.confirm(`Remove ${staff.full_name} from this property’s staff roster?`)) return;
    try {
      const res = await fetch('/api/property-staff', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ staffId: staff.id }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      showToast('success', `${staff.full_name} removed.`);
      await loadData();
    } catch (err) {
      showToast('error', err instanceof Error ? err.message : 'Failed to remove staff member.');
    }
  };

  const handleResend = async (inviteId: string) => {
    if (!userId) return;
    try {
      const res = await fetch('/api/auth/caretaker-invite', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inviteId }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      showToast('success', data.message);
    } catch (err) {
      showToast('error', err instanceof Error ? err.message : 'Failed to resend activation email.');
    }
  };

  const handleRemoveCaretaker = async (caretakerId: string, name: string) => {
    if (!userId) return;
    if (!window.confirm(`Remove ${name}'s caretaker access to this property? Their account will remain available for other properties.`)) return;
    try {
      const res = await fetch('/api/auth/caretaker-invite', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caretakerId, landlordId: userId }),
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
          <p className="text-slate-500 mt-1">Manage your caretaker team and send secure activation links.</p>
        </div>
        <form onSubmit={(event) => { event.preventDefault(); void handleGenerate(); }} className="flex items-center gap-2">
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
            <UserPlus size={18} /> {generating ? 'Sending...' : 'Invite caretaker'}
          </button>
        </form>
      </div>

      {/* How it works */}
      <div className="bg-slate-50 border border-slate-200 rounded-xl p-5 text-sm text-slate-600 space-y-2">
        <p className="font-bold text-slate-800">How to add a caretaker:</p>
        <ol className="list-decimal list-inside space-y-1">
          <li>Enter the caretaker’s email address and send the invitation.</li>
          <li>They follow the secure activation link to set their name, phone number, and password.</li>
          <li>Invitations expire after <strong>48 hours</strong>.</li>
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
                        onClick={() => handleRemoveCaretaker(c.id, c.full_name)}
                        className="p-1.5 text-red-500 hover:bg-red-50 rounded-lg border border-red-200 transition-colors"
                        title="Remove caretaker"
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

      <section className="space-y-4">
        <div>
          <h2 className="text-xl font-bold text-slate-800">Other Property Staff</h2>
          <p className="text-sm text-slate-500 mt-1">Record watchmen and other on-site staff here. These are roster entries, not portal accounts.</p>
        </div>
        <form onSubmit={handleAddStaff} className="grid grid-cols-1 md:grid-cols-5 gap-3 bg-white p-4 rounded-xl border border-slate-200">
          <input required value={staffForm.fullName} onChange={(event) => setStaffForm({ ...staffForm, fullName: event.target.value })} placeholder="Full name" aria-label="Staff full name" className="border border-slate-300 rounded-lg px-3 py-2 text-sm" />
          <select value={staffForm.staffRole} onChange={(event) => setStaffForm({ ...staffForm, staffRole: event.target.value })} aria-label="Staff role" className="border border-slate-300 rounded-lg px-3 py-2 text-sm">
            <option>Watchman</option><option>Cleaner</option><option>Maintenance technician</option><option>Other</option>
          </select>
          <input type="email" value={staffForm.email} onChange={(event) => setStaffForm({ ...staffForm, email: event.target.value })} placeholder="Email (optional)" aria-label="Staff email" className="border border-slate-300 rounded-lg px-3 py-2 text-sm" />
          <input type="tel" value={staffForm.phone} onChange={(event) => setStaffForm({ ...staffForm, phone: event.target.value })} placeholder="Phone (required if no email)" aria-label="Staff phone" className="border border-slate-300 rounded-lg px-3 py-2 text-sm" />
          <button disabled={addingStaff} className="inline-flex items-center justify-center gap-2 bg-slate-900 hover:bg-slate-800 disabled:opacity-60 text-white rounded-lg px-3 py-2 font-semibold text-sm">
            <UserPlus size={16} /> {addingStaff ? 'Adding…' : 'Add to roster'}
          </button>
        </form>
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
          {propertyStaff.length === 0 ? (
            <p className="p-6 text-center text-slate-400">No other staff members have been added.</p>
          ) : (
            <table className="w-full text-sm text-left">
              <thead className="bg-slate-50 border-b border-slate-200"><tr>
                <th className="p-4 font-medium text-slate-500">Name</th><th className="p-4 font-medium text-slate-500">Role</th><th className="p-4 font-medium text-slate-500">Contact</th><th className="p-4 text-right font-medium text-slate-500">Action</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100">
                {propertyStaff.map((staff) => <tr key={staff.id}>
                  <td className="p-4 font-semibold text-slate-900">{staff.full_name}</td>
                  <td className="p-4 text-slate-600">{staff.staff_role}</td>
                  <td className="p-4 text-slate-600">{[staff.email, staff.phone].filter(Boolean).join(' · ')}</td>
                  <td className="p-4 text-right"><button onClick={() => void handleRemoveStaff(staff)} className="p-1.5 text-red-500 hover:bg-red-50 rounded-lg border border-red-200" aria-label={`Remove ${staff.full_name}`}><Trash2 size={16} /></button></td>
                </tr>)}
              </tbody>
            </table>
          )}
        </div>
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
                          title="Resend activation email"
                        >
                          <Mail size={15} /> Resend
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
