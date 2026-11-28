'use client';

import { useEffect, useMemo, useState, type FormEvent } from 'react';
import {
  Pencil,
  RefreshCw,
  ShieldCheck,
  UserCheck,
  UserPlus,
  UserRoundX,
} from 'lucide-react';
import ExistingAccountPicker, {
  type ExistingAccount,
} from '../property-officer/ExistingAccountPicker';
import { PortalLoading, PortalPagination, usePortalPagination } from '../property-officer/PortalUI';

type Branch = {
  id: number;
  name: string;
  town: string;
};

type StaffMember = {
  membership_id: number;
  user_id: number;
  name: string;
  email: string;
  phone: string | null;
  roles: string[];
  pending_roles?: string[];
  pending_branch_ids?: number[];
  branches: Branch[];
  active: boolean;
  account_active: boolean;
  status: 'active' | 'pending_activation' | 'suspended';
};

type StaffResponse = {
  branches: Branch[];
  roles: string[];
  staff: StaffMember[];
};

function branchDisplayName(branch: Branch) {
  const searchText = `${branch.name} ${branch.town}`;
  if (/\bnairobi\b/i.test(searchText)) return 'Nairobi';
  if (/\bnakuru\b/i.test(searchText)) return 'Nakuru';
  return branch.name || branch.town;
}

const EMPTY_FORM = {
  name: '',
  email: '',
  phone: '',
  idOrRegistrationNumber: '',
  roles: [] as string[],
  branchIds: [] as number[],
};

async function getError(response: Response, fallback: string) {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || body.error || fallback);
  }
  return body;
}

export default function StaffManagement({
  selectedAgencyId,
}: {
  selectedAgencyId: number | null;
}) {
  const [agencyId, setAgencyId] = useState(
    selectedAgencyId === null ? '' : String(selectedAgencyId),
  );
  const [branches, setBranches] = useState<Branch[]>([]);
  const [roles, setRoles] = useState<string[]>([]);
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [editRoles, setEditRoles] = useState<string[]>([]);
  const [editBranchIds, setEditBranchIds] = useState<number[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [roleFilter, setRoleFilter] = useState('all');
  const [branchFilter, setBranchFilter] = useState('all');

  useEffect(() => {
    setAgencyId(selectedAgencyId === null ? '' : String(selectedAgencyId));
    setSelectedAccountId(null);
    setForm(EMPTY_FORM);
  }, [selectedAgencyId]);

  async function loadStaff(selectedAgencyId: string) {
    setLoading(true);
    setError('');
    try {
      const response = await fetch(
        `/api/director/staff?agency_id=${encodeURIComponent(selectedAgencyId)}`,
        { cache: 'no-store' },
      );
      const payload = await getError(response, 'Unable to load agency staff.');
      const result = payload as StaffResponse;
      setBranches(result.branches);
      setRoles(result.roles);
      setStaff(result.staff);
      if (!form.roles.length && result.roles.length > 0) {
        setForm((current) => ({ ...current, roles: [result.roles[0]] }));
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load agency staff.');
      setStaff([]);
      setBranches([]);
      setRoles([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (agencyId) {
      void loadStaff(agencyId);
    } else {
      setLoading(false);
      setStaff([]);
      setBranches([]);
      setRoles([]);
    }
  }, [agencyId]);

  async function csrfToken() {
    const response = await fetch('/api/auth/csrf', { cache: 'no-store' });
    const payload = await getError(response, 'Unable to prepare a secure request.');
    return payload.csrfToken as string;
  }

  function toggleBranch(current: number[], branchId: number) {
    return current.includes(branchId)
      ? current.filter((id) => id !== branchId)
      : [...current, branchId];
  }

  async function createStaff(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!agencyId) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const token = await csrfToken();
      const response = await fetch('/api/director/staff', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
        body: JSON.stringify({
          agency_id: Number(agencyId),
          name: form.name,
          email: form.email,
          phone: form.phone,
          id_or_registration_number: form.idOrRegistrationNumber,
          existing_user_id: selectedAccountId,
          roles: form.roles,
          branch_ids: form.branchIds,
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        if (payload.staff) {
          setNotice(payload.detail || 'Staff access was created; resend activation when email is available.');
          await loadStaff(agencyId);
          setShowCreate(false);
        } else {
          throw new Error(payload.detail || payload.error || 'Unable to create the staff account.');
        }
        return;
      }
      setNotice(
        payload.existing_account_link
          ? payload.role_notification_sms_queued
            ? 'Existing agency account linked. The selected roles are active, and an SMS notification was queued.'
            : 'Existing agency account linked. The selected staff roles are active immediately.'
          : payload.activation_email_sent
            ? 'Staff account created. An activation code was sent by email.'
            : 'Existing account added to this agency.',
      );
      setForm({ ...EMPTY_FORM, roles: roles[0] ? [roles[0]] : [] });
      setSelectedAccountId(null);
      setShowCreate(false);
      await loadStaff(agencyId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to create the staff account.');
    } finally {
      setBusy(false);
    }
  }

  async function updateStaff(member: StaffMember, changes: { roles: string[]; branchIds: number[]; active: boolean }) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const token = await csrfToken();
      const response = await fetch(`/api/director/staff/${member.membership_id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
        body: JSON.stringify({
          roles: changes.roles,
          branch_ids: changes.branchIds,
          active: changes.active,
        }),
      });
      const payload = await getError(response, 'Unable to update the staff account.');
      setEditing(null);
      setNotice(
        payload.role_notification_sms_queued
          ? 'Staff access updated, and an SMS notification was queued for the new role.'
          : 'Staff access updated.',
      );
      await loadStaff(agencyId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to update the staff account.');
    } finally {
      setBusy(false);
    }
  }

  async function suspendStaff(member: StaffMember) {
    if (!window.confirm(`Suspend ${member.name}'s access to this agency?`)) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const token = await csrfToken();
      const response = await fetch(`/api/director/staff/${member.membership_id}`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': token },
      });
      await getError(response, 'Unable to suspend this staff account.');
      setNotice('Staff access suspended for this agency.');
      await loadStaff(agencyId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to suspend this staff account.');
    } finally {
      setBusy(false);
    }
  }

  async function resendActivation(member: StaffMember) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const token = await csrfToken();
      const response = await fetch(
        `/api/director/staff/${member.membership_id}/resend-activation`,
        { method: 'POST', headers: { 'X-CSRFToken': token } },
      );
      await getError(response, 'Unable to resend the activation email.');
      setNotice(`A new activation code was sent to ${member.email}.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to resend the activation email.');
    } finally {
      setBusy(false);
    }
  }

  function startEditing(member: StaffMember) {
    setEditing(member.membership_id);
    setEditRoles(member.roles.length ? member.roles : (roles[0] ? [roles[0]] : []));
    setEditBranchIds(member.branches.map((branch) => branch.id));
  }

  const filteredStaff = useMemo(() => {
    const query = search.trim().toLowerCase();
    return staff.filter((member) => {
      const matchesSearch = !query || [
        member.name,
        member.email,
        member.phone || '',
        member.roles.join(' '),
        member.branches.map(branchDisplayName).join(' '),
      ].join(' ').toLowerCase().includes(query);
      const matchesStatus = statusFilter === 'all' || member.status === statusFilter;
      const matchesRole = roleFilter === 'all'
        || member.roles.includes(roleFilter)
        || (member.pending_roles ?? []).includes(roleFilter);
      const matchesBranch = branchFilter === 'all'
        || member.branches.some((branch) => String(branch.id) === branchFilter)
        || (member.pending_branch_ids ?? []).some((id) => String(id) === branchFilter);
      return matchesSearch && matchesStatus && matchesRole && matchesBranch;
    });
  }, [branchFilter, roleFilter, search, staff, statusFilter]);
  const pagination = usePortalPagination(filteredStaff);

  return (
    <section className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-bold text-slate-900">Agency staff accounts</h2>
          <p className="mt-1 text-xs text-slate-500">
            Add staff, assign one or more agency roles and branch scope, and suspend agency access.
            Finance and branch-manager roles require authenticator MFA at sign-in.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setShowCreate((current) => !current)}
            className="inline-flex items-center gap-2 rounded-xl bg-emerald-800 px-3.5 py-2 text-sm font-bold text-white hover:bg-emerald-900"
          >
            <UserPlus size={16} /> Add staff
          </button>
        </div>
      </div>

      {error && <p role="alert" className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</p>}
      {notice && <p role="status" className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">{notice}</p>}

      {showCreate && (
        <form onSubmit={createStaff} className="mt-5 rounded-2xl border border-emerald-200 bg-emerald-50/40 p-4">
          <h3 className="font-bold text-slate-900">Invite staff member</h3>
          {agencyId && (
            <div className="mt-4">
              <ExistingAccountPicker
                agencyId={Number(agencyId)}
                selectedId={selectedAccountId}
                onSelect={(account: ExistingAccount | null) => {
                  setSelectedAccountId(account?.id ?? null);
                  if (account) {
                    setForm((current) => ({
                      ...current,
                      name: account.name,
                      email: account.email,
                      phone: account.phone,
                      idOrRegistrationNumber: account.id_or_registration_number || current.idOrRegistrationNumber,
                    }));
                  }
                }}
              />
            </div>
          )}
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="text-xs font-bold text-slate-700">
              Full name
              <input required maxLength={255} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} readOnly={selectedAccountId !== null} className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-normal" />
            </label>
            <label className="text-xs font-bold text-slate-700">
              Email
              <input required type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} readOnly={selectedAccountId !== null} className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-normal" />
            </label>
            <label className="text-xs font-bold text-slate-700">
              Kenyan phone number
              <input required value={form.phone} onChange={(event) => setForm({ ...form, phone: event.target.value })} placeholder="2547XXXXXXXX" readOnly={selectedAccountId !== null} className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-normal" />
            </label>
            <label className="text-xs font-bold text-slate-700">
              National ID / passport
              <input required maxLength={100} value={form.idOrRegistrationNumber} onChange={(event) => setForm({ ...form, idOrRegistrationNumber: event.target.value })} className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-normal" />
            </label>
          </div>
          <fieldset className="mt-4">
            <legend className="text-xs font-bold text-slate-700">Agency roles</legend>
            <div className="mt-2 flex flex-wrap gap-2">
              {roles.map((role) => (
                <label key={role} className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs">
                  <input
                    type="checkbox"
                    checked={form.roles.includes(role)}
                    onChange={() => setForm((current) => ({
                      ...current,
                      roles: current.roles.includes(role)
                        ? current.roles.filter((item) => item !== role)
                        : [...current.roles, role],
                    }))}
                  />
                  {role}
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset className="mt-4">
            <legend className="text-xs font-bold text-slate-700">Branch access</legend>
            <div className="mt-2 flex flex-wrap gap-2">
              {branches.map((branch) => (
                <label key={branch.id} className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs">
                  <input
                    type="checkbox"
                    checked={form.branchIds.includes(branch.id)}
                    onChange={() => setForm((current) => ({
                      ...current,
                      branchIds: toggleBranch(current.branchIds, branch.id),
                    }))}
                  />
                  {branchDisplayName(branch)}
                </label>
              ))}
              {branches.length === 0 && (
                <p className="text-xs text-slate-500">
                  {loading ? 'Loading agency branches…' : 'No branches are available for this agency.'}
                </p>
              )}
            </div>
          </fieldset>
          <div className="mt-4 flex gap-2">
            <button type="submit" disabled={busy || !form.branchIds.length || !form.roles.length} className="rounded-lg bg-emerald-800 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">
              {busy ? 'Sending…' : 'Create and send activation'}
            </button>
            <button type="button" onClick={() => setShowCreate(false)} className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold">Cancel</button>
          </div>
        </form>
      )}

      <div className="mt-5 grid gap-3 rounded-2xl border border-[#e7ebe5] bg-[#f8faf7] p-4 sm:grid-cols-2 xl:grid-cols-4">
        <label className="text-xs font-semibold text-slate-600">Search staff
          <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Name, email, phone, role, branch" className="mt-1 block w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-normal" />
        </label>
        <label className="text-xs font-semibold text-slate-600">Status
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="mt-1 block w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-normal">
            <option value="all">All statuses</option>
            <option value="active">Active</option>
            <option value="pending_activation">Pending activation</option>
            <option value="suspended">Suspended</option>
          </select>
        </label>
        <label className="text-xs font-semibold text-slate-600">Role
          <select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)} className="mt-1 block w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-normal">
            <option value="all">All roles</option>
            {roles.map((role) => <option key={role} value={role}>{role}</option>)}
          </select>
        </label>
        <label className="text-xs font-semibold text-slate-600">Branch
          <select value={branchFilter} onChange={(event) => setBranchFilter(event.target.value)} className="mt-1 block w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-normal">
            <option value="all">All branches</option>
            {branches.map((branch) => <option key={branch.id} value={branch.id}>{branchDisplayName(branch)}</option>)}
          </select>
        </label>
      </div>

      {loading ? <div className="mt-5"><PortalLoading label="Loading agency staff..." rows={5} /></div> : (
      <div className="mt-5 overflow-x-auto">
        <table className="w-full min-w-[880px] text-left text-sm text-slate-700">
          <thead className="bg-slate-50 text-xs font-bold uppercase tracking-wider text-slate-500">
            <tr>
              <th className="px-3 py-3">Staff member</th>
              <th className="px-3 py-3">Role</th>
              <th className="px-3 py-3">Branch scope</th>
              <th className="px-3 py-3">Status</th>
              <th className="px-3 py-3">MFA</th>
              <th className="px-3 py-3">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {pagination.pageRecords.map((member) => {
              const roleLabel = member.roles.length ? member.roles.join(', ') : 'Unassigned';
              const pendingRoleLabel = member.pending_roles?.length
                ? `${member.pending_roles.join(', ')} (confirmation pending)`
                : null;
              const branchLabel = member.branches.map(branchDisplayName).join(', ');
              const pendingBranchLabel = branches
                .filter((branch) => member.pending_branch_ids?.includes(branch.id))
                .map(branchDisplayName)
                .join(', ');
              const editingThis = editing === member.membership_id;
              const requiresMfa = [...member.roles, ...(member.pending_roles || [])]
                .some((role) => role === 'Branch Manager' || role === 'Accountant');
              return (
                <tr key={member.membership_id} className="align-top hover:bg-slate-50/50">
                  <td className="px-3 py-3">
                    <p className="font-bold text-slate-900">{member.name}</p>
                    <p className="text-xs text-slate-500">{member.email}</p>
                    <p className="text-xs text-slate-500">{member.phone || 'No phone'}</p>
                  </td>
                  <td className="px-3 py-3 font-semibold text-emerald-800">
                    {editingThis ? (
                      <div className="space-y-1">
                        {roles.map((option) => (
                          <label key={option} className="flex items-center gap-1 text-xs">
                            <input
                              type="checkbox"
                              checked={editRoles.includes(option)}
                              onChange={() => setEditRoles((current) =>
                                current.includes(option)
                                  ? current.filter((item) => item !== option)
                                  : [...current, option],
                              )}
                            />
                            {option}
                          </label>
                        ))}
                      </div>
                    ) : pendingRoleLabel || roleLabel}
                  </td>
                  <td className="px-3 py-3">
                    {editingThis ? (
                      <div className="space-y-1">
                        {branches.map((branch) => (
                          <label key={branch.id} className="flex items-center gap-2 text-xs">
                            <input type="checkbox" checked={editBranchIds.includes(branch.id)} onChange={() => setEditBranchIds((current) => toggleBranch(current, branch.id))} />
                            {branchDisplayName(branch)}
                          </label>
                        ))}
                      </div>
                    ) : pendingBranchLabel
                      ? `${branchLabel ? `${branchLabel}; ` : ''}${pendingBranchLabel} (pending)`
                      : branchLabel || 'No branches'}
                  </td>
                  <td className="px-3 py-3">
                    <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${
                      member.status === 'active'
                        ? 'bg-emerald-100 text-emerald-800'
                        : member.status === 'pending_activation'
                          ? 'bg-amber-100 text-amber-800'
                          : 'bg-slate-200 text-slate-700'
                    }`}>
                      {member.status.replace('_', ' ')}
                    </span>
                  </td>
                  <td className="px-3 py-3 text-xs">
                    <span className="inline-flex items-center gap-1">
                      <ShieldCheck size={14} className={requiresMfa ? 'text-emerald-700' : 'text-slate-400'} />
                      {requiresMfa ? 'Authenticator required' : 'Not required'}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex flex-wrap gap-1.5">
                      {editingThis ? (
                        <>
                          <button
                            type="button"
                            disabled={busy || editBranchIds.length === 0 || !editRoles.length}
                            onClick={() => void updateStaff(member, {
                              roles: editRoles,
                              branchIds: editBranchIds,
                              active: member.active,
                            })}
                            className="rounded-md bg-emerald-800 px-2.5 py-1.5 text-xs font-bold text-white disabled:opacity-50"
                          >
                            Save
                          </button>
                          <button type="button" onClick={() => setEditing(null)} className="rounded-md border px-2.5 py-1.5 text-xs font-semibold">Cancel</button>
                        </>
                      ) : (
                        <>
                          {!member.pending_roles?.length && (
                            <button type="button" onClick={() => startEditing(member)} className="inline-flex items-center gap-1 rounded-md border border-slate-300 px-2.5 py-1.5 text-xs font-semibold">
                              <Pencil size={12} /> Edit
                            </button>
                          )}
                          {((!member.account_active && member.active) || Boolean(member.pending_roles?.length)) && (
                            <button type="button" disabled={busy} onClick={() => void resendActivation(member)} className="inline-flex items-center gap-1 rounded-md border border-amber-300 px-2.5 py-1.5 text-xs font-semibold text-amber-900 disabled:opacity-50">
                              <RefreshCw size={12} /> Resend
                            </button>
                          )}
                          {member.active ? (
                            <button type="button" disabled={busy} onClick={() => void suspendStaff(member)} className="inline-flex items-center gap-1 rounded-md border border-red-200 px-2.5 py-1.5 text-xs font-semibold text-red-700 disabled:opacity-50">
                              <UserRoundX size={12} /> Suspend
                            </button>
                          ) : (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => void updateStaff(member, {
                                roles: member.roles,
                                branchIds: member.branches.map((branch) => branch.id),
                                active: true,
                              })}
                              className="inline-flex items-center gap-1 rounded-md border border-emerald-200 px-2.5 py-1.5 text-xs font-semibold text-emerald-800 disabled:opacity-50"
                            >
                              <UserCheck size={12} /> Restore
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
            {filteredStaff.length === 0 && (
              <tr><td colSpan={6} className="px-3 py-10 text-center text-sm text-slate-500">{staff.length === 0 ? 'No staff accounts for this agency yet.' : 'No staff accounts match these filters.'}</td></tr>
            )}
          </tbody>
        </table>
        <PortalPagination page={pagination.page} pageCount={pagination.pageCount} pageSize={pagination.pageSize} total={filteredStaff.length} onPageChange={pagination.setPage} onPageSizeChange={pagination.setPageSize} />
      </div>
      )}
    </section>
  );
}
