'use client';

import { useEffect, useState } from 'react';
import { Download, Calendar, ChevronDown, CheckCircle, AlertCircle, Clock, Info } from 'lucide-react';
import { LedgerTable } from '../../../components/landlord/LedgerTable';

type LedgerEntry = {
  id: string;
  unitId?: string;
  unitCode: string;
  tenantName: string;
  newCharges: number;
  priorArrears: number;
  expected: number;
  paid: number;
  balance: number;
  status: string;
};

type LedgerSummary = {
  totalNewCharges: number;
  totalArrearsBroughtIn: number;
  totalExpected: number;
  totalPaid: number;
  totalBalance: number;
  moveInRequired?: number;
  moveInPaid?: number;
  moveInBalance?: number;
};

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

export default function FinancialLedger() {
  const [availableMonths, setAvailableMonths] = useState<string[]>([]);
  const [selectedMonth, setSelectedMonth] = useState<string>('');
  
  const [rows, setRows] = useState<LedgerEntry[]>([]);
  const [summary, setSummary] = useState<LedgerSummary | null>(null);
  const [moveInRows, setMoveInRows] = useState<LedgerEntry[]>([]);
  const [ledgerView, setLedgerView] = useState<'monthly' | 'move-in'>('monthly');
  const [loading, setLoading] = useState(true);
  
  type FilterCategory = 'All' | 'Fully Cleared' | 'Partially Cleared' | 'Unpaid' | 'Has Arrears';
  const [activeFilter, setActiveFilter] = useState<FilterCategory>('All');

  // Load available months on mount
  useEffect(() => {
    const loadMonths = async () => {
      try {
        const res = await fetch('/api/ledger/months');
        if (res.ok) {
          const data = await res.json();
          setAvailableMonths(data.months || []);
          if (data.months && data.months.length > 0) {
            setSelectedMonth(data.months[0]); // Select the newest month by default
          } else {
            setSelectedMonth(new Date().toISOString().slice(0, 7)); // Fallback
          }
        }
      } catch (err) {
        console.error('Failed to load months', err);
      }
    };
    loadMonths();
  }, []);

  const loadLedger = async (month: string, showLoading = true) => {
    if (showLoading) setLoading(true);
    try {
      const response = await fetch(`/api/ledger?month=${month}`);
      const payload = await response.json();

      if (!response.ok) {
        throw new Error(payload.error || 'Unable to load ledger data');
      }

      setRows(payload.ledger || []);
      setMoveInRows(payload.moveInLedger || []);
      setSummary(payload.summary || null);
    } catch (error) {
      console.error('Ledger load failed:', error);
    } finally {
      if (showLoading) setLoading(false);
    }
  };

  useEffect(() => {
    if (!selectedMonth) return;
    loadLedger(selectedMonth);
    const interval = setInterval(() => loadLedger(selectedMonth, false), 15000);
    return () => clearInterval(interval);
  }, [selectedMonth]);

  // Determine status styles for badges
  const getStatusClasses = (entry: LedgerEntry) => {
    if (entry.balance <= 0 && entry.paid > 0) return 'bg-emerald-100 text-emerald-700'; // Fully Cleared
    if (entry.paid > 0 && entry.balance > 0) return 'bg-amber-100 text-amber-700'; // Partial
    if (entry.status === 'draft') return 'bg-slate-200 text-slate-700'; // Draft
    return 'bg-red-100 text-red-700'; // Unpaid
  };

  const getStatusLabel = (entry: LedgerEntry) => {
    if (entry.status === 'draft') return 'DRAFT';
    if (entry.balance <= 0 && entry.paid > 0) return 'CLEARED';
    if (entry.paid > 0 && entry.balance > 0) return 'PARTIAL';
    return 'UNPAID';
  };

  // Filter the rows
  const filteredRows = rows.filter((row) => {
    if (activeFilter === 'All') return true;
    if (activeFilter === 'Fully Cleared') return row.balance <= 0;
    if (activeFilter === 'Partially Cleared') return row.paid > 0 && row.balance > 0;
    if (activeFilter === 'Unpaid') return row.paid === 0 && row.balance > 0;
    if (activeFilter === 'Has Arrears') return row.priorArrears > 0;
    return true;
  });

  const displayedRows = ledgerView === 'monthly' ? filteredRows : moveInRows;

  const monthLabel = selectedMonth 
    ? new Date(`${selectedMonth}-01`).toLocaleString('default', { month: 'long', year: 'numeric' })
    : '...';

  return (
    <div className="space-y-8">
      {/* ── Header & Month Selector ── */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-3xl font-bold text-slate-900">Financial Ledger</h1>
          <p className="text-slate-500 mt-1">Track comprehensive historical billing and payment records.</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Month Dropdown */}
          <div className="relative">
            <select
              value={selectedMonth}
              onChange={(e) => setSelectedMonth(e.target.value)}
              className="appearance-none bg-white border border-slate-300 rounded-lg pl-10 pr-10 py-2 text-slate-700 font-medium focus:outline-none focus:ring-2 focus:ring-slate-900 cursor-pointer"
            >
              {availableMonths.map((m) => (
                <option key={m} value={m}>
                  {new Date(`${m}-01`).toLocaleString('default', { month: 'long', year: 'numeric' })}
                </option>
              ))}
            </select>
            <Calendar size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
            <ChevronDown size={16} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
          </div>

          <button type="button" onClick={() => window.alert('Exporting to Excel... (Demo)')} className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 rounded-lg font-medium transition-colors">
            <Download size={18} /> Export
          </button>
        </div>
      </div>

      {/* ── Summary Stats ── */}
      {summary && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* Total Billed (New) */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
            <div className="text-sm font-medium text-slate-500 mb-1 flex items-center justify-between">
              New Charges <span title="Rent + Water + Garbage strictly for this month"><Info size={14} className="text-slate-400" /></span>
            </div>
            <div className="text-2xl font-bold text-slate-900">{currency.format(summary.totalNewCharges)}</div>
          </div>
          
          {/* Arrears Brought In */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
            <div className="text-sm font-medium text-slate-500 mb-1 flex items-center justify-between">
              Prior Arrears <span title="Unpaid balances carried over from previous months"><Clock size={14} className="text-amber-500" /></span>
            </div>
            <div className="text-2xl font-bold text-amber-700">{currency.format(summary.totalArrearsBroughtIn)}</div>
          </div>

          {/* Total Paid */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
            <div className="text-sm font-medium text-slate-500 mb-1 flex items-center justify-between">
              Amount Collected <CheckCircle size={14} className="text-emerald-500" />
            </div>
            <div className="text-2xl font-bold text-emerald-600">{currency.format(summary.totalPaid)}</div>
            <div className="text-xs text-slate-400 mt-1">
              {summary.totalExpected > 0 ? Math.round((summary.totalPaid / summary.totalExpected) * 100) : 0}% of expected
            </div>
          </div>

          {/* Total Balance */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
            <div className="text-sm font-medium text-slate-500 mb-1 flex items-center justify-between">
              Total Outstanding <AlertCircle size={14} className="text-red-400" />
            </div>
            <div className="text-2xl font-bold text-slate-900">{currency.format(summary.totalBalance)}</div>
            <div className="text-xs text-slate-400 mt-1">Expected: {currency.format(summary.totalExpected)}</div>
          </div>
        </div>
      )}

      <div className="flex gap-2 bg-white border border-slate-200 rounded-xl p-2 w-fit">
        <button onClick={() => setLedgerView('monthly')} className={`px-4 py-2 rounded-lg text-sm font-bold ${ledgerView === 'monthly' ? 'bg-slate-900 text-white' : 'text-slate-600'}`}>Monthly Bills</button>
        <button onClick={() => setLedgerView('move-in')} className={`px-4 py-2 rounded-lg text-sm font-bold ${ledgerView === 'move-in' ? 'bg-emerald-600 text-white' : 'text-slate-600'}`}>Move-In Settlements</button>
      </div>

      {ledgerView === 'move-in' && summary && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-white rounded-xl border p-5"><p className="text-sm text-slate-500">Move-In Required</p><strong className="text-2xl">{currency.format(summary.moveInRequired || 0)}</strong></div>
          <div className="bg-white rounded-xl border p-5"><p className="text-sm text-slate-500">Move-In Collected</p><strong className="text-2xl text-emerald-600">{currency.format(summary.moveInPaid || 0)}</strong></div>
          <div className="bg-white rounded-xl border p-5"><p className="text-sm text-slate-500">Move-In Outstanding</p><strong className="text-2xl text-red-600">{currency.format(summary.moveInBalance || 0)}</strong></div>
        </div>
      )}

      {/* ── Table & Filters ── */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden flex flex-col">
        
        {/* Filters */}
        <div className="border-b border-slate-200 bg-slate-50 p-2 flex gap-1 overflow-x-auto">
          {(['All', 'Fully Cleared', 'Partially Cleared', 'Unpaid', 'Has Arrears'] as FilterCategory[]).map(filter => (
            <button
              key={filter}
              onClick={() => setActiveFilter(filter)}
              className={`px-4 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors ${
                activeFilter === filter 
                  ? 'bg-slate-900 text-white shadow-sm' 
                  : 'text-slate-600 hover:bg-slate-200/50'
              }`}
            >
              {filter}
            </button>
          ))}
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          {loading ? (
            <div className="p-12 text-center text-slate-500 animate-pulse">Loading ledger records for {monthLabel}...</div>
          ) : displayedRows.length === 0 ? (
            <div className="p-12 text-center text-slate-500">
              No ledger records found for <span className="font-semibold text-slate-700">{activeFilter}</span> units in {monthLabel}.
            </div>
          ) : (
            <LedgerTable records={displayedRows} onPaymentRecorded={() => loadLedger(selectedMonth)} />
          )}
        </div>
      </div>
    </div>
  );
}
