// app/landlord/maintenance/page.tsx
'use client';

import { useEffect, useRef, useState } from 'react';
import { MessageSquare, Clock, CheckCircle2, X, Send, Wrench, AlertCircle, LayoutDashboard, List, Calendar } from 'lucide-react';

type Ticket = {
  id: string;
  ticketId: string;
  category: string;
  description: string;
  priority: string;
  status: string;
  unitCode: string;
  tenantName: string;
  createdAt: string;
};

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
  cost: number;
  resolvedAt: string;
};

const priorityBadge = (p: string) =>
  p === 'high' ? 'bg-red-100 text-red-700' :
  p === 'low' ? 'bg-emerald-100 text-emerald-700' :
  'bg-blue-100 text-blue-700';

const statusBadge = (s: string) =>
  s === 'pending' ? 'bg-amber-100 text-amber-700' :
  s === 'in_progress' ? 'bg-blue-100 text-blue-700' :
  'bg-slate-100 text-slate-600';

const fmt = (d: string) =>
  new Date(d).toLocaleString('en-KE', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

export default function MaintenanceBoard() {
  const [viewMode, setViewMode] = useState<'board' | 'ledger'>('board');

  // Board State
  const [pending, setPending] = useState<Ticket[]>([]);
  const [inProgress, setInProgress] = useState<Ticket[]>([]);
  const [resolved, setResolved] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTicket, setActiveTicket] = useState<Ticket | null>(null);
  const [comments, setComments] = useState<Comment[]>([]);
  const [loadingComments, setLoadingComments] = useState(false);
  const [reply, setReply] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [isUpdatingStatus, setIsUpdatingStatus] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  // Resolution Modal State
  const [resolvingTicketId, setResolvingTicketId] = useState<string | null>(null);
  const [fundiName, setFundiName] = useState('');
  const [repairCost, setRepairCost] = useState('');

  // Ledger State
  const [availableMonths, setAvailableMonths] = useState<string[]>([]);
  const [selectedMonth, setSelectedMonth] = useState<string>('');
  const [ledgerRows, setLedgerRows] = useState<LedgerRecord[]>([]);
  const [ledgerSummary, setLedgerSummary] = useState({ totalSpend: 0, totalRepairs: 0 });
  const [loadingLedger, setLoadingLedger] = useState(false);

  const loadTickets = async () => {
    try {
      const response = await fetch('/api/maintenance/landlord');
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error);
      setPending(payload.pending || []);
      setInProgress(payload.inProgress || []);
      setResolved(payload.resolved || []);
    } catch (error) {
      console.error('Failed to load tickets:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadTickets(); }, []);

  // Fetch ledger months
  useEffect(() => {
    const fetchMonths = async () => {
      try {
        const res = await fetch('/api/maintenance/ledger/months');
        if (res.ok) {
          const data = await res.json();
          setAvailableMonths(data.months || []);
          if (data.months && data.months.length > 0) {
            setSelectedMonth(data.months[0]);
          }
        }
      } catch (err) {}
    };
    fetchMonths();
  }, []);

  // Fetch Ledger data when month changes
  useEffect(() => {
    if (!selectedMonth || viewMode !== 'ledger') return;
    const fetchLedger = async () => {
      setLoadingLedger(true);
      try {
        const res = await fetch(`/api/maintenance/ledger?month=${selectedMonth}`);
        if (res.ok) {
          const data = await res.json();
          setLedgerRows(data.ledger || []);
          setLedgerSummary(data.summary || { totalSpend: 0, totalRepairs: 0 });
        }
      } catch (err) {}
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

  const handleStatusChange = async (ticketId: string, newStatus: string, fundi?: string, cost?: number) => {
    setIsUpdatingStatus(true);
    try {
      const payload: any = { ticketId, status: newStatus };
      if (newStatus === 'resolved') {
        payload.fundiName = fundi;
        payload.cost = cost;
      }

      const response = await fetch('/api/maintenance/landlord', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error('Failed to update status');
      
      await loadTickets();
      if (activeTicket?.id === ticketId) {
        setActiveTicket(prev => prev ? { ...prev, status: newStatus } : null);
      }
    } catch (error) {
      alert('Failed to update ticket status. Please try again.');
    } finally {
      setIsUpdatingStatus(false);
    }
  };

  const handleResolveSubmit = async () => {
    if (!resolvingTicketId) return;
    const numCost = parseFloat(repairCost);
    if (isNaN(numCost) || numCost < 0) {
      alert("Please enter a valid repair cost.");
      return;
    }
    if (!fundiName.trim()) {
      alert("Please enter the fundi's name.");
      return;
    }

    await handleStatusChange(resolvingTicketId, 'resolved', fundiName.trim(), numCost);
    setResolvingTicketId(null);
    setFundiName('');
    setRepairCost('');
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

  const TicketCard = ({ ticket, currentStatus }: { ticket: Ticket; currentStatus: string }) => (
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
      <div className="flex justify-between items-center">
        <span className="text-xs text-slate-400">{ticket.category}</span>
        <span className="text-xs text-slate-400">{new Date(ticket.createdAt).toLocaleDateString('en-KE', { month: 'short', day: 'numeric' })}</span>
      </div>
      <div className="flex gap-2">
        {currentStatus === 'pending' && (
          <button
            onClick={(e) => { e.stopPropagation(); handleStatusChange(ticket.id, 'in_progress'); }}
            className="flex-1 py-1.5 border border-blue-300 text-blue-700 rounded-lg text-xs font-medium hover:bg-blue-50 transition-colors"
          >
            Acknowledge
          </button>
        )}
        {currentStatus === 'in_progress' && (
          <button
            onClick={(e) => { 
              e.stopPropagation(); 
              setResolvingTicketId(ticket.id); 
              setFundiName('');
              setRepairCost('');
            }}
            className="flex-1 py-1.5 border border-emerald-300 text-emerald-700 rounded-lg text-xs font-medium hover:bg-emerald-50 transition-colors"
          >
            Mark Resolved
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
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-3xl font-bold text-slate-900">Maintenance & Repairs</h1>
          <p className="text-slate-500 mt-1">Manage tenant repair requests and track maintenance expenses.</p>
        </div>

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

      {viewMode === 'board' ? (
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
                    {pending.map(t => <TicketCard key={t.id} ticket={t} currentStatus="pending" />)}
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
                    {inProgress.map(t => <TicketCard key={t.id} ticket={t} currentStatus="in_progress" />)}
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
                    {resolved.map(t => <TicketCard key={t.id} ticket={t} currentStatus="resolved" />)}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      ) : (
        /* ─── MAINTENANCE LEDGER VIEW ─── */
        <div className="space-y-6">
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
                          {currency.format(row.cost)}
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

      {/* ─── Resolution Modal ─── */}
      {resolvingTicketId && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-[100] p-4">
          <div className="bg-white rounded-2xl w-full max-w-md shadow-2xl overflow-hidden animate-in fade-in zoom-in-95">
            <div className="p-5 border-b border-slate-100 flex justify-between items-center bg-slate-50">
              <h2 className="font-bold text-slate-900 text-lg">Log Repair Expense</h2>
              <button onClick={() => setResolvingTicketId(null)} className="text-slate-400 hover:text-slate-600">
                <X size={20} />
              </button>
            </div>
            <div className="p-6 space-y-5">
              <p className="text-sm text-slate-600">
                To mark this ticket as resolved, please log the technician and cost details. This will be recorded in the Maintenance Ledger.
              </p>
              <div>
                <label className="block text-sm font-bold text-slate-700 mb-1.5">Fundi / Technician Name</label>
                <input
                  type="text"
                  placeholder="e.g. John Doe Plumbing"
                  value={fundiName}
                  onChange={(e) => setFundiName(e.target.value)}
                  className="w-full border border-slate-300 rounded-lg px-3 py-2.5 outline-none focus:ring-2 focus:ring-emerald-500"
                />
              </div>
              <div>
                <label className="block text-sm font-bold text-slate-700 mb-1.5">Repair Cost (KES)</label>
                <input
                  type="number"
                  placeholder="e.g. 1500"
                  min="0"
                  value={repairCost}
                  onChange={(e) => setRepairCost(e.target.value)}
                  className="w-full border border-slate-300 rounded-lg px-3 py-2.5 outline-none focus:ring-2 focus:ring-emerald-500 font-mono"
                />
              </div>
              <button
                onClick={handleResolveSubmit}
                disabled={isUpdatingStatus || !fundiName || !repairCost}
                className="w-full bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-300 text-white font-bold py-3 rounded-xl transition-colors"
              >
                {isUpdatingStatus ? 'Saving...' : 'Resolve Ticket & Save Record'}
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
                    {activeTicket.status.replace('_', ' ')}
                  </span>
                </div>
                <h2 className="font-bold text-slate-900 text-lg leading-tight">{activeTicket.description}</h2>
                <p className="text-xs text-slate-500 mt-1">{activeTicket.category} • {activeTicket.unitCode} • {activeTicket.tenantName}</p>
              </div>
              <button onClick={() => setActiveTicket(null)} className="text-slate-400 hover:text-slate-600 ml-4 shrink-0">
                <X size={22} />
              </button>
            </div>

            <div className="px-5 py-3 border-b border-slate-100 flex gap-2 shrink-0 bg-white">
              {activeTicket.status === 'pending' && (
                <button
                  onClick={() => handleStatusChange(activeTicket.id, 'in_progress')}
                  disabled={isUpdatingStatus}
                  className="flex-1 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-bold transition-colors disabled:opacity-60"
                >
                  Acknowledge — Start Work
                </button>
              )}
              {activeTicket.status === 'in_progress' && (
                <button
                  onClick={() => {
                    setResolvingTicketId(activeTicket.id);
                    setFundiName('');
                    setRepairCost('');
                    setActiveTicket(null); // close drawer to show modal
                  }}
                  disabled={isUpdatingStatus}
                  className="flex-1 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-sm font-bold transition-colors disabled:opacity-60"
                >
                  ✓ Mark as Resolved
                </button>
              )}
              {activeTicket.status === 'resolved' && (
                <div className="flex-1 py-2 bg-slate-100 text-slate-500 rounded-lg text-sm font-medium text-center">
                  Ticket Resolved
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

            {activeTicket.status !== 'resolved' && (
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