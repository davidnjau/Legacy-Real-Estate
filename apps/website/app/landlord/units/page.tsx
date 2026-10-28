// app/landlord/units/page.tsx
'use client';
import { useEffect, useState } from 'react';
import {
  Key, FileCheck, X, Plus, Trash2, CheckCircle,
  AlertTriangle, Bell, BellOff, LogOut, Home, Info,
} from 'lucide-react';
import { Pagination } from '../../../components/ui/Pagination';

// ─── Types ───────────────────────────────────────────────────────────────────

type UnitStatus =
  | 'vacant'
  | 'booked'
  | 'occupied'
  | 'notice_given'
  | 'deposit_pending'
  | 'maintenance'
  | 'offboarding'; // legacy, treated as deposit_pending

interface UnitRow {
  id: string;
  unitCode: string;
  floor: string;
  roomName: string;
  status: UnitStatus;
  rentAmount: number;
  garbageAmount: number;
  currentTenant: string;
  currentTenantName?: string;
  tenantEmail: string;
  tenantPhone: string;
  tenantStatus?: string | null;
  moveInDate: string;
  depositAmount: number;
  securityDeposit: number;
  waterDeposit: number;
  expectedMoveOutDate?: string | null;
}

interface MoveInSettlement {
  id: string;
  unit_id: string;
  tenant_name: string;
  tenant_email: string;
  tenant_phone: string;
  deposit_amount: number;
  rent_amount: number;
  garbage_amount: number;
  water_deposit_amount: number;
  total_required: number;
  total_paid: number;
  outstanding_balance: number;
  status: string;
  utility_charges?: { name: string; amount: number }[];
  activation_email_sent_at?: string | null;
  activation_email_error?: string | null;
  expected_move_in_date?: string | null;
  units?: { unit_code: string };
}

interface DamageItem {
  description: string;
  amount: number;
}

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

const GRACE_DAY = 10; // Rent payment deadline / move-out grace period

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Returns true if today is past the 10th of the current month */
function isAfterGracePeriod(): boolean {
  return new Date().getDate() > GRACE_DAY;
}

/** Returns true if today is on or before the 10th */
function isWithinGracePeriod(): boolean {
  return new Date().getDate() <= GRACE_DAY;
}

/** Prorates rent: (daysRemaining / totalDays) * rentAmount */
function calcProratedRefund(rentAmount: number): number {
  const today = new Date();
  const totalDays = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
  const daysRemaining = totalDays - today.getDate();
  return Math.round((daysRemaining / totalDays) * rentAmount);
}

// ─── Status display helpers ───────────────────────────────────────────────────

function statusLabel(status: string) {
  const map: Record<string, string> = {
    occupied: 'Occupied',
    booked: 'Booked',
    vacant: 'Vacant',
    notice_given: 'Notice Given',
    deposit_pending: 'Deposit Pending',
    offboarding: 'Deposit Pending',
    maintenance: 'Maintenance',
  };
  return map[status] ?? status;
}

function statusColor(status: string) {
  const map: Record<string, string> = {
    occupied: 'bg-emerald-100 text-emerald-700',
    booked: 'bg-indigo-100 text-indigo-700',
    vacant: 'bg-slate-100 text-slate-600',
    notice_given: 'bg-amber-100 text-amber-700',
    deposit_pending: 'bg-orange-100 text-orange-700',
    offboarding: 'bg-orange-100 text-orange-700',
    maintenance: 'bg-red-100 text-red-700',
  };
  return map[status] ?? 'bg-slate-100 text-slate-600';
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function UnitsAndTenants() {
  const [units, setUnits] = useState<UnitRow[]>([]);
  const [floorFilter, setFloorFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [loading, setLoading] = useState(true);

  // Pagination states
  const [unitsPage, setUnitsPage] = useState(1);
  const [settlementsPage, setSettlementsPage] = useState(1);

  // Tenant detail popover (shows inline below the row)
  const [detailUnitId, setDetailUnitId] = useState<string | null>(null);

  const [settlements, setSettlements] = useState<MoveInSettlement[]>([]);
  const [settlementUnit, setSettlementUnit] = useState<UnitRow | null>(null);
  const [settlementForm, setSettlementForm] = useState({
    tenantName: '',
    tenantEmail: '',
    tenantPhone: '',
    expectedMoveInDate: '',
    deposit: '8000',
    rent: '',
    garbage: '',
    waterDeposit: '500',
  });
  const [paymentForm, setPaymentForm] = useState({ amount: '', reference: '' });
  const [recurringUtilityCharges, setRecurringUtilityCharges] = useState<{ name: string; amount: number }[]>([]);

  // The unit currently being acted upon
  const [activeUnit, setActiveUnit] = useState<UnitRow | null>(null);

  // Which drawer/modal is open
  type ModalType =
    | 'none'
    | 'detail'            // Tenant detail + quick actions (occupied)
    | 'log_notice'        // Confirm log notice
    | 'offboard'          // Physical move-out confirmation
    | 'evacuation'        // Immediate evacuation flow
    | 'settle';           // Settle deposit
  const [modal, setModal] = useState<ModalType>('none');

  // Offboard state
  const [overstayPenalty, setOverstayPenalty] = useState('');
  const [offboardLoading, setOffboardLoading] = useState(false);
  const [offboardError, setOffboardError] = useState('');

  // Evacuation state
  const [evacReason, setEvacReason] = useState('');
  const [evacCancelInvoice, setEvacCancelInvoice] = useState(false);
  const [evacProrateRent, setEvacProrateRent] = useState(false);
  const [evacProrateAmount, setEvacProrateAmount] = useState('');
  const [evacLoading, setEvacLoading] = useState(false);
  const [evacError, setEvacError] = useState('');

  // Settle deposit state
  const [settleArrears, setSettleArrears] = useState(0);
  const [settleOverstay, setSettleOverstay] = useState(0);
  const [settleProrated, setSettleProrated] = useState(0);
  const [damages, setDamages] = useState<DamageItem[]>([]);
  const [newDamageDesc, setNewDamageDesc] = useState('');
  const [newDamageAmount, setNewDamageAmount] = useState('');
  const [mpesaRef, setMpesaRef] = useState('');
  const [isSettling, setIsSettling] = useState(false);
  const [settlementDone, setSettlementDone] = useState(false);
  const [settlementError, setSettlementError] = useState('');
  const [settlementSummary, setSettlementSummary] = useState<any>(null);

  // Button loading states
  const [isCreatingSettlement, setIsCreatingSettlement] = useState(false);
  const [isLoggingNotice, setIsLoggingNotice] = useState(false);
  const [isCancellingSettlement, setIsCancellingSettlement] = useState(false);
  const [isRecordingSettlementPayment, setIsRecordingSettlementPayment] = useState(false);
  const [isInitiatingStk, setIsInitiatingStk] = useState(false);
  const [sendingActivationFor, setSendingActivationFor] = useState<string | null>(null);

  // ─── Fetch units ─────────────────────────────────────────────────────────

  const fetchUnits = async () => {
    try {
      const res = await fetch('/api/units');
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to load units');
      setUnits(data.units || []);
    } catch (err) {
      console.error('Failed to load units', err);
    } finally {
      setLoading(false);
    }
  };

  const fetchSettlements = async () => { const res = await fetch('/api/units/move-in-settlement'); const data = await res.json(); if (res.ok) setSettlements(data.settlements || []); };
  useEffect(() => {
    fetchUnits();
    fetchSettlements();
    fetch('/api/landlord/properties')
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Unable to load property utilities.');
        const activeProperty = (data.properties || []).find((property: { id: string }) => property.id === data.activePropertyId);
        setRecurringUtilityCharges((activeProperty?.recurringCharges || [])
          .filter((charge: { is_active: boolean }) => charge.is_active)
          .map((charge: { name: string; amount: number }) => ({ name: charge.name, amount: Number(charge.amount) })));
      })
      .catch((error) => console.error('Failed to load recurring utilities:', error));
  }, []);

  // ─── Open modals ──────────────────────────────────────────────────────────

  const openModal = (unit: UnitRow, type: ModalType) => {
    setActiveUnit(unit);
    setModal(type);

    // Reset evacuation state
    setEvacReason('');
    setEvacCancelInvoice(false);
    setEvacProrateRent(false);
    setEvacProrateAmount('');
    setEvacError('');

    // Reset offboard state
    setOverstayPenalty('');
    setOffboardError('');

    // Reset settle state
    setDamages([]);
    setNewDamageDesc('');
    setNewDamageAmount('');
    setMpesaRef('');
    setSettlementDone(false);
    setSettlementError('');
    setSettlementSummary(null);

    // Pre-fill settlement penalties if unit has overstay info in the future
    // (server will compute them; we show live values from the API response)
    setSettleArrears(0);
    setSettleOverstay(0);
    setSettleProrated(0);
  };

  const closeModal = () => {
    setModal('none');
    setActiveUnit(null);
  };

  const openSettlement = (unit: UnitRow) => {
    setSettlementUnit(unit);
    const localToday = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Africa/Nairobi',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(new Date());
    setSettlementForm({
      tenantName: '',
      tenantEmail: '',
      tenantPhone: '',
      expectedMoveInDate: localToday,
      deposit: String(unit.securityDeposit || 0),
      rent: String(unit.rentAmount || ''),
      garbage: String(unit.garbageAmount || ''),
      waterDeposit: String(unit.waterDeposit || 0),
    });
    setPaymentForm({ amount: '', reference: '' });
  };

  const createSettlement = async () => {
    if (!settlementUnit) return;
    setIsCreatingSettlement(true);
    try {
      const response = await fetch('/api/units/move-in-settlement', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          unitId: settlementUnit.id,
          tenantName: settlementForm.tenantName,
          tenantEmail: settlementForm.tenantEmail,
          tenantPhone: settlementForm.tenantPhone,
          expectedMoveInDate: settlementForm.expectedMoveInDate,
          depositAmount: settlementForm.deposit,
          rentAmount: settlementForm.rent,
          garbageAmount: settlementForm.garbage,
          waterDepositAmount: settlementForm.waterDeposit,
        }),
      });
      const data = await response.json();
      if (!response.ok) { window.alert(data.error || 'Unable to create settlement.'); return; }
      setSettlementUnit(null); await fetchSettlements();
    } finally {
      setIsCreatingSettlement(false);
    }
  };

  const cancelSettlement = async (settlementId: string) => {
    const reason = window.prompt('Why is this settlement being cancelled?');
    if (!reason) return;
    setIsCancellingSettlement(true);
    try {
      const response = await fetch('/api/units/move-in-settlement', { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settlementId, reason }) });
      const data = await response.json();
      if (!response.ok) window.alert(data.error || 'Unable to cancel settlement.');
      else await fetchSettlements();
    } finally {
      setIsCancellingSettlement(false);
    }
  };

  const recordSettlementPayment = async (settlementId: string) => {
    setIsRecordingSettlementPayment(true);
    try {
      const response = await fetch('/api/units/move-in-settlement', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settlementId, amount: paymentForm.amount, mpesaReference: paymentForm.reference, method: 'manual' }) });
      const data = await response.json();
      if (!response.ok) { window.alert(data.error || 'Unable to record payment.'); return; }
      setPaymentForm({ amount: '', reference: '' }); await fetchSettlements();
      if (data.bookingConfirmed && !data.activationReady) window.alert('Deposit received. The unit is now booked; collect the remaining move-in balance before sending the tenant activation email.');
      if (data.activationReady) window.alert('Move-in balance cleared. You can now email the tenant their activation link.');
    } finally {
      setIsRecordingSettlementPayment(false);
    }
  };

  const initiateSettlementStk = async (settlement: MoveInSettlement) => {
    const amount = window.prompt('Amount to request by STK Push', String(settlement.outstanding_balance));
    if (!amount || Number(amount) <= 0) return;
    const phone = window.prompt('Tenant Safaricom phone number', settlement.tenant_phone || '07XXXXXXXX');
    if (!phone) return;
    setIsInitiatingStk(true);
    try {
      const response = await fetch('/api/mpesa/stkpush', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ phone, amount: Number(amount), settlementId: settlement.id }) });
      const data = await response.json();
      if (!response.ok || !data.success) window.alert(data.error || 'Unable to initiate STK Push.');
      else window.alert('STK Push sent to the tenant phone.');
    } finally {
      setIsInitiatingStk(false);
    }
  };

  const sendActivationEmail = async (settlement: MoveInSettlement) => {
    setSendingActivationFor(settlement.id);
    try {
      const response = await fetch('/api/units/move-in-settlement', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settlementId: settlement.id, sendActivationEmail: true }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to send the tenant activation email.');
      window.alert(data.message || 'Tenant activation email sent.');
      await fetchSettlements();
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Unable to send the tenant activation email.');
    } finally {
      setSendingActivationFor(null);
    }
  };

  // ─── Unit actions (simple) ────────────────────────────────────────────────

  const handleSimpleAction = async (unitId: string, action: string) => {
    try {
      const res = await fetch('/api/units/manage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ unitId, action }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Unable to update unit');
      await fetchUnits();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : 'Unable to update unit');
    }
  };

  // ─── Log Notice ──────────────────────────────────────────────────────────

  const handleLogNotice = async () => {
    if (!activeUnit) return;
    setIsLoggingNotice(true);
    try {
      const res = await fetch('/api/units/manage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ unitId: activeUnit.id, action: 'log_notice' }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to log notice');
      window.alert(`✅ ${data.message}`);
      closeModal();
      await fetchUnits();
    } catch (err: any) {
      window.alert(err.message || 'Failed to log notice');
    } finally {
      setIsLoggingNotice(false);
    }
  };

  // ─── Offboard Tenant ─────────────────────────────────────────────────────

  const handleOffboard = async () => {
    if (!activeUnit) return;
    setOffboardLoading(true);
    setOffboardError('');
    try {
      const res = await fetch('/api/units/manage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          unitId: activeUnit.id,
          action: 'offboard_tenant',
          overstayPenalty: parseFloat(overstayPenalty) || 0,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to offboard tenant');
      window.alert(`✅ ${data.message}`);
      closeModal();
      await fetchUnits();
    } catch (err: any) {
      setOffboardError(err.message || 'Failed to offboard tenant');
    } finally {
      setOffboardLoading(false);
    }
  };

  // ─── Immediate Evacuation ─────────────────────────────────────────────────

  const handleEvacuation = async () => {
    if (!activeUnit) return;
    if (!evacReason.trim()) {
      setEvacError('Please provide a reason for immediate evacuation.');
      return;
    }
    setEvacLoading(true);
    setEvacError('');
    try {
      const prorateAmt = evacProrateRent ? (parseFloat(evacProrateAmount) || 0) : 0;
      const res = await fetch('/api/units/manage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          unitId: activeUnit.id,
          action: 'immediate_evacuation',
          reason: evacReason.trim(),
          cancelCurrentInvoice: evacCancelInvoice,
          proratedRefundAmount: prorateAmt,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to process evacuation');
      window.alert(`✅ ${data.message}`);
      closeModal();
      await fetchUnits();
    } catch (err: any) {
      setEvacError(err.message || 'Failed to process evacuation');
    } finally {
      setEvacLoading(false);
    }
  };

  // ─── Settle Deposit ───────────────────────────────────────────────────────

  const addDamageItem = () => {
    const desc = newDamageDesc.trim();
    const amt = parseFloat(newDamageAmount);
    if (!desc || isNaN(amt) || amt <= 0) {
      window.alert('Please enter a valid damage description and amount.');
      return;
    }
    setDamages(prev => [...prev, { description: desc, amount: amt }]);
    setNewDamageDesc('');
    setNewDamageAmount('');
  };

  const removeDamageItem = (index: number) => {
    setDamages(prev => prev.filter((_, i) => i !== index));
  };

  const totalDamages = damages.reduce((s, d) => s + d.amount, 0);
  const depositHeld = activeUnit?.depositAmount || 0;
  const totalDeductions = totalDamages + settleArrears + settleOverstay;
  const netRefund = Math.max(0, depositHeld + settleProrated - totalDeductions);

  const handleSettleDeposit = async () => {
    if (!activeUnit) return;
    if (!mpesaRef.trim()) {
      setSettlementError('M-Pesa reference code is required.');
      return;
    }
    setIsSettling(true);
    setSettlementError('');
    try {
      const res = await fetch('/api/units/settle-deposit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          unitId: activeUnit.id,
          mpesaRef: mpesaRef.trim(),
          damages,
          depositHeld,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to settle deposit');
      setSettlementSummary(data.summary);
      setSettlementDone(true);
      await fetchUnits();
    } catch (err: any) {
      setSettlementError(err.message || 'Unable to settle deposit.');
    } finally {
      setIsSettling(false);
    }
  };

  // ─── Render ───────────────────────────────────────────────────────────────

  // Computed overstay indicator for a notice_given unit
  const isOverstayed = (unit: UnitRow) => {
    if (unit.status !== 'notice_given' || !unit.expectedMoveOutDate) return false;
    return new Date().toISOString().slice(0, 10) > unit.expectedMoveOutDate;
  };

  const floors = [...new Set(units.map((unit) => unit.floor).filter((floor) => floor && floor !== '-'))].sort();
  const filteredUnits = units.filter((unit) => (
    (floorFilter === 'all' || unit.floor === floorFilter) &&
    (statusFilter === 'all' || unit.status === statusFilter)
  ));

  // ─── Pagination Logic ───────────────────────────────────────────────────────
  const ITEMS_PER_PAGE = 10;

  // 1. Units & Tenants
  const totalUnitsPages = Math.ceil(filteredUnits.length / ITEMS_PER_PAGE);
  const currentUnits = filteredUnits.slice(
    (unitsPage - 1) * ITEMS_PER_PAGE,
    unitsPage * ITEMS_PER_PAGE
  );

  // 3. Move-in Settlements
  const totalSettlementsPages = Math.ceil(settlements.length / ITEMS_PER_PAGE);
  const currentSettlements = settlements.slice(
    (settlementsPage - 1) * ITEMS_PER_PAGE,
    settlementsPage * ITEMS_PER_PAGE
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-slate-900">Units &amp; Tenants</h1>
        <p className="text-slate-500 mt-1">Manage occupancy, notices, offboarding, and deposits.</p>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <div className="p-5 border-b border-slate-200 bg-emerald-50">
          <h2 className="font-bold text-slate-900">Move-In Settlements</h2>
          <p className="text-sm text-slate-600 mt-1">Deposits book the unit. Collect the remaining move-in balance before emailing the tenant an activation link.</p>
        </div>
        {settlements.length === 0 ? <p className="p-5 text-sm text-slate-500">No move-in settlements started.</p> : <div className="divide-y divide-slate-100">
          {currentSettlements.map((settlement) => <div key={settlement.id} className="p-5 flex flex-wrap items-center gap-5">
            <div className="min-w-40"><strong>{settlement.units?.unit_code || '-'}</strong><p className="text-sm text-slate-600">{settlement.tenant_name}</p></div>
            <div className="text-sm"><span className="text-slate-500">Required</span><strong className="block">{currency.format(Number(settlement.total_required))}</strong></div>
            <div className="text-sm"><span className="text-slate-500">Paid</span><strong className="block text-emerald-700">{currency.format(Number(settlement.total_paid))}</strong></div>
            <div className="text-sm"><span className="text-slate-500">Balance</span><strong className="block text-red-600">{currency.format(Number(settlement.outstanding_balance))}</strong></div>
            <span className="px-2.5 py-1 rounded-full text-xs font-bold uppercase bg-slate-100 text-slate-600">{settlement.status}</span>
            {Number(settlement.outstanding_balance) <= 0 && <button onClick={() => sendActivationEmail(settlement)} disabled={sendingActivationFor === settlement.id} className="ml-auto bg-slate-900 hover:bg-slate-800 disabled:opacity-60 text-white rounded-lg px-3 py-1.5 text-sm font-bold transition-colors">{sendingActivationFor === settlement.id ? 'Sending...' : settlement.activation_email_sent_at ? 'Resend Activation Email' : 'Send Activation Email'}</button>}
            {settlement.activation_email_error && <p className="w-full text-sm text-red-700">Last email attempt failed: {settlement.activation_email_error}</p>}
            {settlement.activation_email_sent_at && <p className="w-full text-sm text-emerald-700">Activation link sent to {settlement.tenant_email} on {new Date(settlement.activation_email_sent_at).toLocaleString('en-KE')}.</p>}
            {settlement.status !== 'paid' && <div className="flex gap-2 ml-auto flex-wrap">
              <button onClick={() => initiateSettlementStk(settlement)} disabled={isInitiatingStk} className="bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white rounded-lg px-3 py-1.5 text-sm font-bold transition-colors">{isInitiatingStk ? 'Sending...' : 'STK Push'}</button>
              <input type="number" placeholder="Amount" value={paymentForm.amount} onChange={(e) => setPaymentForm({ ...paymentForm, amount: e.target.value })} className="w-28 border rounded-lg px-2 py-1.5 text-sm" />
              <input placeholder="M-Pesa ref" value={paymentForm.reference} onChange={(e) => setPaymentForm({ ...paymentForm, reference: e.target.value })} className="w-32 border rounded-lg px-2 py-1.5 text-sm" />
              <button onClick={() => recordSettlementPayment(settlement.id)} disabled={isRecordingSettlementPayment} className="bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white rounded-lg px-3 py-1.5 text-sm font-bold transition-colors">{isRecordingSettlementPayment ? 'Recording...' : 'Record Payment'}</button>
              <button onClick={() => cancelSettlement(settlement.id)} disabled={isCancellingSettlement} className="border border-red-300 text-red-600 hover:bg-red-50 disabled:opacity-60 rounded-lg px-3 py-1.5 text-sm font-bold transition-colors">{isCancellingSettlement ? 'Cancelling...' : 'Cancel'}</button>
            </div>}
          </div>)}
          <Pagination
            currentPage={settlementsPage}
            totalPages={totalSettlementsPages}
            onPageChange={setSettlementsPage}
          />
        </div>}
      </div>

      {/* ── Units Table ── */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <div className="p-4 border-b border-slate-200 flex flex-wrap gap-3 items-center">
          <span className="text-sm font-bold text-slate-700">Filter units:</span>
          <select value={floorFilter} onChange={(event) => setFloorFilter(event.target.value)} className="border border-slate-300 rounded-lg px-3 py-2 text-sm">
            <option value="all">All floors</option>
            {floors.map((floor) => <option key={floor} value={floor}>{floor}</option>)}
          </select>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="border border-slate-300 rounded-lg px-3 py-2 text-sm">
            <option value="all">All statuses</option>
            {['vacant', 'booked', 'occupied', 'notice_given', 'deposit_pending', 'maintenance'].map((status) => <option key={status} value={status}>{statusLabel(status)}</option>)}
          </select>
        </div>
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200 text-sm text-slate-500">
              <th className="p-4 font-medium">Room</th>
              <th className="p-4 font-medium">Floor</th>
              <th className="p-4 font-medium">Status</th>
              <th className="p-4 font-medium">Tenant</th>
              <th className="p-4 font-medium">Move-In</th>
              <th className="p-4 font-medium">Move-Out By</th>
              <th className="p-4 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200">
            {loading ? (
              <tr><td colSpan={7} className="p-6 text-center text-slate-500">Loading units...</td></tr>
            ) : currentUnits.map((unit) => (
              <tr key={unit.id} className="hover:bg-slate-50 transition-colors">
                <td className="p-4 font-bold text-slate-900">{unit.unitCode}</td>
                <td className="p-4 text-slate-700">{unit.floor}</td>
                <td className="p-4">
                  <span className={`px-2.5 py-1 text-xs font-bold rounded-full ${statusColor(unit.status)}`}>
                    {statusLabel(unit.status)}
                  </span>
                  {isOverstayed(unit) && (
                    <span className="ml-2 px-2 py-0.5 text-xs font-bold rounded-full bg-red-100 text-red-700 animate-pulse">
                      ⚠ Overstayed
                    </span>
                  )}
                </td>
                <td className="p-4">
                  <div className="flex items-center gap-2">
                    <span className="text-slate-700">{unit.currentTenant === '-' ? unit.currentTenantName || '—' : unit.currentTenant || '—'}</span>
                    {unit.currentTenant && unit.currentTenant !== '-' && (
                      <button
                        onClick={() => setDetailUnitId(detailUnitId === unit.id ? null : unit.id)}
                        title="View tenant details"
                        className="p-1 rounded-md text-slate-400 hover:text-blue-600 hover:bg-blue-50 transition-colors"
                      >
                        <Info size={14} />
                      </button>
                    )}
                  </div>
                  {/* Tenant detail panel */}
                  {detailUnitId === unit.id && (
                    <div className="mt-2 bg-blue-50 border border-blue-200 rounded-lg p-3 text-xs space-y-1 shadow-sm w-56">
                      <div className="flex justify-between">
                        <span className="text-slate-500">Email</span>
                        <span className="font-medium text-slate-800 truncate ml-2">{unit.tenantEmail || '—'}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-slate-500">Phone</span>
                        <span className="font-medium text-slate-800">{unit.tenantPhone || '—'}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-slate-500">Move-In</span>
                        <span className="font-medium text-slate-800">{unit.moveInDate || '—'}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-slate-500">Security Deposit</span>
                        <span className="font-medium text-emerald-700">{currency.format(unit.securityDeposit || 0)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-slate-500">Water Deposit</span>
                        <span className="font-medium text-blue-700">{currency.format(unit.waterDeposit || 0)}</span>
                      </div>
                    </div>
                  )}
                </td>
                <td className="p-4 text-slate-500 text-sm">{unit.moveInDate || '—'}</td>
                <td className="p-4 text-slate-500 text-sm">
                  {unit.expectedMoveOutDate
                    ? <span className={isOverstayed(unit) ? 'text-red-600 font-bold' : 'text-amber-600 font-medium'}>
                        {unit.expectedMoveOutDate}
                      </span>
                    : '—'}
                </td>
                <td className="p-4 text-right">
                  <div className="flex flex-col justify-end items-end gap-1.5">

                    {/* VACANT */}
                    {unit.status === 'vacant' && (<>
                      <button onClick={() => openSettlement(unit)}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium rounded-lg transition-colors">
                        <Key size={14} /> Start Move-In Settlement
                      </button>
                      <button onClick={() => handleSimpleAction(unit.id, 'mark_maintenance')}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium rounded-lg transition-colors">
                        Maintenance
                      </button>
                    </>)}

                    {/* MAINTENANCE */}
                    {unit.status === 'maintenance' && (
                      <button onClick={() => handleSimpleAction(unit.id, 'mark_vacant')}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-slate-200 hover:bg-slate-300 text-slate-700 text-sm font-medium rounded-lg transition-colors">
                        Mark Vacant
                      </button>
                    )}

                    {/* OCCUPIED */}
                    {unit.status === 'occupied' && (<>
                      <button onClick={() => openModal(unit, 'log_notice')}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-amber-50 border border-amber-300 hover:bg-amber-100 text-amber-700 text-sm font-medium rounded-lg transition-colors w-full justify-center">
                        <Bell size={14} /> Log Notice
                      </button>
                      <button onClick={() => openModal(unit, 'evacuation')}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-red-600 hover:bg-red-700 text-white text-sm font-medium rounded-lg transition-colors w-full justify-center">
                        <AlertTriangle size={14} /> Immediate Eviction
                      </button>
                    </>)}

                    {/* NOTICE GIVEN */}
                    {unit.status === 'notice_given' && (<>
                      <button onClick={() => openModal(unit, 'offboard')}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-orange-500 hover:bg-orange-600 text-white text-sm font-medium rounded-lg transition-colors">
                        <LogOut size={14} /> Offboard Tenant
                      </button>
                      <button onClick={() => handleSimpleAction(unit.id, 'revoke_notice')}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium rounded-lg transition-colors">
                        <BellOff size={14} /> Revoke Notice
                      </button>
                    </>)}

                    {/* DEPOSIT PENDING (offboarded, awaiting settlement) */}
                    {(unit.status === 'deposit_pending' || unit.status === 'offboarding') && (
                      <button onClick={() => openModal(unit, 'settle')}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium rounded-lg transition-colors">
                        <FileCheck size={14} /> Settle Deposit
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filteredUnits.length > 0 && (
          <Pagination
            currentPage={unitsPage}
            totalPages={totalUnitsPages}
            onPageChange={setUnitsPage}
          />
        )}
      </div>

      {settlementUnit && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-lg w-full p-6">
            <div className="flex items-center justify-between mb-5"><h2 className="text-xl font-bold">Start Move-In Settlement: {settlementUnit.unitCode}</h2><button onClick={() => setSettlementUnit(null)} className="text-slate-400"><X size={22} /></button></div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <label className="text-sm">Tenant Name<input required value={settlementForm.tenantName} onChange={(e) => setSettlementForm({ ...settlementForm, tenantName: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>
              <label className="text-sm">Tenant Email<input required type="email" value={settlementForm.tenantEmail} onChange={(e) => setSettlementForm({ ...settlementForm, tenantEmail: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>
              <label className="text-sm">Tenant Phone<input required type="tel" value={settlementForm.tenantPhone} onChange={(e) => setSettlementForm({ ...settlementForm, tenantPhone: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>
              <label className="text-sm">Expected Move-In Date<input required type="date" min={new Intl.DateTimeFormat('en-CA', { timeZone: 'Africa/Nairobi', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())} value={settlementForm.expectedMoveInDate} onChange={(e) => setSettlementForm({ ...settlementForm, expectedMoveInDate: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>
              <label className="text-sm">Deposit<input type="number" value={settlementForm.deposit} onChange={(e) => setSettlementForm({ ...settlementForm, deposit: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>
              <label className="text-sm">First Rent<input type="number" value={settlementForm.rent} onChange={(e) => setSettlementForm({ ...settlementForm, rent: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>
              <label className="text-sm">Garbage Fee<input type="number" value={settlementForm.garbage} onChange={(e) => setSettlementForm({ ...settlementForm, garbage: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>
              <label className="text-sm">Water Deposit<input type="number" value={settlementForm.waterDeposit} onChange={(e) => setSettlementForm({ ...settlementForm, waterDeposit: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label>
            </div>
            <div className="mt-5 bg-slate-50 rounded-xl p-4 space-y-2">
              <div className="flex justify-between"><span className="font-bold">Total Required</span><strong>{currency.format(Number(settlementForm.deposit || 0) + Number(settlementForm.rent || 0) + Number(settlementForm.garbage || 0) + Number(settlementForm.waterDeposit || 0) + recurringUtilityCharges.reduce((sum, charge) => sum + charge.amount, 0))}</strong></div>
              {recurringUtilityCharges.map((charge) => <div key={charge.name} className="flex justify-between text-sm text-slate-600"><span>{charge.name}</span><span>{currency.format(charge.amount)}</span></div>)}
              <p className="text-xs text-slate-500">Metered utility usage is billed from the tenant&apos;s move-in baseline reading, not estimated in advance.</p>
            </div>
            <button type="button" onClick={createSettlement} disabled={isCreatingSettlement || !settlementForm.tenantName.trim() || !settlementForm.tenantEmail.trim() || !settlementForm.tenantPhone.trim() || !settlementForm.expectedMoveInDate} className="mt-5 w-full bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-3 rounded-xl disabled:opacity-60">
              {isCreatingSettlement ? 'Creating Settlement...' : 'Create Settlement'}
            </button>
          </div>
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════════════
          MODAL: Log Move-Out Notice
      ════════════════════════════════════════════════════════════════════ */}
      {modal === 'log_notice' && activeUnit && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2"><Bell size={20} className="text-amber-500" /> Log Move-Out Notice</h2>
              <button onClick={closeModal} className="text-slate-400 hover:text-slate-600"><X size={22} /></button>
            </div>
            <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-800 space-y-2 mb-6">
              <p><strong>Unit:</strong> {activeUnit.unitCode} — {activeUnit.currentTenant}</p>
              <p>This will flag the tenant as moving out next month. The system will:</p>
              <ul className="list-disc ml-4 space-y-1">
                <li>Set the move-out deadline to the <strong>10th of next month</strong> (grace period).</li>
                <li><strong>Skip billing</strong> for next month's rent cycle.</li>
                <li>Alert you if the tenant overstays beyond the 10th.</li>
              </ul>
            </div>
            <div className="flex gap-3">
              <button onClick={closeModal} className="flex-1 py-2.5 rounded-xl border border-slate-300 text-slate-700 font-medium hover:bg-slate-50">Cancel</button>
              <button onClick={handleLogNotice} disabled={isLoggingNotice} className="flex-1 bg-amber-600 hover:bg-amber-700 text-white font-bold py-3 rounded-xl disabled:opacity-60">
                {isLoggingNotice ? 'Processing...' : 'Confirm & Log Notice'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════════════
          MODAL: Offboard Tenant (physical move-out after notice)
      ════════════════════════════════════════════════════════════════════ */}
      {modal === 'offboard' && activeUnit && (() => {
        const overstayed = isOverstayed(activeUnit);
        return (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50">
            <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6">
              <div className="flex justify-between items-center mb-4">
                <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
                  <LogOut size={20} className="text-orange-500" /> Offboard Tenant
                </h2>
                <button onClick={closeModal} className="text-slate-400 hover:text-slate-600"><X size={22} /></button>
              </div>

              <div className="bg-slate-50 border border-slate-200 rounded-xl p-4 text-sm space-y-1 mb-4">
                <div className="flex justify-between"><span className="text-slate-500">Tenant</span><span className="font-medium">{activeUnit.currentTenant}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">Move-Out Deadline</span><span className={`font-medium ${overstayed ? 'text-red-600' : 'text-amber-600'}`}>{activeUnit.expectedMoveOutDate || '—'}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">Today</span><span className="font-medium">{new Date().toISOString().slice(0, 10)}</span></div>
              </div>

              {overstayed ? (
                <div className="bg-red-50 border border-red-200 rounded-xl p-4 mb-4">
                  <p className="text-sm font-bold text-red-700 flex items-center gap-2 mb-2"><AlertTriangle size={16} /> Tenant has overstayed!</p>
                  <p className="text-xs text-red-600 mb-3">The move-out deadline has passed. You may apply an overstay penalty (negotiate with tenant). Leave blank to waive it.</p>
                  <label className="block text-sm font-medium text-slate-700 mb-1">Overstay Penalty (KES)</label>
                  <input
                    type="number" min="0" placeholder="0"
                    value={overstayPenalty}
                    onChange={e => setOverstayPenalty(e.target.value)}
                    className="w-full border border-red-300 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-red-400"
                  />
                  <p className="text-xs text-slate-500 mt-1">This will be deducted from the deposit at settlement.</p>
                </div>
              ) : (
                <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-3 mb-4 text-sm text-emerald-700">
                  ✅ Tenant is moving out within the grace period. No overstay charges apply.
                </div>
              )}

              <p className="text-sm text-slate-600 mb-4">
                Clicking <strong>"Confirm Move-Out"</strong> records that the tenant has physically handed over the keys today. The unit will move to <strong>Deposit Pending</strong> — you can settle the deposit separately at any time.
              </p>

              {offboardError && <p className="text-sm text-red-600 mb-3">{offboardError}</p>}

              <div className="flex gap-3">
                <button onClick={closeModal} className="flex-1 py-2.5 rounded-xl border border-slate-300 text-slate-700 font-medium hover:bg-slate-50">Cancel</button>
                <button onClick={handleOffboard} disabled={offboardLoading}
                  className="flex-1 py-2.5 rounded-xl bg-orange-500 hover:bg-orange-600 disabled:opacity-60 text-white font-bold">
                  {offboardLoading ? 'Processing...' : 'Confirm Move-Out'}
                </button>
              </div>
            </div>
          </div>
        );
      })()}

      {/* ════════════════════════════════════════════════════════════════════
          MODAL: Immediate Evacuation
      ════════════════════════════════════════════════════════════════════ */}
      {modal === 'evacuation' && activeUnit && (() => {
        const withinGrace = isWithinGracePeriod();
        const afterGrace = isAfterGracePeriod();
        const suggestedProrate = calcProratedRefund(activeUnit.depositAmount || 0);

        return (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50">
            <div className="bg-white rounded-2xl shadow-2xl max-w-lg w-full p-6 max-h-screen overflow-y-auto">
              <div className="flex justify-between items-center mb-4">
                <h2 className="text-xl font-bold text-red-700 flex items-center gap-2">
                  <AlertTriangle size={20} /> Immediate Eviction
                </h2>
                <button onClick={closeModal} className="text-slate-400 hover:text-slate-600"><X size={22} /></button>
              </div>

              <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-800 mb-4">
                <p>You are immediately evicting <strong>{activeUnit.currentTenant}</strong> from <strong>{activeUnit.unitCode}</strong>. This bypasses the standard notice period.</p>
              </div>

              {/* Reason */}
              <div className="mb-4">
                <label className="block text-sm font-bold text-slate-700 mb-2">Reason for Eviction <span className="text-red-500">*</span></label>
                <select
                  value={evacReason}
                  onChange={e => setEvacReason(e.target.value)}
                  className="w-full border border-slate-300 rounded-lg px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-red-400 bg-white"
                >
                  <option value="">— Select a reason —</option>
                  <option value="Criminal offense">Criminal offense</option>
                  <option value="Lease violation">Lease violation</option>
                  <option value="Severe rent default">Severe rent default (3+ months)</option>
                  <option value="Destruction of property">Destruction of property</option>
                  <option value="Nuisance / disturbance">Nuisance / disturbance</option>
                  <option value="Other">Other (specify below)</option>
                </select>
                {evacReason === 'Other' && (
                  <input
                    type="text" placeholder="Specify the reason..."
                    className="mt-2 w-full border border-slate-300 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-red-400"
                    onChange={e => setEvacReason(e.target.value)}
                  />
                )}
              </div>

              {/* Invoice handling — dynamic based on date */}
              <div className="border border-slate-200 rounded-xl p-4 mb-4 space-y-4">
                <p className="text-sm font-bold text-slate-700">Invoice Handling</p>

                {/* Within grace period (1st–10th): option to cancel invoice */}
                {withinGrace && (
                  <label className="flex items-start gap-3 cursor-pointer">
                    <input type="checkbox" checked={evacCancelInvoice}
                      onChange={e => { setEvacCancelInvoice(e.target.checked); if (e.target.checked) setEvacProrateRent(false); }}
                      className="mt-0.5 w-4 h-4 rounded accent-red-600"
                    />
                    <div>
                      <p className="text-sm font-medium text-slate-800">Cancel current month&apos;s invoice</p>
                      <p className="text-xs text-slate-500">Today is within the grace period (1st–10th). You may void the draft invoice since the tenant is leaving early in the month.</p>
                    </div>
                  </label>
                )}

                {/* After grace period: option to prorate refund */}
                {afterGrace && (
                  <label className="flex items-start gap-3 cursor-pointer">
                    <input type="checkbox" checked={evacProrateRent}
                      onChange={e => {
                        setEvacProrateRent(e.target.checked);
                        if (e.target.checked) {
                          setEvacCancelInvoice(false);
                          setEvacProrateAmount(suggestedProrate.toString());
                        } else {
                          setEvacProrateAmount('');
                        }
                      }}
                      className="mt-0.5 w-4 h-4 rounded accent-emerald-600"
                    />
                    <div>
                      <p className="text-sm font-medium text-slate-800">Prorate &amp; refund remaining rent</p>
                      <p className="text-xs text-slate-500">Today is past the 10th. If the tenant has already paid rent for this month, you may choose to refund the unused days as a credit.</p>
                    </div>
                  </label>
                )}

                {evacProrateRent && (
                  <div>
                    <label className="block text-xs font-medium text-slate-600 mb-1">Refund Amount (KES)</label>
                    <input
                      type="number" min="0"
                      value={evacProrateAmount}
                      onChange={e => setEvacProrateAmount(e.target.value)}
                      placeholder={`Suggested: ${suggestedProrate}`}
                      className="w-full border border-emerald-300 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-emerald-400"
                    />
                    <p className="text-xs text-slate-500 mt-1">This credit will be added to the deposit refund during settlement.</p>
                  </div>
                )}

                {!withinGrace && !afterGrace && (
                  <p className="text-xs text-slate-500">No special invoice action needed. The standard arrears deduction will apply during deposit settlement.</p>
                )}
              </div>

              {evacError && <p className="text-sm text-red-600 mb-3">{evacError}</p>}

              <div className="flex gap-3">
                <button onClick={closeModal} className="flex-1 py-2.5 rounded-xl border border-slate-300 text-slate-700 font-medium hover:bg-slate-50">Cancel</button>
                <button onClick={handleEvacuation} disabled={evacLoading}
                  className="flex-1 py-2.5 rounded-xl bg-red-600 hover:bg-red-700 disabled:opacity-60 text-white font-bold">
                  {evacLoading ? 'Processing...' : 'Confirm Eviction'}
                </button>
              </div>
            </div>
          </div>
        );
      })()}

      {/* ════════════════════════════════════════════════════════════════════
          DRAWER: Settle Deposit
      ════════════════════════════════════════════════════════════════════ */}
      {modal === 'settle' && activeUnit && (
        <div className="fixed inset-0 bg-black/50 flex justify-end z-50">
          <div className="bg-white w-full max-w-md h-full shadow-2xl p-6 overflow-y-auto">
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-xl font-bold text-slate-900">Settle Deposit: {activeUnit.unitCode}</h2>
              <button onClick={closeModal} className="text-slate-400 hover:text-slate-600"><X size={24} /></button>
            </div>

            {settlementDone ? (
              <div className="text-center py-8">
                <CheckCircle size={56} className="text-emerald-500 mx-auto mb-4" />
                <h3 className="text-xl font-bold text-slate-900">Deposit Settled!</h3>
                <p className="text-slate-500 mt-2 text-sm mb-6">The unit is now vacant and ready for a new tenant.</p>

                {settlementSummary && (
                  <div className="bg-slate-50 border border-slate-200 rounded-xl p-4 text-sm text-left space-y-2 mb-6">
                    <div className="flex justify-between"><span className="text-slate-500">Deposit Held</span><span>{currency.format(settlementSummary.depositHeld)}</span></div>
                    {settlementSummary.proratedRefundCredit > 0 && <div className="flex justify-between text-emerald-600"><span>Prorated Credit</span><span>+{currency.format(settlementSummary.proratedRefundCredit)}</span></div>}
                    {settlementSummary.totalDamages > 0 && <div className="flex justify-between text-red-600"><span>Damage Deductions</span><span>-{currency.format(settlementSummary.totalDamages)}</span></div>}
                    {settlementSummary.outstandingArrears > 0 && <div className="flex justify-between text-red-600"><span>Arrears Deducted</span><span>-{currency.format(settlementSummary.outstandingArrears)}</span></div>}
                    {settlementSummary.overstayPenalty > 0 && <div className="flex justify-between text-red-600"><span>Overstay Penalty</span><span>-{currency.format(settlementSummary.overstayPenalty)}</span></div>}
                    <div className="flex justify-between border-t border-slate-200 pt-2 mt-2 font-bold text-lg">
                      <span>Net Refund Paid</span>
                      <span className="text-emerald-600">{currency.format(settlementSummary.netRefund)}</span>
                    </div>
                  </div>
                )}

                <button onClick={closeModal} className="bg-slate-900 text-white px-6 py-2.5 rounded-xl font-medium">
                  Close
                </button>
              </div>
            ) : (
              <div className="space-y-5">
                {/* Tenant summary */}
                <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-2 text-sm">
                  <div className="flex justify-between"><span className="text-slate-500">Tenant</span><span className="font-medium">{activeUnit.currentTenant || '—'}</span></div>
                  <div className="flex justify-between"><span className="text-slate-500">Deposit Held</span><span className="font-bold text-emerald-700">{currency.format(depositHeld)}</span></div>
                  {settleArrears > 0 && <div className="flex justify-between text-red-600"><span>Outstanding Arrears</span><span className="font-bold">-{currency.format(settleArrears)}</span></div>}
                  {settleOverstay > 0 && <div className="flex justify-between text-red-600"><span>Overstay Penalty</span><span className="font-bold">-{currency.format(settleOverstay)}</span></div>}
                  {settleProrated > 0 && <div className="flex justify-between text-emerald-600"><span>Prorated Credit</span><span className="font-bold">+{currency.format(settleProrated)}</span></div>}
                </div>

                <p className="text-xs text-slate-500">
                  Note: Outstanding arrears, overstay penalties, and any prorated credits recorded during offboarding are automatically applied by the server during settlement.
                </p>

                {/* Damage items */}
                <div>
                  <label className="block text-sm font-bold text-slate-700 mb-3">Damage Deductions</label>
                  {damages.length > 0 && (
                    <div className="space-y-2 mb-3">
                      {damages.map((d, i) => (
                        <div key={i} className="flex justify-between items-center bg-red-50 border border-red-100 rounded-lg px-3 py-2 text-sm">
                          <span className="text-slate-700">{d.description}</span>
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-red-600">{currency.format(d.amount)}</span>
                            <button onClick={() => removeDamageItem(i)} className="text-slate-400 hover:text-red-500"><Trash2 size={14} /></button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="flex gap-2">
                    <input type="text" placeholder="e.g. Broken window" value={newDamageDesc}
                      onChange={e => setNewDamageDesc(e.target.value)}
                      className="flex-1 border border-slate-300 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-emerald-500"
                    />
                    <input type="number" min="0" placeholder="KES" value={newDamageAmount}
                      onChange={e => setNewDamageAmount(e.target.value)}
                      className="w-24 border border-slate-300 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-emerald-500"
                    />
                    <button type="button" onClick={addDamageItem}
                      className="bg-slate-100 hover:bg-slate-200 border border-slate-200 px-3 py-2 rounded-lg">
                      <Plus size={16} />
                    </button>
                  </div>
                </div>

                {/* Financial summary */}
                <div className="bg-slate-50 rounded-xl border border-slate-200 p-4 space-y-2 text-sm">
                  <div className="flex justify-between"><span className="text-slate-500">Deposit Held</span><span>{currency.format(depositHeld)}</span></div>
                  {settleProrated > 0 && <div className="flex justify-between text-emerald-600"><span>Prorated Credit (stored)</span><span>+{currency.format(settleProrated)}</span></div>}
                  <div className="flex justify-between text-red-600"><span>Damage Deductions</span><span>-{currency.format(totalDamages)}</span></div>
                  {settleArrears > 0 && <div className="flex justify-between text-red-600"><span>Arrears (auto-deducted)</span><span>-{currency.format(settleArrears)}</span></div>}
                  {settleOverstay > 0 && <div className="flex justify-between text-red-600"><span>Overstay Penalty</span><span>-{currency.format(settleOverstay)}</span></div>}
                  <div className="flex justify-between border-t border-slate-200 pt-2 mt-1 font-bold text-base">
                    <span className="text-slate-900">Est. Net Refund</span>
                    <span className="text-emerald-600">{currency.format(netRefund)}</span>
                  </div>
                  <p className="text-xs text-slate-400">Final amount computed server-side based on all stored deductions.</p>
                </div>

                {/* M-Pesa ref */}
                <div>
                  <label className="block text-sm font-bold text-slate-700 mb-2">M-Pesa Reference Code</label>
                  <input type="text" placeholder="e.g. QWE1234567" value={mpesaRef}
                    onChange={e => setMpesaRef(e.target.value.toUpperCase())}
                    className="w-full border border-slate-300 rounded-lg px-3 py-2.5 outline-none focus:ring-2 focus:ring-emerald-500 uppercase font-mono text-sm tracking-wider"
                  />
                </div>

                {settlementError && <p className="text-sm text-red-600 font-medium">{settlementError}</p>}

                <button onClick={handleSettleDeposit} disabled={isSettling}
                  className="w-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-70 text-white font-bold py-3 rounded-xl transition-colors flex items-center justify-center gap-2">
                  <CheckCircle size={18} />
                  {isSettling ? 'Processing...' : 'Confirm Settlement & Vacate Unit'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

    </div>
  );
}