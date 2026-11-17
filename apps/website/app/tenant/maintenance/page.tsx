// app/tenant/maintenance/page.tsx
'use client';

import { useEffect, useRef, useState } from 'react';
import { Camera, Clock, CheckCircle2, AlertCircle, MessageSquare, Send, ChevronDown, ChevronUp } from 'lucide-react';

type Ticket = {
  id: number;
  category: string;
  description: string;
  priority: string;
  status: string;
  created_at: string;
  updated_at: string;
  evidence: Evidence[];
};

type Evidence = {
  name: string;
  content_type: string;
  size_bytes: number;
  download_path: string;
};

type Comment = {
  id: number;
  sender_role: string;
  sender_name: string;
  message: string;
  created_at: string;
};

const getStatusInfo = (status: string) => {
  if (status === 'in_progress') return { label: 'In Progress', badge: 'bg-blue-100 text-blue-700', icon: <AlertCircle size={13} />, desc: 'The repair is in progress.' };
  if (status === 'completed' || status === 'closed') return { label: status === 'closed' ? 'Closed' : 'Completed', badge: 'bg-emerald-100 text-emerald-700', icon: <CheckCircle2 size={13} />, desc: 'This issue has been marked as completed.' };
  if (status === 'assigned') return { label: 'Assigned', badge: 'bg-blue-100 text-blue-700', icon: <Clock size={13} />, desc: 'A caretaker has been assigned to your request.' };
  if (status === 'quotation' || status === 'awaiting_landlord_approval') return { label: status === 'quotation' ? 'Quotation' : 'Awaiting Approval', badge: 'bg-violet-100 text-violet-700', icon: <Clock size={13} />, desc: 'Your request is being reviewed.' };
  return { label: 'Open', badge: 'bg-amber-100 text-amber-700', icon: <Clock size={13} />, desc: 'Your request has been received and is awaiting review.' };
};

const fmt = (d: string) =>
  new Date(d).toLocaleString('en-KE', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

export default function TenantMaintenance() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitSuccess, setSubmitSuccess] = useState('');
  const [submitError, setSubmitError] = useState('');
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [commentError, setCommentError] = useState('');
  const [formData, setFormData] = useState({
    category: 'plumbing',
    description: '',
    priority: 'normal',
  });
  const [expandedTicket, setExpandedTicket] = useState<number | null>(null);
  const [comments, setComments] = useState<Record<string, Comment[]>>({});
  const [loadingComments, setLoadingComments] = useState<number | null>(null);
  const [replies, setReplies] = useState<Record<string, string>>({});
  const [sendingReply, setSendingReply] = useState<number | null>(null);
  const bottomRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const evidenceInputRef = useRef<HTMLInputElement | null>(null);

  const loadTickets = async () => {
    try {
      const response = await fetch('/api/maintenance/tenant');
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Unable to load maintenance requests.');
      setTickets(payload.tickets || []);
      setLoadError('');
    } catch (error) {
      console.error('Failed to load tickets:', error);
      setLoadError(error instanceof Error ? error.message : 'Unable to load maintenance requests.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadTickets(); }, []);

  const loadComments = async (ticketId: number) => {
    setLoadingComments(ticketId);
    setCommentError('');
    try {
      const res = await fetch(`/api/maintenance/tenant/comment?ticketId=${ticketId}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Unable to load request messages.');
      setComments(prev => ({ ...prev, [ticketId]: data.comments || [] }));
    } catch (error) {
      setCommentError(error instanceof Error ? error.message : 'Unable to load request messages.');
    } finally {
      setLoadingComments(null);
    }
  };

  const toggleTicket = async (ticketId: number) => {
    setCommentError('');
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

  const handleSendReply = async (ticketId: number) => {
    const msg = replies[ticketId]?.trim();
    if (!msg) return;
    setSendingReply(ticketId);
    try {
      const res = await fetch('/api/maintenance/tenant/comment', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticketId, message: msg }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      setReplies(prev => ({ ...prev, [ticketId]: '' }));
      await loadComments(ticketId);
    } catch (error) {
      setCommentError(error instanceof Error ? error.message : 'Failed to send message.');
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
      const submission = new FormData();
      submission.set('category', formData.category);
      submission.set('description', formData.description);
      submission.set('priority', formData.priority);
      selectedFiles.forEach((file) => submission.append('evidence', file));
      const response = await fetch('/api/maintenance/create', {
        method: 'POST',
        body: submission,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Failed to submit request');

      setSubmitSuccess('Your maintenance request was submitted successfully. The landlord will respond shortly.');
      setFormData({ category: 'plumbing', description: '', priority: 'normal' });
      setSelectedFiles([]);
      if (evidenceInputRef.current) evidenceInputRef.current.value = '';
      await loadTickets();
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : 'Failed to submit. Please try again.');
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
                <option value="plumbing">Plumbing / Water</option>
                <option value="electrical">Electrical</option>
                <option value="structural">Structural</option>
                <option value="security">Security</option>
                <option value="other">Other</option>
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
                <option value="urgent">Urgent — Immediate attention</option>
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
              <label className="border-2 border-dashed border-slate-300 rounded-lg p-6 flex flex-col items-center text-slate-500 hover:bg-slate-50 cursor-pointer transition-colors">
                <Camera size={24} className="mb-2" />
                <span className="text-sm">Choose JPEG, PNG, or WebP images (up to 5 files, 5 MB each)</span>
                <input
                  ref={evidenceInputRef}
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  multiple
                  className="sr-only"
                  onChange={(event) => setSelectedFiles(Array.from(event.target.files || []))}
                />
              </label>
              {selectedFiles.length > 0 && (
                <ul className="mt-2 space-y-1 text-xs text-slate-500">
                  {selectedFiles.map((file, index) => (
                    <li key={`${file.name}-${index}`}>{file.name} · {(file.size / (1024 * 1024)).toFixed(1)} MB</li>
                  ))}
                </ul>
              )}
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
          ) : loadError ? (
            <div className="text-sm text-red-600">{loadError}</div>
          ) : tickets.length === 0 ? (
            <div className="text-center p-10 bg-slate-50 border border-slate-200 rounded-xl text-slate-400 text-sm">
              {loadError || 'No maintenance requests yet.'}
            </div>
          ) : (
            tickets.map((ticket) => {
              const statusInfo = getStatusInfo(ticket.status);
              const isExpanded = expandedTicket === ticket.id;
              const ticketComments = comments[ticket.id] || [];
              const newReplies = ticketComments.filter(c => c.sender_role !== 'tenant');

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
                        <p className="text-xs text-slate-400 mt-1">{ticket.category.replaceAll('_', ' ')} · {new Date(ticket.created_at).toLocaleDateString('en-KE', { month: 'short', day: 'numeric', year: 'numeric' })}</p>
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
                        {ticket.evidence.length > 0 && (
                          <div className="flex flex-wrap gap-2">
                            {ticket.evidence.map((item, index) => (
                              <a
                                key={`${ticket.id}-${index}`}
                                href={`/api/maintenance/tenant/${ticket.id}/evidence/${index}`}
                                target="_blank"
                                rel="noreferrer"
                                className="text-xs text-emerald-700 underline"
                              >
                                {item.name}
                              </a>
                            ))}
                          </div>
                        )}
                        {loadingComments === ticket.id ? (
                          <p className="text-xs text-slate-400 py-4 text-center">Loading messages...</p>
                        ) : ticketComments.length === 0 ? (
                          <div className="text-center py-6 text-slate-400">
                            <MessageSquare size={24} className="mx-auto mb-1 opacity-30" />
                            <p className="text-xs">No replies yet. Your landlord will respond here.</p>
                          </div>
                        ) : (
                          ticketComments.map((c) => {
                            const isTenant = c.sender_role === 'tenant';
                            return (
                              <div key={c.id} className={`flex flex-col ${isTenant ? 'items-end' : 'items-start'}`}>
                                <div className={`max-w-xs px-3 py-2 rounded-xl text-sm ${
                                  isTenant
                                    ? 'bg-slate-800 text-white'
                                    : 'bg-white border border-slate-200 text-slate-800'
                                }`}>
                                  <p className={`text-xs font-semibold mb-1 ${isTenant ? 'text-slate-300' : 'text-emerald-600'}`}>
                                    {isTenant ? 'You' : `${c.sender_name} (Property team)`}
                                  </p>
                                  <p>{c.message}</p>
                                </div>
                                <span className="text-xs text-slate-400 mt-0.5 px-1">{fmt(c.created_at)}</span>
                              </div>
                            );
                          })
                        )}
                        {commentError && <p className="text-xs text-red-600">{commentError}</p>}
                        <div ref={(el) => { bottomRefs.current[ticket.id] = el; }} />
                      </div>

                      {ticket.status !== 'completed' && ticket.status !== 'closed' && (
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