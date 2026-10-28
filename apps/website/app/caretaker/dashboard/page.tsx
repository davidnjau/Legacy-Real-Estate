'use client';

import { useEffect, useState } from 'react';
import { Home, CheckCircle, AlertCircle, Clock, Pencil, Save, X, Droplets, PlayCircle } from 'lucide-react';

type UnitStatus = {
  id: string;
  unitCode: string;
  status: string;
  floor: string | number;
  rentAmount: number;
  tenantName: string | null;
  baselineNeeded?: boolean;
};

const statusConfig: Record<string, { label: string; bg: string; border: string; icon: any; textColor: string }> = {
  occupied: {
    label: 'Occupied',
    bg: 'bg-emerald-50',
    border: 'border-emerald-200',
    icon: CheckCircle,
    textColor: 'text-emerald-700',
  },
  notice_given: {
    label: 'Moving Out',
    bg: 'bg-amber-50',
    border: 'border-amber-200',
    icon: Clock,
    textColor: 'text-amber-700',
  },
  vacant: {
    label: 'Vacant',
    bg: 'bg-slate-50',
    border: 'border-slate-200',
    icon: AlertCircle,
    textColor: 'text-slate-500',
  },
  deposit_pending: {
    label: 'Deposit Pending',
    bg: 'bg-purple-50',
    border: 'border-purple-200',
    icon: Clock,
    textColor: 'text-purple-700',
  },
};

export default function CaretakerDashboard() {
  const [units, setUnits] = useState<UnitStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [floorFilter, setFloorFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [profile, setProfile] = useState({ full_name: '', email: '', phone: '', created_at: '' });
  const [profileForm, setProfileForm] = useState({ fullName: '', email: '', phone: '' });
  const [editingProfile, setEditingProfile] = useState(false);
  const [profileMessage, setProfileMessage] = useState('');
  const [profileError, setProfileError] = useState('');
  const [baselineUnit, setBaselineUnit] = useState<UnitStatus | null>(null);
  const [baselineReading, setBaselineReading] = useState('');
  const [baselineSaving, setBaselineSaving] = useState(false);
  const [baselineSuccess, setBaselineSuccess] = useState('');
  const [waterReminderDue, setWaterReminderDue] = useState(false);
  const [waterReadingsPublished, setWaterReadingsPublished] = useState(false);

  const fetchUnits = async () => {
    try {
      const res = await fetch('/api/caretaker/units');
      const data = await res.json();
      setUnits(data.units || []);
      setWaterReminderDue(data.waterReadingReminderDue === true);
      setWaterReadingsPublished(data.waterReadingsPublished === true);
    } catch (err) {
      console.error('Failed to load units', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUnits();
  }, []);

  const openBaselineModal = (unit: UnitStatus) => {
    setBaselineUnit(unit);
    setBaselineReading('');
    setBaselineSuccess('');
  };

  const saveBaseline = async () => {
    const supabase = await import('../../../lib/supabase/client').then((module) => module.createClient());
    const { data: user } = await supabase.auth.getUser();
    if (!user.user || !baselineUnit || !baselineReading) return;
    setBaselineSaving(true);
    setBaselineSuccess('');
    const response = await fetch('/api/caretaker/water-readings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        caretakerId: user.user.id,
        baseline: { unitId: baselineUnit.id, reading: Number(baselineReading) },
        readings: [], // Required by the API shape
      }),
    });
    const data = await response.json();
    setBaselineSaving(false);
    if (!response.ok) { window.alert(data.error || 'Unable to save baseline reading.'); return; }

    // Mark this unit as done in the local state
    setUnits((current) => current.map((unit) => unit.id === baselineUnit.id ? { ...unit, baselineNeeded: false } : unit));
    setBaselineSuccess(`Baseline reading of ${Number(baselineReading).toLocaleString()} saved for ${baselineUnit.unitCode}.`);
    setBaselineReading('');

    // Check if there are more units needing baselines
    const remaining = units.filter(u => u.baselineNeeded && u.id !== baselineUnit.id);
    if (remaining.length > 0) {
      // Auto-advance to the next unit after a brief delay
      setTimeout(() => {
        setBaselineUnit(remaining[0]);
        setBaselineSuccess('');
      }, 1500);
    } else {
      // All done — close after a brief delay
      setTimeout(() => {
        setBaselineUnit(null);
        setBaselineSuccess('');
      }, 1500);
    }
  };

  const unitsNeedingBaseline = units.filter(u => u.baselineNeeded);
  const occupied = units.filter(u => u.status === 'occupied' || u.status === 'notice_given').length;
  const vacant = units.filter(u => u.status === 'vacant').length;
  const floors = [...new Set(units.map((unit) => String(unit.floor || '')).filter(Boolean))].sort();
  const filteredUnits = units.filter((unit) => (floorFilter === 'all' || String(unit.floor) === floorFilter) && (statusFilter === 'all' || unit.status === statusFilter));

  return (
    <div className="space-y-8">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold text-slate-900">Room Status</h1>
          <p className="text-slate-500 mt-1">Overview of all units in the apartment.</p>
        </div>
        <button
          onClick={() => window.location.assign('?tour=true')}
          className="flex items-center gap-2 border border-emerald-200 bg-emerald-50 hover:bg-emerald-100 text-emerald-700 rounded-lg px-3 py-2 text-sm font-semibold transition-colors"
        >
          <PlayCircle size={16} /> Take Tour
        </button>
      </div>

      {/* Baseline Needed Alert */}
      {unitsNeedingBaseline.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-5">
          <div className="flex items-center justify-between gap-4 mb-3">
            <div>
              <h2 className="font-bold text-amber-900">Baseline water readings needed</h2>
              <p className="text-sm text-amber-800 mt-1">
                {unitsNeedingBaseline.length} occupied unit(s) need a move-in water meter reading before monthly billing can start.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {unitsNeedingBaseline.map(unit => (
              <button
                key={unit.id}
                onClick={() => openBaselineModal(unit)}
                className="inline-flex items-center gap-2 bg-amber-600 hover:bg-amber-700 text-white font-bold px-3 py-1.5 rounded-lg text-sm transition-colors"
              >
                <Droplets size={14} /> {unit.unitCode}{unit.tenantName ? ` — ${unit.tenantName}` : ''}
              </button>
            ))}
          </div>
        </div>
      )}

      {waterReminderDue && !waterReadingsPublished && (
        <div className="bg-blue-50 border border-blue-200 rounded-xl p-5 flex items-center justify-between gap-4">
          <div><h2 className="font-bold text-blue-900">Monthly water readings due</h2><p className="text-sm text-blue-800 mt-1">Enter and publish the current meter readings so the landlord can prepare invoices.</p></div>
          <a href="/caretaker/water-readings" className="shrink-0 bg-blue-600 hover:bg-blue-700 text-white font-bold px-4 py-2 rounded-lg">Record readings</a>
        </div>
      )}

      {/* Summary Bar */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-white rounded-xl border border-slate-200 p-5 text-center shadow-sm">
          <div className="text-2xl font-bold text-slate-900">{units.length}</div>
          <div className="text-sm text-slate-500 mt-1">Total Units</div>
        </div>
        <div className="bg-emerald-50 rounded-xl border border-emerald-200 p-5 text-center shadow-sm">
          <div className="text-2xl font-bold text-emerald-700">{occupied}</div>
          <div className="text-sm text-emerald-600 mt-1">Occupied</div>
        </div>
        <div className="bg-slate-100 rounded-xl border border-slate-200 p-5 text-center shadow-sm">
          <div className="text-2xl font-bold text-slate-600">{vacant}</div>
          <div className="text-sm text-slate-500 mt-1">Vacant</div>
        </div>
      </div>

      {/* Unit Table */}
      {loading ? (
        <div className="text-slate-500 animate-pulse py-12 text-center">Loading units...</div>
      ) : (
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="p-4 border-b flex flex-wrap gap-3">
            <select value={floorFilter} onChange={(event) => setFloorFilter(event.target.value)} className="border rounded-lg px-3 py-2 text-sm"><option value="all">All floors</option>{floors.map((floor) => <option key={floor} value={floor}>{floor}</option>)}</select>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="border rounded-lg px-3 py-2 text-sm"><option value="all">All statuses</option>{Object.keys(statusConfig).map((status) => <option key={status} value={status}>{statusConfig[status].label}</option>)}</select>
          </div>
          <table className="w-full text-left text-sm"><thead className="bg-slate-50"><tr><th className="p-4">Room</th><th className="p-4">Floor</th><th className="p-4">Tenant</th><th className="p-4">Status</th><th className="p-4">Water Baseline</th></tr></thead><tbody className="divide-y">{filteredUnits.map((unit) => { const config = statusConfig[unit.status] || statusConfig.vacant; return <tr key={unit.id}><td className="p-4 font-bold">{unit.unitCode}</td><td className="p-4">{unit.floor || '-'}</td><td className="p-4 text-slate-600">{unit.tenantName || '-'}</td><td className={`p-4 font-bold ${config.textColor}`}>{config.label}</td><td className="p-4">{unit.baselineNeeded ? <button onClick={() => openBaselineModal(unit)} className="text-xs bg-amber-100 text-amber-700 font-bold px-2 py-1 rounded-md border border-amber-200 hover:bg-amber-200 transition-colors">Add Reading</button> : (unit.status === 'occupied' || unit.status === 'notice_given') ? <span className="text-xs text-emerald-700 bg-emerald-50 px-2 py-1 rounded-md border border-emerald-200 font-bold">✓ Set</span> : '—'}</td></tr>; })}</tbody></table>
        </div>
      )}

      {/* Baseline Modal */}
      {baselineUnit && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6">
            <div className="flex justify-between items-center mb-5">
              <h2 className="text-xl font-bold">Baseline Reading: {baselineUnit.unitCode}</h2>
              <button onClick={() => { setBaselineUnit(null); setBaselineSuccess(''); }} className="text-slate-400 hover:text-slate-600"><X size={22} /></button>
            </div>

            {baselineSuccess ? (
              <div className="flex items-center gap-3 bg-emerald-50 border border-emerald-200 rounded-xl p-4 text-emerald-800 font-medium text-sm">
                <CheckCircle size={20} className="text-emerald-600 shrink-0" />
                {baselineSuccess}
              </div>
            ) : (
              <>
                <p className="text-sm text-slate-600 mb-4">
                  Enter the current water meter reading for <strong>{baselineUnit.tenantName || baselineUnit.unitCode}</strong>.
                  This reading is the baseline and will <strong>not</strong> be billed as usage — it's the starting point for future calculations.
                </p>
                <label className="text-sm font-medium block mb-1">
                  Meter Reading
                </label>
                <input
                  type="number"
                  min="0"
                  value={baselineReading}
                  onChange={(event) => setBaselineReading(event.target.value)}
                  className="w-full border rounded-lg px-3 py-2.5 text-lg font-mono"
                  placeholder="e.g. 4523"
                  autoFocus
                />
                <button
                  onClick={saveBaseline}
                  disabled={baselineSaving || !baselineReading}
                  className="mt-5 w-full inline-flex justify-center items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-3 rounded-xl disabled:opacity-60 transition-colors"
                >
                  <Droplets size={18} /> {baselineSaving ? 'Saving...' : 'Save Baseline Reading'}
                </button>
                {unitsNeedingBaseline.length > 1 && (
                  <p className="text-xs text-slate-400 text-center mt-3">
                    {unitsNeedingBaseline.filter(u => u.id !== baselineUnit.id).length} more unit(s) remaining after this one.
                  </p>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
