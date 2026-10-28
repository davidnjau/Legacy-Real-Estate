'use client';

import { useEffect, useState } from 'react';
import { Droplets, CheckCircle, Send, AlertTriangle } from 'lucide-react';
import { createClient } from '../../../lib/supabase/client';
import { Pagination } from '../../../components/ui/Pagination';

type ReadingRow = {
  unitId: string;
  unitCode: string;
  baselineReading: number | null;
  previousReading: number | null;
  latestReading: number | null;
  unitsUsed: number | null;
  hasBaseline: boolean;
  status: string;
  // local editable value for new end-of-month reading
  newReading: string;
};

export default function WaterReadingsPage() {
  const [rows, setRows] = useState<ReadingRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [userId, setUserId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);
  const [billingMonth, setBillingMonth] = useState('');

  // Pagination states
  const [readingsPage, setReadingsPage] = useState(1);
  const ITEMS_PER_PAGE = 10;

  const showToast = (type: 'success' | 'error', msg: string) => {
    setToast({ type, msg });
    setTimeout(() => setToast(null), 4000);
  };

  // Get current user id
  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getUser().then(({ data }) => {
      setUserId(data.user?.id ?? null);
    });
  }, []);

  const fetchReadings = async () => {
    setLoading(true);
    setLoadError('');
    try {
      const res = await fetch('/api/caretaker/water-readings');
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Unable to load occupied units.');
      setRows(
        (data.readings || []).map((r: any) => ({
          ...r,
          // Only pre-fill the input if there's already a monthly reading saved
          newReading: r.latestReading !== null ? String(r.latestReading) : '',
        }))
      );
      setBillingMonth(data.billingMonth || '');
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Unable to load occupied units.');
      console.error('Failed to load readings', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchReadings();
  }, []);

  const updateRow = (unitId: string, value: string) => {
    setRows(prev => prev.map(r => r.unitId === unitId ? { ...r, newReading: value } : r));
  };

  const handleSaveAndPublish = async () => {
    if (!userId) return;
    setSaving(true);
    try {
      // Only send readings for units that HAVE a baseline and have a value entered
      const readings = rows
        .filter(r => r.hasBaseline && r.newReading.trim() !== '')
        .map(r => ({ unitId: r.unitId, reading: parseFloat(r.newReading) }));

      if (readings.length === 0) {
        showToast('error', 'Please enter at least one reading before saving.');
        setSaving(false);
        return;
      }

      const res = await fetch('/api/caretaker/water-readings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caretakerId: userId, readings }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      const publishRes = await fetch('/api/caretaker/water-readings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caretakerId: userId }),
      });
      const publishData = await publishRes.json();
      if (!publishRes.ok) throw new Error(publishData.error || 'Readings saved but could not be published.');
      showToast('success', 'Water readings saved and published for billing.');
      await fetchReadings();
    } catch (err: any) {
      showToast('error', err.message || 'Failed to save readings.');
    } finally {
      setSaving(false);
    }
  };

  const unitsWithoutBaseline = rows.filter(r => !r.hasBaseline);

  return (
    <div className="space-y-8">
      {/* Toast */}
      {toast && (
        <div className={`fixed top-6 right-6 z-50 flex items-center gap-3 px-5 py-3 rounded-xl shadow-lg text-white text-sm font-medium transition-all ${
          toast.type === 'success' ? 'bg-emerald-600' : 'bg-red-500'
        }`}>
          {toast.type === 'success' ? <CheckCircle size={18} /> : <AlertTriangle size={18} />}
          {toast.msg}
        </div>
      )}

      <div>
        <h1 className="text-3xl font-bold text-slate-900">Water Meter Readings</h1>
        <p className="text-slate-500 mt-1">Record meter readings for the active {billingMonth || 'current'} billing cycle. Published readings become the previous reading for the next cycle.</p>
      </div>


      {/* Warning for units missing baseline */}
      {unitsWithoutBaseline.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 flex items-start gap-3">
          <AlertTriangle size={20} className="text-amber-600 shrink-0 mt-0.5" />
          <div>
            <p className="font-bold text-amber-900 text-sm">
              {unitsWithoutBaseline.length} unit(s) missing baseline readings
            </p>
            <p className="text-sm text-amber-800 mt-1">
              Units without a baseline reading cannot have monthly readings recorded.
              Go to the <strong>Dashboard</strong> and click <strong>"Add reading"</strong> to set a baseline for:
              {' '}<strong>{unitsWithoutBaseline.map(r => r.unitCode).join(', ')}</strong>
            </p>
          </div>
        </div>
      )}

      {/* Instructions */}
      <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 text-sm text-blue-800">
        <strong>How it works:</strong> This table is for <strong>end-of-month readings only</strong>.
        Each unit must first have a baseline meter reading (set during migration or by the caretaker via the Dashboard).
        Enter the current meter reading for each unit — the system automatically calculates water usage by subtracting the previous reading.
        Click <strong>Save &amp; Publish Readings</strong> to make them available for the landlord's billing wizard.
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        {loading ? (
          <div className="p-12 text-center text-slate-500 animate-pulse">Loading units...</div>
        ) : loadError ? (
          <div className="p-12 text-center text-red-600">{loadError}</div>
        ) : rows.length === 0 ? (
          <div className="p-12 text-center text-slate-500">No occupied units are available for water readings.</div>
        ) : (
          <table className="w-full text-sm text-left border-collapse">
            <thead>
              <tr className="bg-slate-900 text-white">
                <th className="p-4 font-medium">Unit</th>
                <th className="p-4 font-medium text-right">Baseline</th>
                <th className="p-4 font-medium text-right">Previous Reading</th>
                <th className="p-4 font-medium text-right">New Reading</th>
                <th className="p-4 font-medium text-right">Units Used</th>
                <th className="p-4 font-medium text-center">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.slice((readingsPage - 1) * ITEMS_PER_PAGE, readingsPage * ITEMS_PER_PAGE).map((row) => {
                const newVal = parseFloat(row.newReading);
                const refReading = row.previousReading; // baseline or last monthly
                const delta = !isNaN(newVal) && refReading !== null
                  ? Math.max(0, newVal - refReading)
                  : null;
                const isPublished = row.status === 'published' && row.latestReading !== null;
                const noBaseline = !row.hasBaseline;

                return (
                  <tr key={row.unitId} className={`hover:bg-slate-50 transition-colors ${noBaseline ? 'opacity-60' : ''}`}>
                    <td className="p-4 font-bold text-slate-900">{row.unitCode}</td>
                    {/* Baseline column */}
                    <td className="p-4 text-right">
                      {row.baselineReading !== null ? (
                        <span className="font-mono text-slate-700">{row.baselineReading.toLocaleString()}</span>
                      ) : (
                        <span className="text-amber-600 font-bold text-xs bg-amber-50 px-2 py-1 rounded-md border border-amber-200">
                          Not set
                        </span>
                      )}
                    </td>
                    {/* Previous reading column */}
                    <td className="p-4 text-right text-slate-500 font-mono">
                      {row.previousReading !== null ? row.previousReading.toLocaleString() : '—'}
                    </td>
                    {/* New reading input */}
                    <td className="p-4 text-right">
                      {noBaseline ? (
                        <span className="text-xs text-amber-700 bg-amber-50 px-2 py-1.5 rounded-md border border-amber-200 font-bold">
                          Add baseline first
                        </span>
                      ) : (
                        <input
                          type="number"
                          min={row.previousReading ?? 0}
                          value={row.newReading}
                          onChange={(e) => updateRow(row.unitId, e.target.value)}
                          disabled={isPublished}
                          placeholder="Enter reading"
                          className="w-36 text-right border border-slate-300 rounded-lg px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-emerald-500 font-mono disabled:bg-slate-100 disabled:cursor-not-allowed"
                        />
                      )}
                    </td>
                    {/* Units used */}
                    <td className="p-4 text-right font-bold text-slate-900">
                      {noBaseline ? '—' : delta !== null ? delta.toLocaleString() : (row.unitsUsed !== null ? row.unitsUsed.toLocaleString() : '—')}
                    </td>
                    {/* Status */}
                    <td className="p-4 text-center">
                      <span className={`px-2.5 py-1 text-xs font-bold rounded-full ${
                        isPublished
                          ? 'bg-emerald-100 text-emerald-700'
                          : row.status === 'submitted'
                          ? 'bg-blue-100 text-blue-700'
                          : noBaseline
                          ? 'bg-amber-100 text-amber-700'
                          : 'bg-slate-100 text-slate-500'
                      }`}>
                        {isPublished ? 'PUBLISHED' : row.status === 'submitted' ? 'SAVED' : noBaseline ? 'NO BASELINE' : 'PENDING'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {!loading && !loadError && rows.length > 0 && (
          <Pagination
            currentPage={readingsPage}
            totalPages={Math.ceil(rows.length / ITEMS_PER_PAGE)}
            onPageChange={setReadingsPage}
          />
        )}
      </div>
      {!loading && !loadError && rows.length > 0 && (
        <div className="flex justify-end">
          <button onClick={handleSaveAndPublish} disabled={saving} className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white px-6 py-3 rounded-xl font-bold disabled:opacity-60">
            <Send size={18} /> {saving ? 'Saving & Publishing...' : 'Save & Publish All Readings'}
          </button>
        </div>
      )}
    </div>
  );
}
