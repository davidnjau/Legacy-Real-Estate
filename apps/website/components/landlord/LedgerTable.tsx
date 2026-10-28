// components/landlord/LedgerTable.tsx
'use client';

import { MessageCircle, FileText, Banknote, X } from 'lucide-react';
import { Fragment, useState } from 'react';

interface LedgerPayment {
  id: string;
  date: string;
  amount: number;
  reference: string;
  method: string;
}

export interface LedgerEntry {
  id: string;
  unitCode: string;
  tenantName: string;
  newCharges: number;
  priorArrears: number;
  expected: number;
  paid: number;
  balance: number;
  status: string;
  paymentHistory?: LedgerPayment[];
}

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

export function LedgerTable({ records, onPaymentRecorded }: { records: LedgerEntry[], onPaymentRecorded?: () => void }) {
  const [selectedEntry, setSelectedEntry] = useState<LedgerEntry | null>(null);
  const [paymentAmount, setPaymentAmount] = useState<string>('');
  const [paymentMethod, setPaymentMethod] = useState<string>('cash');
  const [paymentReference, setPaymentReference] = useState<string>('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [expandedEntryId, setExpandedEntryId] = useState<string | null>(null);

  const handleRecordPayment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedEntry) return;

    setIsSubmitting(true);
    setError('');

    try {
      const res = await fetch('/api/ledger/payment', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          invoiceId: selectedEntry.id,
          amount: Number(paymentAmount),
          method: paymentMethod,
          reference: paymentReference,
        }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to record payment');

      setSelectedEntry(null);
      setPaymentAmount('');
      setPaymentReference('');
      if (onPaymentRecorded) onPaymentRecorded();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  };
  const getStatusClasses = (entry: LedgerEntry) => {
    if (entry.balance <= 0 && entry.paid > 0) return 'bg-emerald-100 text-emerald-700';
    if (entry.paid > 0 && entry.balance > 0) return 'bg-amber-100 text-amber-700';
    if (entry.status === 'draft') return 'bg-slate-200 text-slate-700';
    return 'bg-red-100 text-red-700';
  };

  const getStatusLabel = (entry: LedgerEntry) => {
    if (entry.status === 'draft') {
      if (entry.balance <= 0 && entry.paid > 0) return 'DRAFT · PAID';
      if (entry.paid > 0) return 'DRAFT · PARTIAL';
      return 'DRAFT';
    }
    if (entry.balance <= 0 && entry.paid > 0) return 'CLEARED';
    if (entry.paid > 0 && entry.balance > 0) return 'PARTIAL';
    return 'UNPAID';
  };

  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-slate-500 bg-white">
            <th className="p-4 font-medium rounded-tl-xl">Unit & Tenant</th>
            <th className="p-4 font-medium text-right bg-slate-50/50">New Charges</th>
            <th className="p-4 font-medium text-right bg-slate-50/50 text-amber-700/70">+ Arrears</th>
            <th className="p-4 font-medium text-right border-x border-slate-100">= Expected</th>
            <th className="p-4 font-medium text-right text-emerald-700/70">- Paid</th>
            <th className="p-4 font-medium text-right">= Balance</th>
            <th className="p-4 font-medium text-center">Status</th>
            <th className="p-4 font-medium text-right rounded-tr-xl">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 bg-white">
          {records.map((row) => (
            <Fragment key={row.id}>
            <tr className="hover:bg-slate-50 transition-colors group">
              <td className="p-4">
                <div className="font-bold text-slate-900">{row.unitCode}</div>
                <div className="text-slate-500 text-xs mt-0.5 truncate max-w-[150px]">{row.tenantName}</div>
              </td>
              <td className="p-4 text-right text-slate-700 bg-slate-50/30">
                {currency.format(row.newCharges)}
              </td>
              <td className="p-4 text-right font-medium text-amber-700 bg-slate-50/30">
                {row.priorArrears > 0 ? currency.format(row.priorArrears) : '-'}
              </td>
              <td className="p-4 text-right font-bold text-slate-900 border-x border-slate-100 bg-slate-50/50">
                {currency.format(row.expected)}
              </td>
              <td className="p-4 text-right font-bold text-emerald-600">
                {row.paid > 0 ? currency.format(row.paid) : '-'}
              </td>
              <td className="p-4 text-right font-bold text-slate-900">
                {currency.format(row.balance)}
              </td>
              <td className="p-4 text-center">
                <span className={`px-2.5 py-1 text-[10px] font-bold rounded-full ${getStatusClasses(row)}`}>
                  {getStatusLabel(row)}
                </span>
              </td>
              <td className="p-4 text-right">
                <div className="flex justify-end gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                  {row.balance > 0 && (
                    <>
                      <button type="button" onClick={() => { setSelectedEntry(row); setPaymentAmount(row.balance.toString()); }} className="p-1.5 text-emerald-600 hover:bg-emerald-50 rounded-lg border border-emerald-200 transition-colors" title="Record Payment">
                        <Banknote size={16} />
                      </button>
                      <button type="button" onClick={() => window.alert('Sending WhatsApp Reminder... (Demo)')} className="p-1.5 text-green-600 hover:bg-green-50 rounded-lg border border-green-200 transition-colors" title="Send WhatsApp Reminder">
                        <MessageCircle size={16} />
                      </button>
                    </>
                  )}
                  <button type="button" onClick={() => setExpandedEntryId((current) => current === row.id ? null : row.id)} className="p-1.5 text-slate-600 hover:bg-slate-100 rounded-lg border border-slate-200 transition-colors" title="Toggle payment history" aria-expanded={expandedEntryId === row.id}>
                    <FileText size={16} />
                  </button>
                </div>
              </td>
            </tr>
            {expandedEntryId === row.id && <tr><td colSpan={8} className="bg-slate-50 px-5 py-4">
              <h4 className="text-xs uppercase font-bold text-slate-500 mb-2">Payment history</h4>
              {row.paymentHistory?.length ? <div className="divide-y divide-slate-200">{row.paymentHistory.map((payment) => <div key={payment.id} className="py-2 flex justify-between gap-4 text-sm"><span className="text-slate-600">{new Date(payment.date).toLocaleString()} · {payment.method.replace(/_/g, ' ')}{payment.reference ? ` · ${payment.reference}` : ''}</span><strong className="text-emerald-700">{currency.format(payment.amount)}</strong></div>)}</div> : <p className="text-sm text-slate-500">No payment records.</p>}
            </td></tr>}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
      
      {/* Payment Modal */}
      {selectedEntry && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-sm p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-md overflow-hidden">
            <div className="flex items-center justify-between p-5 border-b border-slate-100 bg-slate-50">
              <h3 className="font-bold text-slate-900 flex items-center gap-2">
                <Banknote className="text-emerald-600" size={20} />
                Record Payment
              </h3>
              <button onClick={() => setSelectedEntry(null)} className="text-slate-400 hover:text-slate-600 transition-colors">
                <X size={20} />
              </button>
            </div>
            <form onSubmit={handleRecordPayment} className="p-5 space-y-4">
              {error && (
                <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg border border-red-100">
                  {error}
                </div>
              )}
              
              <div>
                <p className="text-sm text-slate-500 mb-1">Tenant & Unit</p>
                <p className="font-medium text-slate-900">{selectedEntry.tenantName} ({selectedEntry.unitCode})</p>
              </div>

              <div>
                <p className="text-sm text-slate-500 mb-1">Outstanding Balance</p>
                <p className="font-medium text-amber-700">{currency.format(selectedEntry.balance)}</p>
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Payment Amount (KES)</label>
                <input
                  type="number"
                  required
                  min="1"
                  max={selectedEntry.balance}
                  value={paymentAmount}
                  onChange={(e) => setPaymentAmount(e.target.value)}
                  className="w-full border border-slate-300 rounded-lg p-2.5 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1">Method</label>
                  <select
                    value={paymentMethod}
                    onChange={(e) => setPaymentMethod(e.target.value)}
                    className="w-full border border-slate-300 rounded-lg p-2.5 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all bg-white"
                  >
                    <option value="cash">Cash</option>
                    <option value="bank_transfer">Bank Transfer</option>
                    <option value="mpesa_manual">M-PESA (Manual)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1">Reference (Optional)</label>
                  <input
                    type="text"
                    value={paymentReference}
                    onChange={(e) => setPaymentReference(e.target.value)}
                    placeholder="e.g. Receipt #123"
                    className="w-full border border-slate-300 rounded-lg p-2.5 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all"
                  />
                </div>
              </div>

              <div className="pt-4 flex justify-end gap-3 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setSelectedEntry(null)}
                  className="px-4 py-2 font-medium text-slate-600 hover:text-slate-900 transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="bg-emerald-600 hover:bg-emerald-700 text-white px-5 py-2 rounded-lg font-medium transition-colors disabled:opacity-50"
                >
                  {isSubmitting ? 'Saving...' : 'Save Payment'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}