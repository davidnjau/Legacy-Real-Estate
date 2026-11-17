'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { MessageSquare, Clock, CheckCircle2, X, Send, Wrench, AlertCircle, LayoutDashboard, List, Calendar, Camera } from 'lucide-react';
import { usePathname } from 'next/navigation';

type Ticket = {
  id: string;
  propertyId: number;
  propertyName: string;
  ticketId: string;
  category: string;
  description: string;
  priority: string;
  status: string;
  unitCode: string;
  tenantName: string;
  createdAt: string;
  assignedToId: number | null;
  canUpdate: boolean;
  evidence: Array<{ name: string; download_path: string }>;
};

type PropertyRecord = { id: number; name: string };
type UnitRecord = { id: number; property_id: number; code: string };
type CaretakerRecord = { id: number; full_name: string; property_id: number };

type Comment = {
  id: string;
  sender_role: string;
  sender_name: string;
  message: string;
  created_at: string;
};

type LedgerRecord = {
  id: string;
  ticketId: string;
  unitCode: string;
  tenantName: string;
  category: string;
  description: string;
  fundiName: string;
  cost: number | null;
  resolvedAt: string;
};

const priorityBadge = (p: string) =>
  p === 'high' ? 'bg-red-100 text-red-700' :
  p === 'low' ? 'bg-emerald-100 text-emerald-700' :
  'bg-blue-100 text-blue-700';

const statusBadge = (s: string) =>
  ['open', 'assigned', 'quotation', 'awaiting_landlord_approval'].includes(s) ? 'bg-amber-100 text-amber-700' :
  s === 'in_progress' ? 'bg-blue-100 text-blue-700' :
  ['completed', 'closed'].includes(s) ? 'bg-emerald-100 text-emerald-700' :
  'bg-slate-100 text-slate-600';

const fmt = (d: string) =>
  new Date(d).toLocaleString('en-KE', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

export default function MaintenanceBoard() {
  const pathname = usePathname();
  const isCaretakerPortal = pathname.startsWith('/caretaker/');
  const isPropertyOfficerPortal = pathname.startsWith('/property-officer/');
  const [viewMode, setViewMode] = useState<'board' | 'ledger'>('board');

  // Board State
  const [pending, setPending] = useState<Ticket[]>([]);
  const [inProgress, setInProgress] = useState<Ticket[]>([]);
  const [resolved, setResolved] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [properties, setProperties] = useState<PropertyRecord[]>([]);
  const [units, setUnits] = useState<UnitRecord[]>([]);
  const [caretakers, setCaretakers] = useState<CaretakerRecord[]>([]);
  const [selectedPropertyId, setSelectedPropertyId] = useState('');
  const [selectedUnitId, setSelectedUnitId] = useState('');
  const [newTicket, setNewTicket] = useState({
    title: '',
    description: '',
    category: 'other',
    priority: 'normal',
  });
  const [creatingTicket, setCreatingTicket] = useState(false);
  const [assignmentIds, setAssignmentIds] = useState<Record<string, string>>({});
  const [activeTicket, setActiveTicket] = useState<Ticket | null>(null);
  const [comments, setComments] = useState<Comment[]>([]);
  const [loadingComments, setLoadingComments] = useState(false);
  const [reply, setReply] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [isUpdatingStatus, setIsUpdatingStatus] = useState(false);
  const [resolvingTicketId, setResolvingTicketId] = useState<string | null>(null);
  const [fundiName, setFundiName] = useState('');
  const [repairCost, setRepairCost] = useState('');
  const [completionPhotos, setCompletionPhotos] = useState<File[]>([]);
  const [completionPhotoUploaded, setCompletionPhotoUploaded] = useState(false);
  const [uploadingCompletionPhotos, setUploadingCompletionPhotos] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  // Ledger State
  const [availableMonths, setAvailableMonths] = useState<string[]>([]);
  const [selectedMonth, setSelectedMonth] = useState<string>('');
  const [ledgerRows, setLedgerRows] = useState<LedgerRecord[]>([]);
  const [ledgerSummary, setLedgerSummary] = useState({ totalSpend: 0, totalRepairs: 0 });
  const [loadingLedger, setLoadingLedger] = useState(false);
  const [ledgerError, setLedgerError] = useState('');

  const loadTickets = async () => {
    try {
      const response = await fetch('/api/maintenance/landlord');
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error);
      setPending(payload.pending || []);
      setInProgress(payload.inProgress || []);
      setResolved(payload.resolved || []);
      setLoadError('');
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Unable to load maintenance tickets.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadTickets(); }, []);

  useEffect(() => {
    if (!isPropertyOfficerPortal) return;
    let active = true;
    const fetchPayload = async (url: string) => {
      const response = await fetch(url, { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || 'Unable to load maintenance records.');
      }
      return payload;
    };
    Promise.all([
      fetchPayload('/api/property-officer/properties'),
      fetchPayload('/api/property-officer/units'),
      fetchPayload('/api/property-officer/caretakers'),
    ]).then(([propertyPayload, unitPayload, caretakerPayload]) => {
      if (!active) return;
      const propertyRows = Array.isArray(propertyPayload.properties) ? propertyPayload.properties : [];
      setProperties(propertyRows);
      setUnits(Array.isArray(unitPayload.units) ? unitPayload.units : []);
      setCaretakers(Array.isArray(caretakerPayload.caretakers) ? caretakerPayload.caretakers : []);
      setSelectedPropertyId((current) => current || String(propertyRows[0]?.id ?? ''));
    }).catch((error: unknown) => {
      if (active) {
        setLoadError(error instanceof Error ? error.message : 'Unable to load property assignments.');
      }
    });
    return () => { active = false; };
  }, [isPropertyOfficerPortal]);

  useEffect(() => {
    if (isCaretakerPortal) return;
    const fetchMonths = async () => {
      try {
        const res = await fetch('/api/maintenance/ledger/months');
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Unable to load ledger months.');
        setAvailableMonths(data.months || []);
        if (data.months && data.months.length > 0) {
          setSelectedMonth(data.months[0]);
        }
      } catch (error) {
        setLedgerError(error instanceof Error ? error.message : 'Unable to load ledger months.');
      }
    };
    fetchMonths();
  }, [isCaretakerPortal]);

  useEffect(() => {
    if (!isCaretakerPortal) return;
    let active = true;
    fetch('/api/caretaker/dashboard', { cache: 'no-store' })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || payload.error || 'Unable to load assigned properties.');
        }
        return payload as { properties?: PropertyRecord[] };
      })
      .then((payload) => {
        if (!active) return;
        const assignedProperties = Array.isArray(payload.properties) ? payload.properties : [];
        setProperties(assignedProperties);
        setSelectedPropertyId((current) =>
          assignedProperties.some((property) => String(property.id) === current)
            ? current
            : String(assignedProperties[0]?.id ?? ''),
        );
      })
      .catch((error: unknown) => {
        if (active) {
          setLoadError(error instanceof Error ? error.message : 'Unable to load assigned properties.');
        }
      });
    return () => { active = false; };
  }, [isCaretakerPortal]);

  // Fetch Ledger data when month changes
  useEffect(() => {
    if (!selectedMonth || viewMode !== 'ledger') return;
    const fetchLedger = async () => {
      setLoadingLedger(true);
      setLedgerError('');
      try {
        const res = await fetch(`/api/maintenance/ledger?month=${selectedMonth}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Unable to load maintenance ledger.');
        setLedgerRows(data.ledger || []);
        setLedgerSummary(data.summary || { totalSpend: 0, totalRepairs: 0 });
      } catch (error) {
        setLedgerError(error instanceof Error ? error.message : 'Unable to load maintenance ledger.');
      }
      finally {
        setLoadingLedger(false);
      }
    };
    fetchLedger();
  }, [selectedMonth, viewMode]);

  const openTicket = async (ticket: Ticket) => {
    setActiveTicket(ticket);
    setReply('');
    setLoadingComments(true);
    try {
      const res = await fetch(`/api/maintenance/comment?ticketId=${ticket.id}`);
      const data = await res.json();
      setComments(data.comments || []);
    } catch {
      setComments([]);
    } finally {
      setLoadingComments(false);
    }
  };

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [comments]);

  const handleStatusChange = async (
    ticketId: string,
    newStatus: string,
    resolution?: { fundiName: string; repairCost: string },
    assignedTo?: number,
  ): Promise<boolean> => {
    setIsUpdatingStatus(true);
    try {
      const response = await fetch('/api/maintenance/landlord', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticketId,
          status: newStatus,
          ...(assignedTo !== undefined ? { assignedTo } : {}),
          ...(resolution
            ? { fundiName: resolution.fundiName, repairCost: resolution.repairCost }
            : {}),
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Failed to update status');

      await loadTickets();
      if (activeTicket?.id === ticketId) {
        setActiveTicket(prev => prev ? { ...prev, status: newStatus } : null);
      }
      return true;
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Failed to update ticket status.');
      return false;
    } finally {
      setIsUpdatingStatus(false);
    }
  };

  const assignCaretaker = async (ticket: Ticket) => {
    const caretakerId = Number(assignmentIds[ticket.id]);
    if (!Number.isInteger(caretakerId) || caretakerId <= 0) return;
    if (await handleStatusChange(ticket.id, 'assigned', undefined, caretakerId)) {
      setAssignmentIds((current) => ({ ...current, [ticket.id]: '' }));
    }
  };

  const createTicket = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setCreatingTicket(true);
    setLoadError('');
    try {
      const response = await fetch('/api/maintenance/landlord', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          property_id: Number(selectedPropertyId),
          ...(selectedUnitId ? { unit_id: Number(selectedUnitId) } : {}),
          ...newTicket,
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || 'Unable to create the ticket.');
      }
      setNewTicket({ title: '', description: '', category: 'other', priority: 'normal' });
      setSelectedUnitId('');
      await loadTickets();
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Unable to create the maintenance ticket.');
    } finally {
      setCreatingTicket(false);
    }
  };

  const startResolution = (ticketId: string) => {
    setResolvingTicketId(ticketId);
    setFundiName('');
    setRepairCost('');
    setCompletionPhotos([]);
    setCompletionPhotoUploaded(false);
  };

  const handleResolveSubmit = async () => {
    if (!resolvingTicketId) return;
    const cost = Number(repairCost);
    if (!fundiName.trim() || !repairCost.trim() || !Number.isFinite(cost) || cost < 0) {
      alert('Enter the technician name and a valid non-negative repair cost.');
      return;
    }
    if (isCaretakerPortal && !completionPhotoUploaded && completionPhotos.length === 0) {
      alert('Add at least one completion photo before completing this ticket.');
      return;
    }
    if (isCaretakerPortal && !completionPhotoUploaded) {
      setUploadingCompletionPhotos(true);
      try {
        const formData = new FormData();
        completionPhotos.forEach((photo) => formData.append('photos', photo));
        const response = await fetch(
          `/api/maintenance/${resolvingTicketId}/completion-photos`,
          { method: 'POST', body: formData },
        );
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || payload.error || 'Unable to upload completion photos.');
        }
        setCompletionPhotoUploaded(true);
        setCompletionPhotos([]);
      } catch (error) {
        alert(error instanceof Error ? error.message : 'Unable to upload completion photos.');
        return;
      } finally {
        setUploadingCompletionPhotos(false);
      }
    }
    const completed = await handleStatusChange(resolvingTicketId, 'completed', {
      fundiName: fundiName.trim(),
      repairCost,
    });
    if (completed) {
      setResolvingTicketId(null);
      setFundiName('');
      setRepairCost('');
      setCompletionPhotos([]);
      setCompletionPhotoUploaded(false);
    }
  };

  const handleSendReply = async () => {
    if (!activeTicket || !reply.trim()) return;
    setIsSending(true);
    try {
      const res = await fetch('/api/maintenance/comment', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticketId: activeTicket.id, message: reply.trim() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      setReply('');
      const commentsRes = await fetch(`/api/maintenance/comment?ticketId=${activeTicket.id}`);
      const commentsData = await commentsRes.json();
      setComments(commentsData.comments || []);
      await loadTickets();
      const allTickets = [...pending, ...inProgress, ...resolved];
      const refreshed = allTickets.find(t => t.id === activeTicket.id);
      if (refreshed) setActiveTicket(refreshed);
    } catch (err: any) {
      alert(err.message || 'Failed to send reply.');
    } finally {
      setIsSending(false);
    }
  };

  const TicketCard = ({ ticket }: { ticket: Ticket }) => (
    <div
      onClick={() => openTicket(ticket)}
      className="bg-white p-4 rounded-xl shadow-sm border border-slate-200 space-y-3 cursor-pointer hover:shadow-md hover:border-emerald-300 transition-all"
    >
      <div className="flex justify-between items-start">
        <span className={`text-xs font-bold px-2 py-0.5 rounded-full capitalize ${priorityBadge(ticket.priority)}`}>
          {ticket.priority} Priority
        </span>
        <span className="text-xs text-slate-400">{ticket.unitCode} • {ticket.tenantName}</span>
      </div>
      <p className="font-medium text-slate-900 text-sm line-clamp-2">{ticket.description}</p>
      <p className="text-xs text-slate-500">{ticket.propertyName} · Unit {ticket.unitCode}</p>
      <div className="flex justify-between items-center">
        <span className="text-xs text-slate-400">{ticket.category}</span>
        <span className="text-xs text-slate-400">{new Date(ticket.createdAt).toLocaleDateString('en-KE', { month: 'short', day: 'numeric' })}</span>
      </div>
      <div className="flex gap-2">
        {isPropertyOfficerPortal && ticket.status === 'open' && (
          <div className="flex min-w-0 flex-1 gap-1" onClick={(event) => event.stopPropagation()}>
            <select
              aria-label={`Assign ${ticket.ticketId} to caretaker`}
              value={assignmentIds[ticket.id] ?? ''}
              onChange={(event) => setAssignmentIds((current) => ({
                ...current,
                [ticket.id]: event.target.value,
              }))}
              className="min-w-0 flex-1 rounded-lg border border-slate-300 px-2 py-1 text-xs"
            >
              <option value="">Select caretaker</option>
              {caretakers
                .filter((caretaker) => caretaker.property_id === ticket.propertyId)
                .map((caretaker) => (
                  <option key={caretaker.id} value={caretaker.id}>{caretaker.full_name}</option>
                ))}
            </select>
            <button
              type="button"
              onClick={() => void assignCaretaker(ticket)}
              disabled={!assignmentIds[ticket.id] || isUpdatingStatus}
              className="rounded-lg border border-blue-300 px-2 py-1 text-xs font-semibold text-blue-800 disabled:opacity-50"
            >
              Assign
            </button>
          </div>
        )}
        {isCaretakerPortal && ticket.canUpdate && ticket.status === 'open' && (
          <button
            onClick={(e) => { e.stopPropagation(); handleStatusChange(ticket.id, 'assigned'); }}
            className="flex-1 py-1.5 border border-blue-300 text-blue-700 rounded-lg text-xs font-medium hover:bg-blue-50 transition-colors"
          >
            Assign to me
          </button>
        )}
        {isCaretakerPortal && ticket.canUpdate && ticket.status === 'assigned' && (
          <button
            onClick={(e) => { e.stopPropagation(); handleStatusChange(ticket.id, 'in_progress'); }}
            className="flex-1 py-1.5 border border-blue-300 text-blue-700 rounded-lg text-xs font-medium hover:bg-blue-50 transition-colors"
          >
            Start Work
          </button>
        )}
        {isCaretakerPortal && ticket.canUpdate && ticket.status === 'in_progress' && (
          <button
            onClick={(e) => { e.stopPropagation(); startResolution(ticket.id); }}
            className="flex-1 py-1.5 border border-emerald-300 text-emerald-700 rounded-lg text-xs font-medium hover:bg-emerald-50 transition-colors"
          >
            Complete & Log Cost
          </button>
        )}
        <button
          onClick={(e) => { e.stopPropagation(); openTicket(ticket); }}
          className="flex-1 py-1.5 bg-slate-900 text-white rounded-lg text-xs font-medium hover:bg-slate-800 transition-colors"
        >
          View & Reply
        </button>
      </div>
    </div>
  );

  return (
    <div className="space-y-6 pb-20">
      <header>
          <h1 className="text-3xl font-bold text-slate-900">Maintenance & Repairs</h1>
          <p className="text-slate-500 mt-1">
            {isCaretakerPortal
              ? 'Report property-wide issues and manage the tickets assigned to you.'
              : 'Manage tenant repair requests and track maintenance expenses.'}
          </p>
      </header>

      {loadError && (
        <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {loadError}
        </p>
      )}

      <div className="grid items-start gap-5 lg:grid-cols-2">
        {(isPropertyOfficerPortal || isCaretakerPortal) && (
          <form onSubmit={createTicket} className="grid gap-3 rounded-xl border border-slate-200 bg-white p-4 md:grid-cols-2">
            <h2 className="font-bold text-slate-900 md:col-span-2">
              {isCaretakerPortal ? 'Report an on-site issue' : 'Create maintenance ticket'}
            </h2>
            <label className="text-sm font-medium">Property
              <select
                required
                value={selectedPropertyId}
                onChange={(event) => {
                  setSelectedPropertyId(event.target.value);
                  setSelectedUnitId('');
                }}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              >
                <option value="">Select property</option>
                {properties.map((property) => (
                  <option key={property.id} value={property.id}>{property.name}</option>
                ))}
              </select>
            </label>
            {isPropertyOfficerPortal && (
              <label className="text-sm font-medium">Unit (optional)
                <select
                  value={selectedUnitId}
                  onChange={(event) => setSelectedUnitId(event.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                >
                  <option value="">Property-wide</option>
                  {units
                    .filter((unit) => String(unit.property_id) === selectedPropertyId)
                    .map((unit) => <option key={unit.id} value={unit.id}>{unit.code}</option>)}
                </select>
              </label>
            )}
            <label className="text-sm font-medium">Title
              <input
                required
                maxLength={255}
                value={newTicket.title}
                onChange={(event) => setNewTicket({ ...newTicket, title: event.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-sm font-medium">Category
                <select
                  value={newTicket.category}
                  onChange={(event) => setNewTicket({ ...newTicket, category: event.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                >
                  {['plumbing', 'electrical', 'structural', 'security', 'other']
                    .map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </label>
              <label className="text-sm font-medium">Priority
                <select
                  value={newTicket.priority}
                  onChange={(event) => setNewTicket({ ...newTicket, priority: event.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                >
                  {['low', 'normal', 'high', 'urgent']
                    .map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </label>
            </div>
            <label className="text-sm font-medium md:col-span-2">Description
              <textarea
                required
                maxLength={5000}
                rows={3}
                value={newTicket.description}
                onChange={(event) => setNewTicket({ ...newTicket, description: event.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <button
              type="submit"
              disabled={creatingTicket || !selectedPropertyId}
              className="rounded-lg bg-emerald-800 px-4 py-2 text-sm font-bold text-white disabled:opacity-50 md:col-span-2"
            >
              {creatingTicket ? 'Saving ticket…' : 'Create ticket'}
            </button>
          </form>
        )}

        {!isCaretakerPortal && (
          <div className="flex min-h-14 items-center justify-end rounded-xl border border-slate-200 bg-white p-3">
          {/* View Toggle */}
          <div className="flex p-1 bg-slate-200 rounded-lg">
          <button
            onClick={() => setViewMode('board')}
            className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-bold transition-all ${
              viewMode === 'board' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            <LayoutDashboard size={16} /> Board
          </button>
          <button
            onClick={() => setViewMode('ledger')}
            className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-bold transition-all ${
              viewMode === 'ledger' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            <List size={16} /> Ledger
          </button>
          </div>
          </div>
        )}
      </div>

      {isCaretakerPortal || viewMode === 'board' ? (
        /* ─── KANBAN BOARD VIEW ─── */
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          {loading ? (
            <div className="col-span-3 text-center py-12 text-slate-500">Loading board...</div>
          ) : (
            <>
              {/* Pending */}
              <div className="bg-amber-50 rounded-xl p-4 border border-amber-200">
                <div className="flex items-center gap-2 mb-4">
                  <AlertCircle size={18} className="text-amber-600" />
                  <h3 className="font-bold text-amber-800">Pending ({pending.length})</h3>
                </div>
                {pending.length === 0 ? (
                  <div className="text-center py-8 text-amber-600/50 text-sm">No pending requests.</div>
                ) : (
                  <div className="space-y-3">
                    {pending.map(t => <TicketCard key={t.id} ticket={t} />)}
                  </div>
                )}
              </div>

              {/* In Progress */}
              <div className="bg-blue-50 rounded-xl p-4 border border-blue-200">
                <div className="flex items-center gap-2 mb-4">
                  <Wrench size={18} className="text-blue-600" />
                  <h3 className="font-bold text-blue-800">In Progress ({inProgress.length})</h3>
                </div>
                {inProgress.length === 0 ? (
                  <div className="text-center py-8 text-blue-600/50 text-sm">No active repairs.</div>
                ) : (
                  <div className="space-y-3">
                    {inProgress.map(t => <TicketCard key={t.id} ticket={t} />)}
                  </div>
                )}
              </div>

              {/* Resolved */}
              <div className="bg-slate-100 rounded-xl p-4 border border-slate-200">
                <div className="flex items-center gap-2 mb-4">
                  <CheckCircle2 size={18} className="text-slate-500" />
                  <h3 className="font-bold text-slate-600">Resolved ({resolved.length})</h3>
                </div>
                {resolved.length === 0 ? (
                  <div className="text-center py-8 text-slate-400 text-sm">No resolved requests.</div>
                ) : (
                  <div className="space-y-3">
                    {resolved.map(t => <TicketCard key={t.id} ticket={t} />)}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      ) : (
        /* ─── MAINTENANCE LEDGER VIEW ─── */
        <div className="space-y-6">
          {ledgerError && (
            <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {ledgerError}
            </p>
          )}
          <div className="flex justify-between items-center bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
            <div className="flex items-center gap-4">
              <div className="relative">
                <select
                  value={selectedMonth}
                  onChange={(e) => setSelectedMonth(e.target.value)}
                  className="appearance-none bg-slate-50 border border-slate-300 rounded-lg pl-10 pr-10 py-2 text-slate-700 font-medium focus:outline-none focus:ring-2 focus:ring-slate-900 cursor-pointer"
                >
                  {availableMonths.map((m) => (
                    <option key={m} value={m}>
                      {new Date(`${m}-01`).toLocaleString('default', { month: 'long', year: 'numeric' })}
                    </option>
                  ))}
                </select>
                <Calendar size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
              </div>
              <p className="text-sm text-slate-500">{ledgerSummary.totalRepairs} repairs completed in this period</p>
            </div>
            <div className="text-right">
              <p className="text-sm font-medium text-slate-500">Total Monthly Spend</p>
              <h2 className="text-2xl font-bold text-slate-900">{currency.format(ledgerSummary.totalSpend)}</h2>
            </div>
          </div>

          <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-sm">
                <thead>
                  <tr className="bg-slate-900 text-white">
                    <th className="p-4 font-medium">Unit & Tenant</th>
                    <th className="p-4 font-medium">Ticket ID</th>
                    <th className="p-4 font-medium">Issue Description</th>
                    <th className="p-4 font-medium">Fundi / Technician</th>
                    <th className="p-4 font-medium text-right">Cost</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {loadingLedger ? (
                    <tr><td colSpan={5} className="p-12 text-center text-slate-500">Loading ledger data...</td></tr>
                  ) : ledgerRows.length === 0 ? (
                    <tr><td colSpan={5} className="p-12 text-center text-slate-500">No maintenance records found for this month.</td></tr>
                  ) : (
                    ledgerRows.map((row) => (
                      <tr key={row.id} className="hover:bg-slate-50 transition-colors">
                        <td className="p-4">
                          <div className="font-bold text-slate-900">{row.unitCode}</div>
                          <div className="text-slate-500 text-xs mt-0.5">{row.tenantName}</div>
                        </td>
                        <td className="p-4 font-mono text-slate-500 text-xs">{row.ticketId}</td>
                        <td className="p-4 text-slate-700 max-w-sm">
                          <span className="font-semibold text-slate-900 mr-2">{row.category}</span>
                          <span className="line-clamp-2">{row.description}</span>
                        </td>
                        <td className="p-4 text-slate-700 font-medium">
                          {row.fundiName}
                        </td>
                        <td className="p-4 text-right font-bold text-slate-900">
                          {row.cost === null ? 'Not recorded' : currency.format(row.cost)}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {resolvingTicketId && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-[100] p-4">
          <div className="bg-white rounded-2xl w-full max-w-md shadow-2xl overflow-hidden">
            <div className="p-5 border-b border-slate-100 flex justify-between items-center bg-slate-50">
              <h2 className="font-bold text-slate-900 text-lg">
                {isCaretakerPortal ? 'Complete On-Site Ticket' : 'Log Repair Expense'}
              </h2>
              <button
                onClick={() => setResolvingTicketId(null)}
                disabled={isUpdatingStatus}
                className="text-slate-400 hover:text-slate-600 disabled:opacity-50"
                aria-label="Close repair expense form"
              >
                <X size={20} />
              </button>
            </div>
            <div className="p-6 space-y-5">
              <p className="text-sm text-slate-600">
                {isCaretakerPortal
                  ? 'Record the technician, repair cost, and completion photos before closing your work.'
                  : 'Record the technician and actual repair cost before completing this ticket.'}
              </p>
              {isCaretakerPortal && (
                <label className="block text-sm font-bold text-slate-700">
                  Completion photos
                  <span className="mt-1 flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 font-normal">
                    <Camera size={16} />
                    {completionPhotoUploaded
                      ? 'Completion photos uploaded'
                      : `${completionPhotos.length} photo${completionPhotos.length === 1 ? '' : 's'} selected`}
                  </span>
                  {!completionPhotoUploaded && (
                    <input
                      type="file"
                      accept="image/jpeg,image/png,image/webp"
                      capture="environment"
                      multiple
                      onChange={(event) => setCompletionPhotos(Array.from(event.target.files ?? []))}
                      className="mt-2 block w-full text-sm font-normal"
                    />
                  )}
                </label>
              )}
              <div>
                <label htmlFor="resolution-fundi" className="block text-sm font-bold text-slate-700 mb-1.5">
                  Fundi / Technician Name
                </label>
                <input
                  id="resolution-fundi"
                  type="text"
                  maxLength={255}
                  value={fundiName}
                  onChange={(event) => setFundiName(event.target.value)}
                  className="w-full border border-slate-300 rounded-lg px-3 py-2.5 outline-none focus:ring-2 focus:ring-emerald-500"
                  required
                />
              </div>
              <div>
                <label htmlFor="resolution-cost" className="block text-sm font-bold text-slate-700 mb-1.5">
                  Repair Cost (KES)
                </label>
                <input
                  id="resolution-cost"
                  type="number"
                  min="0"
                  step="0.01"
                  value={repairCost}
                  onChange={(event) => setRepairCost(event.target.value)}
                  className="w-full border border-slate-300 rounded-lg px-3 py-2.5 outline-none focus:ring-2 focus:ring-emerald-500 font-mono"
                  required
                />
              </div>
              <button
                onClick={handleResolveSubmit}
                disabled={
                  isUpdatingStatus
                  || uploadingCompletionPhotos
                  || !fundiName.trim()
                  || !repairCost.trim()
                  || (isCaretakerPortal && !completionPhotoUploaded && completionPhotos.length === 0)
                }
                className="w-full bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-300 text-white font-bold py-3 rounded-xl transition-colors"
              >
                {uploadingCompletionPhotos
                  ? 'Uploading photos…'
                  : isUpdatingStatus
                    ? 'Saving...'
                    : 'Complete Ticket & Save Record'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ─── Conversation Drawer ─── */}
      {activeTicket && (
        <div className="fixed inset-0 bg-black/50 flex justify-end z-50">
          <div className="bg-white w-full max-w-lg h-full flex flex-col shadow-2xl">
            <div className="p-5 border-b border-slate-200 bg-slate-50 flex justify-between items-start shrink-0">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <span className={`text-xs font-bold px-2 py-0.5 rounded-full capitalize ${priorityBadge(activeTicket.priority)}`}>
                    {activeTicket.priority} Priority
                  </span>
                  <span className={`text-xs font-bold px-2 py-0.5 rounded-full capitalize ${statusBadge(activeTicket.status)}`}>
                    {activeTicket.status.replaceAll('_', ' ')}
                  </span>
                </div>
                <h2 className="font-bold text-slate-900 text-lg leading-tight">{activeTicket.description}</h2>
                <p className="text-xs text-slate-500 mt-1">{activeTicket.category} • {activeTicket.unitCode} • {activeTicket.tenantName}</p>
                {activeTicket.evidence.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-2">
                    {activeTicket.evidence.map((item, index) => (
                      <a
                        key={`${activeTicket.id}-${index}`}
                        href={`/api/maintenance/${activeTicket.id}/evidence/${index}`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs text-emerald-700 underline"
                      >
                        {item.name}
                      </a>
                    ))}
                  </div>
                )}
              </div>
              <button onClick={() => setActiveTicket(null)} className="text-slate-400 hover:text-slate-600 ml-4 shrink-0">
                <X size={22} />
              </button>
            </div>

            <div className="px-5 py-3 border-b border-slate-100 flex gap-2 shrink-0 bg-white">
              {isCaretakerPortal && activeTicket.canUpdate && activeTicket.status === 'open' && (
                <button
                  onClick={() => handleStatusChange(activeTicket.id, 'assigned')}
                  disabled={isUpdatingStatus}
                  className="flex-1 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-bold transition-colors disabled:opacity-60"
                >
                  Assign to me
                </button>
              )}
              {isCaretakerPortal && activeTicket.canUpdate && activeTicket.status === 'assigned' && (
                <button
                  onClick={() => handleStatusChange(activeTicket.id, 'in_progress')}
                  disabled={isUpdatingStatus}
                  className="flex-1 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-sm font-bold transition-colors disabled:opacity-60"
                >
                  Start Work
                </button>
              )}
              {isCaretakerPortal && activeTicket.canUpdate && activeTicket.status === 'in_progress' && (
                <button
                  onClick={() => {
                    startResolution(activeTicket.id);
                    setActiveTicket(null);
                  }}
                  disabled={isUpdatingStatus}
                  className="flex-1 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-sm font-bold transition-colors disabled:opacity-60"
                >
                  Complete & Log Cost
                </button>
              )}
              {(activeTicket.status === 'completed' || activeTicket.status === 'closed') && (
                <div className="flex-1 py-2 bg-slate-100 text-slate-500 rounded-lg text-sm font-medium text-center">
                  Ticket Completed
                </div>
              )}
            </div>

            <div className="flex-1 overflow-y-auto p-5 space-y-4 bg-slate-50">
              {loadingComments ? (
                <div className="text-center text-slate-400 py-8 text-sm">Loading conversation...</div>
              ) : comments.length === 0 ? (
                <div className="text-center text-slate-400 py-12 text-sm">
                  <MessageSquare size={32} className="mx-auto mb-2 opacity-30" />
                  No messages yet. Send a reply to acknowledge the request.
                </div>
              ) : (
                comments.map((c) => {
                  const isLandlord = c.sender_role === 'landlord' || c.sender_role === 'caretaker';
                  return (
                    <div key={c.id} className={`flex flex-col ${isLandlord ? 'items-end' : 'items-start'}`}>
                      <div className={`max-w-xs px-4 py-3 rounded-2xl text-sm shadow-sm ${
                        isLandlord
                          ? 'bg-emerald-600 text-white rounded-br-sm'
                          : 'bg-white text-slate-800 border border-slate-200 rounded-bl-sm'
                      }`}>
                        <p className={`text-xs font-semibold mb-1 ${isLandlord ? 'text-emerald-100' : 'text-slate-500'}`}>
                          {isLandlord ? 'You' : c.sender_name}
                        </p>
                        <p className="leading-relaxed">{c.message}</p>
                      </div>
                      <span className="text-xs text-slate-400 mt-1 px-1">{fmt(c.created_at)}</span>
                    </div>
                  );
                })
              )}
              <div ref={bottomRef} />
            </div>

            {activeTicket.status !== 'completed' && activeTicket.status !== 'closed' && (
              <div className="p-4 border-t border-slate-200 bg-white shrink-0">
                <div className="flex gap-2">
                  <textarea
                    rows={2}
                    placeholder="Type a reply..."
                    value={reply}
                    onChange={(e) => setReply(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSendReply(); } }}
                    className="flex-1 border border-slate-300 rounded-xl px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-emerald-500 resize-none"
                  />
                  <button
                    onClick={handleSendReply}
                    disabled={isSending || !reply.trim()}
                    className="bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-300 text-white px-4 rounded-xl transition-colors"
                  >
                    <Send size={18} />
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}