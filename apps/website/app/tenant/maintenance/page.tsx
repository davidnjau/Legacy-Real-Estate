// app/tenant/maintenance/page.tsx
'use client';

import { useEffect, useRef, useState } from 'react';
import { Camera, Clock, CheckCircle2, AlertCircle, MessageSquare, Send, ChevronDown, ChevronUp } from 'lucide-react';

type Ticket = {
  id: string;
  ticketId: string;
  category: string;
  description: string;
  priority: string;
  status: string;
  createdAt: string;
  updatedAt: string;
};

type Comment = {
  id: string;
  sender_role: string;
  sender_name: string;
  message: string;
  created_at: string;
};

const getStatusInfo = (status: string) => {
  if (status === 'pending') return { label: 'Pending', badge: 'bg-amber-100 text-amber-700', icon: <Clock size={13} />, desc: 'Your request has been received. The landlord will acknowledge it shortly.' };
  if (status === 'in_progress') return { label: 'In Progress', badge: 'bg-blue-100 text-blue-700', icon: <AlertCircle size={13} />, desc: 'The landlord is working on this. Help is on the way!' };
  return { label: 'Resolved', badge: 'bg-emerald-100 text-emerald-700', icon: <CheckCircle2 size={13} />, desc: 'This issue has been marked as resolved.' };
};

const fmt = (d: string) =>
  new Date(d).toLocaleString('en-KE', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

export default function TenantMaintenance() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitSuccess, setSubmitSuccess] = useState('');
  const [submitError, setSubmitError] = useState('');
  const [formData, setFormData] = useState({
    category: 'Plumbing / Water',
    description: '',
    priority: 'normal',
  });
  const [expandedTicket, setExpandedTicket] = useState<string | null>(null);
  const [comments, setComments] = useState<Record<string, Comment[]>>({});
  const [loadingComments, setLoadingComments] = useState<string | null>(null);
  const [replies, setReplies] = useState<Record<string, string>>({});
  const [sendingReply, setSendingReply] = useState<string | null>(null);
  const bottomRefs = useRef<Record<string, HTMLDivElement | null>>({});

  const loadTickets = async () => {
    try {
      const response = await fetch('/api/maintenance/tenant');
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error);
      setTickets(payload.tickets || []);
    } catch (error) {
      console.error('Failed to load tickets:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadTickets(); }, []);

  const loadComments = async (ticketId: string) => {
    setLoadingComments(ticketId);
    try {
      const res = await fetch(`/api/maintenance/comment?ticketId=${ticketId}`);
      const data = await res.json();
      setComments(prev => ({ ...prev, [ticketId]: data.comments || [] }));
    } finally {
      setLoadingComments(null);
    }
  };

  const toggleTicket = async (ticketId: string) => {
    if (expandedTicket === ticketId) {
      setExpandedTicket(null);
    } else {
      setExpandedTicket(ticketId);
      if (!comments[ticketId]) {
        await loadComments(ticketId);
      }
    }
  };

  useEffect(() => {
    if (expandedTicket) {
      setTimeout(() => {
        bottomRefs.current[expandedTicket]?.scrollIntoView({ behavior: 'smooth' });
      }, 100);
    }
  }, [comments, expandedTicket]);

  const handleSendReply = async (ticketId: string) => {
    const msg = replies[ticketId]?.trim();
    if (!msg) return;
    setSendingReply(ticketId);
    try {
      const res = await fetch('/api/maintenance/comment', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticketId, message: msg }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      setReplies(prev => ({ ...prev, [ticketId]: '' }));
      await loadComments(ticketId);
    } catch (err: any) {
      alert(err.message || 'Failed to send message.');
    } finally {
      setSendingReply(null);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.description.trim()) return;
    setIsSubmitting(true);
    setSubmitError('');
    setSubmitSuccess('');

    try {
      const response = await fetch('/api/maintenance/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Failed to submit request');

      setSubmitSuccess('Your maintenance request was submitted successfully. The landlord will respond shortly.');
      setFormData({ category: 'Plumbing / Water', description: '', priority: 'normal' });
      await loadTickets();
    } catch (error: any) {
      setSubmitError(error.message || 'Failed to submit. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold text-slate-900">Maintenance Requests</h1>
        <p className="text-slate-500 mt-1">Report issues in your unit and track the response from your landlord.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 items-start">
        {/* ── Submit Form ── */}
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
          <h2 className="text-lg font-bold text-slate-900 mb-4">Submit a New Request</h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Issue Category</label>
              <select
                value={formData.category}
                onChange={(e) => setFormData({ ...formData, category: e.target.value })}
                className="w-full border border-slate-300 rounded-lg px-4 py-2 outline-none focus:ring-2 focus:ring-emerald-500 bg-white text-sm"
              >
                <option>Plumbing / Water</option>
                <option>Electrical</option>
                <option>Structural (Doors, Windows, Walls)</option>
                <option>Pest Control</option>
                <option>Security</option>
                <option>Other</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Priority</label>
              <select
                value={formData.priority}
                onChange={(e) => setFormData({ ...formData, priority: e.target.value })}
                className="w-full border border-slate-300 rounded-lg px-4 py-2 outline-none focus:ring-2 focus:ring-emerald-500 bg-white text-sm"
              >
                <option value="low">Low — Not urgent</option>
                <option value="normal">Normal — Needs attention soon</option>
                <option value="high">High — Urgent</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Describe the Issue</label>
              <textarea
                rows={4}
                placeholder="Please describe the problem in detail..."
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                className="w-full border border-slate-300 rounded-lg px-4 py-2 outline-none focus:ring-2 focus:ring-emerald-500 resize-none text-sm"
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Photo Evidence (Optional)</label>
              <div className="border-2 border-dashed border-slate-300 rounded-lg p-6 flex flex-col items-center text-slate-400 hover:bg-slate-50 cursor-pointer transition-colors">
                <Camera size={24} className="mb-2" />
                <span className="text-sm">Click to upload a photo</span>
              </div>
            </div>
            {submitError && <p className="text-sm text-red-600">{submitError}</p>}
            {submitSuccess && <p className="text-sm text-emerald-600">{submitSuccess}</p>}
            <button
              type="submit"
              disabled={isSubmitting}
              className="w-full bg-slate-900 hover:bg-slate-800 disabled:bg-slate-600 text-white font-bold py-3 rounded-xl transition-colors"
            >
              {isSubmitting ? 'Submitting...' : 'Submit Request'}
            </button>
          </form>
        </div>

        {/* ── Ticket History ── */}
        <div className="space-y-4">
          <h2 className="text-lg font-bold text-slate-900">Your Requests</h2>
          {loading ? (
            <div className="text-slate-500 text-sm">Loading tickets...</div>
          ) : tickets.length === 0 ? (
            <div className="text-center p-10 bg-slate-50 border border-slate-200 rounded-xl text-slate-400 text-sm">
              No maintenance requests yet.
            </div>
          ) : (
            tickets.map((ticket) => {
              const statusInfo = getStatusInfo(ticket.status);
              const isExpanded = expandedTicket === ticket.id;
              const ticketComments = comments[ticket.id] || [];
              const newReplies = ticketComments.filter(c => c.sender_role === 'landlord' || c.sender_role === 'caretaker');

              return (
                <div key={ticket.id} className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
                  {/* Ticket header */}
                  <button
                    onClick={() => toggleTicket(ticket.id)}
                    className="w-full p-5 text-left hover:bg-slate-50 transition-colors"
                  >
                    <div className="flex justify-between items-start">
                      <div className="flex-1 pr-4">
                        <p className="font-semibold text-slate-900 text-sm line-clamp-2">{ticket.description}</p>
                        <p className="text-xs text-slate-400 mt-1">{ticket.category} · {new Date(ticket.createdAt).toLocaleDateString('en-KE', { month: 'short', day: 'numeric', year: 'numeric' })}</p>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className={`px-2 py-0.5 text-xs font-bold rounded-full flex items-center gap-1 ${statusInfo.badge}`}>
                          {statusInfo.icon} {statusInfo.label}
                        </span>
                        {newReplies.length > 0 && (
                          <span className="w-5 h-5 bg-emerald-500 text-white text-xs font-bold rounded-full flex items-center justify-center">
                            {newReplies.length}
                          </span>
                        )}
                        {isExpanded ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
                      </div>
                    </div>
                    {/* Status description */}
                    <p className="text-xs text-slate-500 mt-2 italic">{statusInfo.desc}</p>
                  </button>

                  {/* Expanded: conversation thread */}
                  {isExpanded && (
                    <div className="border-t border-slate-100">
                      <div className="bg-slate-50 px-5 py-3 max-h-64 overflow-y-auto space-y-3">
                        {loadingComments === ticket.id ? (
                          <p className="text-xs text-slate-400 py-4 text-center">Loading messages...</p>
                        ) : ticketComments.length === 0 ? (
                          <div className="text-center py-6 text-slate-400">
                            <MessageSquare size={24} className="mx-auto mb-1 opacity-30" />
                            <p className="text-xs">No replies yet. Your landlord will respond here.</p>
                          </div>
                        ) : (
                          ticketComments.map((c) => {
                            const isLandlord = c.sender_role === 'landlord' || c.sender_role === 'caretaker';
                            return (
                              <div key={c.id} className={`flex flex-col ${isLandlord ? 'items-start' : 'items-end'}`}>
                                <div className={`max-w-xs px-3 py-2 rounded-xl text-sm ${
                                  isLandlord
                                    ? 'bg-white border border-slate-200 text-slate-800'
                                    : 'bg-slate-800 text-white'
                                }`}>
                                  <p className={`text-xs font-semibold mb-1 ${isLandlord ? 'text-emerald-600' : 'text-slate-300'}`}>
                                    {isLandlord ? `${c.sender_name} (Landlord)` : 'You'}
                                  </p>
                                  <p>{c.message}</p>
                                </div>
                                <span className="text-xs text-slate-400 mt-0.5 px-1">{fmt(c.created_at)}</span>
                              </div>
                            );
                          })
                        )}
                        <div ref={(el) => { bottomRefs.current[ticket.id] = el; }} />
                      </div>

                      {/* Reply box (only if not resolved) */}
                      {ticket.status !== 'resolved' && (
                        <div className="p-3 border-t border-slate-100 flex gap-2 bg-white">
                          <input
                            type="text"
                            placeholder="Add a message..."
                            value={replies[ticket.id] || ''}
                            onChange={(e) => setReplies(prev => ({ ...prev, [ticket.id]: e.target.value }))}
                            onKeyDown={(e) => { if (e.key === 'Enter') handleSendReply(ticket.id); }}
                            className="flex-1 border border-slate-300 rounded-lg px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-emerald-500"
                          />
                          <button
                            onClick={() => handleSendReply(ticket.id)}
                            disabled={sendingReply === ticket.id || !replies[ticket.id]?.trim()}
                            className="bg-slate-900 hover:bg-slate-800 disabled:bg-slate-300 text-white px-3 rounded-lg transition-colors"
                          >
                            <Send size={15} />
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}