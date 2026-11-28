'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';

type Notice = {
  id: number;
  tenancy_id: number;
  tenant_name: string;
  property_name: string;
  unit_code: string;
  proposed_move_out_date: string;
  reason: string;
  status: string;
  review_note: string;
  available_caretakers: { id: number; name: string }[];
};

type MeteredUtility = {
  utility_type: string;
  name: string;
  meter: string | null;
  rate_per_unit: string;
  minimum_charge: string;
  previous_reading: { period: string; current_value: string } | null;
};

type Candidate = {
  tenancy_id: number;
  notice_id: number;
  unit_id: number;
  tenant_name: string;
  property_name: string;
  unit_code: string;
  move_out_date: string;
  deposit_balance: string;
  arrears_amount: string;
  metered_utilities: MeteredUtility[];
};

type Settlement = {
  id: number;
  tenancy_id: number;
  tenant_name: string;
  property_name: string;
  unit_code: string;
  actual_move_out_date: string;
  deposit_balance: string;
  arrears_amount: string;
  utility_amount: string;
  damages_amount: string;
  deduction_from_deposit: string;
  refund_amount: string;
  shortfall_amount: string;
  status: string;
  prepared_by: number;
  rejection_reason: string;
  payout_reference: string | null;
  payout_date: string | null;
};

type Tenancy = {
  id: number;
  tenant_name: string;
  property_name: string;
  unit_code: string;
  notice_period_days: number;
  status: string;
};

type Inspection = {
  id: number;
  unit_id: number | null;
  inspection_type: string;
  title: string;
  status: string;
};

type Workflows = {
  notices: Notice[];
  settlements: Settlement[];
  eligible_tenancies: Candidate[];
};

type DamageItem = { description: string; amount: string };

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 2,
});

function todayInNairobi() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Nairobi',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
}

function addDays(value: string, days: number) {
  const [year, month, day] = value.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, cache: 'no-store' });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || 'The request could not be completed.');
  return payload as T;
}

export default function MoveOutsPage() {
  const [workflows, setWorkflows] = useState<Workflows | null>(null);
  const [tenancies, setTenancies] = useState<Tenancy[]>([]);
  const [inspections, setInspections] = useState<Inspection[]>([]);
  const [selectedTenancy, setSelectedTenancy] = useState('');
  const [staffNoticeDate, setStaffNoticeDate] = useState(todayInNairobi());
  const [staffNoticeReason, setStaffNoticeReason] = useState('');
  const [selectedCaretakers, setSelectedCaretakers] = useState<Record<number, string>>({});
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [actualMoveOutDate, setActualMoveOutDate] = useState(todayInNairobi());
  const [inspectionId, setInspectionId] = useState('');
  const [readingValues, setReadingValues] = useState<Record<string, string>>({});
  const [damageItems, setDamageItems] = useState<DamageItem[]>([]);
  const [damageDescription, setDamageDescription] = useState('');
  const [damageAmount, setDamageAmount] = useState('');
  const [payoutReferences, setPayoutReferences] = useState<Record<number, string>>({});
  const [payoutDates, setPayoutDates] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const activeTenancies = useMemo(
    () => tenancies.filter((tenancy) =>
      tenancy.status === 'active' &&
      !workflows?.notices.some((notice) =>
        notice.tenancy_id === tenancy.id &&
        ['submitted', 'accepted'].includes(notice.status),
      ),
    ),
    [tenancies, workflows],
  );
  const selectedTenancyRecord = activeTenancies.find(
    (tenancy) => String(tenancy.id) === selectedTenancy,
  );
  const eligibleInspections = useMemo(
    () => inspections.filter((inspection) =>
      inspection.unit_id === candidate?.unit_id &&
      inspection.status === 'completed' &&
      ['move_out', 'exit'].includes(inspection.inspection_type.toLowerCase()),
    ),
    [candidate, inspections],
  );

  const loadData = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [workflowData, tenancyData, inspectionData] = await Promise.all([
        requestJson<Workflows>('/api/move-outs/workflows'),
        requestJson<{ tenancies: Tenancy[] }>('/api/move-outs/tenancies'),
        requestJson<{ inspections: Inspection[] }>('/api/move-outs/inspections?status=completed'),
      ]);
      setWorkflows(workflowData);
      setTenancies(tenancyData.tenancies);
      setInspections(inspectionData.inspections);
    } catch (loadError) {
      setWorkflows(null);
      setError(loadError instanceof Error ? loadError.message : 'Unable to load move-out workflows.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    if (!selectedTenancyRecord) return;
    setStaffNoticeDate(addDays(todayInNairobi(), selectedTenancyRecord.notice_period_days));
  }, [selectedTenancyRecord]);

  const submitStaffNotice = async () => {
    if (!selectedTenancy) return;
    setBusy(true);
    setError('');
    try {
      await requestJson('/api/move-outs/notices', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tenancy_id: Number(selectedTenancy),
          proposed_move_out_date: staffNoticeDate,
          reason: staffNoticeReason,
        }),
      });
      setSelectedTenancy('');
      setStaffNoticeReason('');
      await loadData();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'Unable to record the notice.');
    } finally {
      setBusy(false);
    }
  };

  const decideNotice = async (notice: Notice, action: 'accept' | 'reject' | 'withdraw') => {
    const caretakerId = selectedCaretakers[notice.id];
    if (action === 'accept' && !caretakerId) {
      setError('Select a caretaker before accepting the notice.');
      return;
    }
    const reviewNote = action === 'reject'
      ? window.prompt('Reason for rejecting this notice?')?.trim()
      : '';
    if (action === 'reject' && !reviewNote) return;
    setBusy(true);
    setError('');
    try {
      await requestJson(`/api/move-outs/notices/${notice.id}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action,
          review_note: reviewNote || '',
          ...(action === 'accept' ? { caretaker_id: Number(caretakerId) } : {}),
        }),
      });
      await loadData();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'Unable to update the notice.');
    } finally {
      setBusy(false);
    }
  };

  const openSettlement = (moveOut: Candidate) => {
    setCandidate(moveOut);
    setActualMoveOutDate(todayInNairobi());
    setInspectionId('');
    setReadingValues({});
    setDamageItems([]);
    setDamageDescription('');
    setDamageAmount('');
  };

  const addDamage = () => {
    const description = damageDescription.trim();
    const amount = Number(damageAmount);
    if (!description || !Number.isFinite(amount) || amount <= 0) {
      setError('Enter a damage description and a positive amount.');
      return;
    }
    setDamageItems((items) => [...items, { description, amount: damageAmount }]);
    setDamageDescription('');
    setDamageAmount('');
    setError('');
  };

  const submitSettlement = async () => {
    if (!candidate || !inspectionId) return;
    setBusy(true);
    setError('');
    try {
      await requestJson('/api/move-outs/settlements', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tenancy_id: candidate.tenancy_id,
          notice_id: candidate.notice_id,
          exit_inspection_id: Number(inspectionId),
          actual_move_out_date: actualMoveOutDate,
          final_readings: candidate.metered_utilities.map((utility) => ({
            utility_type: utility.utility_type,
            current_value: readingValues[utility.utility_type] || '',
          })),
          damage_items: damageItems.map((item) => ({
            ...item,
            amount: Number(item.amount),
          })),
        }),
      });
      setCandidate(null);
      await loadData();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'Unable to submit the settlement.');
    } finally {
      setBusy(false);
    }
  };

  const decideSettlement = async (settlement: Settlement, action: 'approve' | 'reject') => {
    const reviewNote = action === 'reject'
      ? window.prompt('Reason for rejecting this settlement?')?.trim()
      : '';
    if (action === 'reject' && !reviewNote) return;
    setBusy(true);
    setError('');
    try {
      await requestJson(`/api/move-outs/settlements/${settlement.id}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, review_note: reviewNote || '' }),
      });
      await loadData();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'Unable to review the settlement.');
    } finally {
      setBusy(false);
    }
  };

  const recordPayout = async (settlement: Settlement) => {
    setBusy(true);
    setError('');
    try {
      await requestJson(`/api/move-outs/settlements/${settlement.id}/payout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          payout_reference: payoutReferences[settlement.id] || '',
          payout_date: payoutDates[settlement.id] || todayInNairobi(),
        }),
      });
      await loadData();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'Unable to record the payout.');
    } finally {
      setBusy(false);
    }
  };

  if (loading && !workflows) {
    return <p className="p-8 text-slate-500">Loading move-out workflows...</p>;
  }

  return (
    <div className="space-y-7">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-slate-900">Move-out workflows</h1>
          <p className="mt-1 text-slate-600">Review notices, assign exit inspections, prepare settlements, and record refunds.</p>
        </div>
        <button type="button" onClick={() => void loadData()} disabled={loading} className="inline-flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-semibold disabled:opacity-60">
          <RefreshCw size={16} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </header>

      {error && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-lg font-bold text-slate-900">Record a staff-submitted notice</h2>
        <p className="mt-1 text-sm text-slate-600">The proposed date must honor the tenancy notice period.</p>
        <div className="mt-4 grid gap-3 md:grid-cols-[2fr_1fr_2fr_auto]">
          <label className="text-sm font-medium text-slate-700">
            Active tenancy
            <select value={selectedTenancy} onChange={(event) => setSelectedTenancy(event.target.value)} className="mt-1 w-full rounded-lg border px-3 py-2">
              <option value="">Select a tenancy</option>
              {activeTenancies.map((tenancy) => (
                <option key={tenancy.id} value={tenancy.id}>
                  {tenancy.property_name} · {tenancy.unit_code} · {tenancy.tenant_name}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm font-medium text-slate-700">
            Proposed date
            <input type="date" min={selectedTenancyRecord ? addDays(todayInNairobi(), selectedTenancyRecord.notice_period_days) : todayInNairobi()} value={staffNoticeDate} onChange={(event) => setStaffNoticeDate(event.target.value)} className="mt-1 w-full rounded-lg border px-3 py-2" />
          </label>
          <label className="text-sm font-medium text-slate-700">
            Reason (optional)
            <input value={staffNoticeReason} onChange={(event) => setStaffNoticeReason(event.target.value)} className="mt-1 w-full rounded-lg border px-3 py-2" />
          </label>
          <button type="button" disabled={busy || !selectedTenancy || !staffNoticeDate} onClick={() => void submitStaffNotice()} className="self-end rounded-lg bg-slate-900 px-4 py-2 font-semibold text-white disabled:opacity-50">
            Record notice
          </button>
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b p-5">
          <h2 className="text-lg font-bold text-slate-900">Move-out notices</h2>
          <p className="mt-1 text-sm text-slate-600">Tenant- and staff-submitted notices in your property scope.</p>
        </div>
        {!workflows?.notices.length ? (
          <p className="p-5 text-sm text-slate-500">No move-out notices to review.</p>
        ) : (
          <div className="divide-y">
            {workflows.notices.map((notice) => (
              <article key={notice.id} className="flex flex-wrap items-center gap-4 p-5">
                <div className="min-w-56 flex-1">
                  <h3 className="font-semibold text-slate-900">{notice.tenant_name} · {notice.property_name} / {notice.unit_code}</h3>
                  <p className="text-sm text-slate-600">Proposed move-out: {notice.proposed_move_out_date}</p>
                  {notice.reason && <p className="mt-1 text-sm text-slate-600">{notice.reason}</p>}
                  {notice.review_note && <p className="mt-1 text-sm text-amber-800">Review note: {notice.review_note}</p>}
                </div>
                <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold uppercase text-slate-700">{notice.status.replaceAll('_', ' ')}</span>
                {notice.status === 'submitted' && (
                  <div className="flex flex-wrap items-end gap-2">
                    <label className="text-sm font-medium text-slate-700">
                      Assign caretaker
                      <select
                        value={selectedCaretakers[notice.id] || ''}
                        onChange={(event) => setSelectedCaretakers((values) => ({
                          ...values,
                          [notice.id]: event.target.value,
                        }))}
                        className="mt-1 block rounded-lg border px-3 py-2"
                      >
                        <option value="">Select a caretaker</option>
                        {notice.available_caretakers.map((caretaker) => (
                          <option key={caretaker.id} value={caretaker.id}>{caretaker.name}</option>
                        ))}
                      </select>
                    </label>
                    <button type="button" disabled={busy || notice.available_caretakers.length === 0 || !selectedCaretakers[notice.id]} onClick={() => void decideNotice(notice, 'accept')} className="rounded-lg bg-emerald-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">Accept</button>
                    <button type="button" disabled={busy} onClick={() => void decideNotice(notice, 'reject')} className="rounded-lg border border-red-300 px-3 py-2 text-sm font-semibold text-red-700 disabled:opacity-50">Reject</button>
                    {notice.available_caretakers.length === 0 && (
                      <p className="basis-full text-xs text-amber-800">Assign another active caretaker to this property before accepting; the tenant cannot inspect their own unit.</p>
                    )}
                  </div>
                )}
                {notice.status === 'accepted' && (
                  <button type="button" disabled={busy} onClick={() => void decideNotice(notice, 'withdraw')} className="rounded-lg border px-3 py-2 text-sm font-semibold text-slate-700 disabled:opacity-50">Withdraw notice</button>
                )}
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b p-5">
          <h2 className="text-lg font-bold text-slate-900">Prepare an exit settlement</h2>
          <p className="mt-1 text-sm text-slate-600">Select an accepted notice, completed exit inspection, final readings, and itemized damage.</p>
        </div>
        {!workflows?.eligible_tenancies.length ? (
          <p className="p-5 text-sm text-slate-500">No accepted notices are ready for settlement.</p>
        ) : (
          <div className="divide-y">
            {workflows.eligible_tenancies.map((item) => (
              <article key={item.tenancy_id} className="flex flex-wrap items-center gap-4 p-5">
                <div className="min-w-56 flex-1">
                  <h3 className="font-semibold text-slate-900">{item.tenant_name} · {item.property_name} / {item.unit_code}</h3>
                  <p className="text-sm text-slate-600">Move-out date {item.move_out_date} · Deposit {currency.format(Number(item.deposit_balance))} · Current arrears {currency.format(Number(item.arrears_amount))}</p>
                  {item.metered_utilities.length > 0 && (
                    <p className="mt-1 text-xs text-slate-500">{item.metered_utilities.length} final meter reading(s) required.</p>
                  )}
                </div>
                <button type="button" onClick={() => openSettlement(item)} className="rounded-lg bg-emerald-700 px-4 py-2 text-sm font-semibold text-white">Prepare settlement</button>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b p-5">
          <h2 className="text-lg font-bold text-slate-900">Settlement review and payout</h2>
          <p className="mt-1 text-sm text-slate-600">A different Branch Manager must approve; finance-authorized staff record the manual payout.</p>
        </div>
        {!workflows?.settlements.length ? (
          <p className="p-5 text-sm text-slate-500">No move-out settlements yet.</p>
        ) : (
          <div className="divide-y">
            {workflows.settlements.map((settlement) => (
              <article key={settlement.id} className="space-y-4 p-5">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <h3 className="font-semibold text-slate-900">{settlement.tenant_name} · {settlement.property_name} / {settlement.unit_code}</h3>
                    <p className="text-sm text-slate-600">Moved out {settlement.actual_move_out_date} · {settlement.status.replaceAll('_', ' ')}</p>
                  </div>
                  <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold uppercase text-slate-700">{settlement.status.replaceAll('_', ' ')}</span>
                </div>
                <dl className="grid gap-3 text-sm sm:grid-cols-3 lg:grid-cols-6">
                  <div><dt className="text-slate-500">Deposit</dt><dd className="font-semibold">{currency.format(Number(settlement.deposit_balance))}</dd></div>
                  <div><dt className="text-slate-500">Invoice arrears</dt><dd className="font-semibold">{currency.format(Number(settlement.arrears_amount))}</dd></div>
                  <div><dt className="text-slate-500">Final metered utilities (deposit deduction)</dt><dd className="font-semibold">{currency.format(Number(settlement.utility_amount))}</dd></div>
                  <div><dt className="text-slate-500">Damage</dt><dd className="font-semibold">{currency.format(Number(settlement.damages_amount))}</dd></div>
                  <div><dt className="text-slate-500">Refund</dt><dd className="font-semibold text-emerald-700">{currency.format(Number(settlement.refund_amount))}</dd></div>
                  <div><dt className="text-slate-500">Debt shortfall</dt><dd className="font-semibold text-red-700">{currency.format(Number(settlement.shortfall_amount))}</dd></div>
                </dl>
                {settlement.rejection_reason && <p className="text-sm text-red-700">Rejection reason: {settlement.rejection_reason}</p>}
                {settlement.status === 'awaiting_approval' && (
                  <div className="flex gap-2">
                    <button type="button" disabled={busy} onClick={() => void decideSettlement(settlement, 'approve')} className="rounded-lg bg-emerald-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">Approve</button>
                    <button type="button" disabled={busy} onClick={() => void decideSettlement(settlement, 'reject')} className="rounded-lg border border-red-300 px-3 py-2 text-sm font-semibold text-red-700 disabled:opacity-50">Reject</button>
                  </div>
                )}
                {settlement.status === 'approved' && (
                  <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
                    <label className="text-sm font-medium text-slate-700">
                      Manual payout reference{Number(settlement.refund_amount) > 0 ? ' (required)' : ''}
                      <input value={payoutReferences[settlement.id] || ''} onChange={(event) => setPayoutReferences((values) => ({ ...values, [settlement.id]: event.target.value }))} maxLength={100} className="mt-1 w-full rounded-lg border px-3 py-2" />
                    </label>
                    <label className="text-sm font-medium text-slate-700">
                      Payout date
                      <input type="date" min={settlement.actual_move_out_date} max={todayInNairobi()} value={payoutDates[settlement.id] || todayInNairobi()} onChange={(event) => setPayoutDates((values) => ({ ...values, [settlement.id]: event.target.value }))} className="mt-1 w-full rounded-lg border px-3 py-2" />
                    </label>
                    <button type="button" disabled={busy || (Number(settlement.refund_amount) > 0 && !payoutReferences[settlement.id]?.trim())} onClick={() => void recordPayout(settlement)} className="self-end rounded-lg bg-slate-900 px-4 py-2 font-semibold text-white disabled:opacity-50">Record payout / close</button>
                  </div>
                )}
                {settlement.status === 'paid' && settlement.payout_date && (
                  <p className="text-sm text-emerald-800">Closed {settlement.payout_date}{settlement.payout_reference ? ` · Reference ${settlement.payout_reference}` : ' · No refund was due'}</p>
                )}
              </article>
            ))}
          </div>
        )}
      </section>

      {candidate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/50 p-4">
          <section aria-labelledby="settlement-form-title" className="my-8 max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-2xl bg-white p-6 shadow-2xl">
            <h2 id="settlement-form-title" className="text-xl font-bold text-slate-900">Exit settlement · {candidate.property_name} / {candidate.unit_code}</h2>
            <p className="mt-1 text-sm text-slate-600">{candidate.tenant_name} · Deposit balance {currency.format(Number(candidate.deposit_balance))}</p>
            <div className="mt-5 grid gap-4 sm:grid-cols-2">
              <label className="text-sm font-medium text-slate-700">
                Actual move-out date
                <input type="date" min={candidate.move_out_date} max={todayInNairobi()} value={actualMoveOutDate} onChange={(event) => setActualMoveOutDate(event.target.value)} className="mt-1 w-full rounded-lg border px-3 py-2" />
              </label>
              <label className="text-sm font-medium text-slate-700">
                Completed exit inspection
                <select value={inspectionId} onChange={(event) => setInspectionId(event.target.value)} className="mt-1 w-full rounded-lg border px-3 py-2">
                  <option value="">Select an inspection</option>
                  {eligibleInspections.map((inspection) => (
                    <option key={inspection.id} value={inspection.id}>{inspection.title} · #{inspection.id}</option>
                  ))}
                </select>
              </label>
            </div>
            {eligibleInspections.length === 0 && <p className="mt-2 text-sm text-amber-800">Complete a move-out or exit inspection for this unit before submitting the settlement.</p>}

            {candidate.metered_utilities.length > 0 && (
              <fieldset className="mt-5 space-y-3">
                <legend className="font-semibold text-slate-900">Final meter readings</legend>
                {candidate.metered_utilities.map((utility) => (
                  <label key={utility.utility_type} className="grid gap-1 text-sm text-slate-700 sm:grid-cols-[1fr_1fr]">
                    <span>{utility.name} · Meter {utility.meter || 'not configured'} · Previous {utility.previous_reading?.current_value ?? 'no prior reading'} ({utility.previous_reading?.period ?? '—'})</span>
                    <input type="number" step="0.0001" min={utility.previous_reading?.current_value ?? '0'} value={readingValues[utility.utility_type] || ''} onChange={(event) => setReadingValues((values) => ({ ...values, [utility.utility_type]: event.target.value }))} placeholder="Current meter value" className="rounded-lg border px-3 py-2" />
                  </label>
                ))}
              </fieldset>
            )}

            <fieldset className="mt-5 space-y-3">
              <legend className="font-semibold text-slate-900">Itemized damage deductions</legend>
              <div className="grid gap-2 sm:grid-cols-[1fr_10rem_auto]">
                <input value={damageDescription} onChange={(event) => setDamageDescription(event.target.value)} maxLength={255} placeholder="Damage description" className="rounded-lg border px-3 py-2" />
                <input type="number" step="0.01" min="0.01" value={damageAmount} onChange={(event) => setDamageAmount(event.target.value)} placeholder="Amount (KES)" className="rounded-lg border px-3 py-2" />
                <button type="button" onClick={addDamage} className="rounded-lg border px-3 py-2 font-semibold">Add item</button>
              </div>
              {damageItems.map((item, index) => (
                <div key={`${item.description}-${index}`} className="flex justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-sm">
                  <span>{item.description}</span>
                  <span>{currency.format(Number(item.amount))}</span>
                  <button type="button" onClick={() => setDamageItems((items) => items.filter((_, itemIndex) => itemIndex !== index))} className="font-semibold text-red-700">Remove</button>
                </div>
              ))}
            </fieldset>

            <div className="mt-6 flex justify-end gap-3">
              <button type="button" onClick={() => setCandidate(null)} className="rounded-lg border px-4 py-2 font-semibold">Cancel</button>
              <button type="button" disabled={busy || !inspectionId || candidate.metered_utilities.some((utility) => !utility.previous_reading || !utility.meter || !readingValues[utility.utility_type])} onClick={() => void submitSettlement()} className="rounded-lg bg-emerald-700 px-4 py-2 font-semibold text-white disabled:opacity-50">Submit for approval</button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
