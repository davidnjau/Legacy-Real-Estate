'use client';

import { useState, useEffect } from 'react';
import { Upload, Home, Send, CheckCircle, FileSpreadsheet, AlertTriangle, Edit3, Save } from 'lucide-react';

type Assignment = {
  id: string;
  unit_id: string;
  tenant_id: string;
  opening_arrears: number;
  arrears_editable?: boolean;
  units?: { unit_code: string; is_migrated: boolean };
  users?: { full_name: string };
  payments?: { id: string; amount: number; mpesa_reference: string | null; payment_date: string; method: string }[];
};

export default function MigrationPage({ canManageDepositDefaults = true }: { canManageDepositDefaults?: boolean }) {
  const [tab, setTab] = useState<'manual' | 'csv' | 'arrears'>('manual');
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);

  // Manual Form State
  const [unitCode, setUnitCode] = useState('');
  const [floorNumber, setFloorNumber] = useState('');
  const [rentAmount, setRentAmount] = useState('');
  const [garbageAmount, setGarbageAmount] = useState('');
  const [waterRate, setWaterRate] = useState('150');
  const [isOccupied, setIsOccupied] = useState(true);
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [openingArrears, setOpeningArrears] = useState('');
  const [waterReading, setWaterReading] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  // CSV State
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [csvUploading, setCsvUploading] = useState(false);

  // Arrears Editor State
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [arrearsEditable, setArrearsEditable] = useState(true);
  const [arrearsEdits, setArrearsEdits] = useState<Record<string, string>>({});
  const [savingArrearsId, setSavingArrearsId] = useState<string | null>(null);
  const [paymentAssignment, setPaymentAssignment] = useState<Assignment | null>(null);
  const [paymentForm, setPaymentForm] = useState({ amount: '', reference: '', date: new Date().toISOString().slice(0, 10) });
  const [isRecordingPayment, setIsRecordingPayment] = useState(false);
  const [securityDeposit, setSecurityDeposit] = useState('8000');
  const [waterDeposit, setWaterDeposit] = useState('500');

  const [isSavingSettings, setIsSavingSettings] = useState(false);

  const showToast = (type: 'success' | 'error', msg: string) => {
    setToast({ type, msg });
    setTimeout(() => setToast(null), 5000);
  };

  useEffect(() => {
    fetch('/api/migration/settings').then((res) => res.json()).then((data) => {
      if (data.settings) {
        if (data.settings.universal_security_deposit !== undefined) setSecurityDeposit(String(data.settings.universal_security_deposit));
        if (data.settings.universal_water_deposit !== undefined) setWaterDeposit(String(data.settings.universal_water_deposit));
      }
    }).catch(() => undefined);
  }, []);

  const saveSettings = async () => {
    setIsSavingSettings(true);
    try {
      const response = await fetch('/api/migration/settings', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ securityDeposit: Number(securityDeposit), waterDeposit: Number(waterDeposit) }) });
      const data = await response.json();
      showToast(response.ok ? 'success' : 'error', response.ok ? 'Migration deposit defaults saved.' : data.error || 'Unable to save settings.');
    } finally {
      setIsSavingSettings(false);
    }
  };

  const fetchAssignments = async (showLoading = true) => {
    if (showLoading) setLoading(true);
    const response = await fetch('/api/migration/arrears');
    const payload = await response.json();
    const data = response.ok ? payload.assignments || [] : [];
    setAssignments(data as Assignment[]);
    setArrearsEditable(payload.arrearsEditable !== false);
    setArrearsEdits(Object.fromEntries(data.map((assignment: Assignment) => [assignment.id, String(assignment.opening_arrears || 0)])));
    if (showLoading) setLoading(false);
  };

  useEffect(() => {
    if (tab !== 'arrears') return;
    fetchAssignments();
    const interval = setInterval(() => fetchAssignments(false), 15000);
    return () => clearInterval(interval);
  }, [tab]);

  const handleManualSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    try {
      const payload = {
        unitCode, floorNumber, rentAmount, garbageAmount, waterRate,
        isOccupied, fullName, email, phone, openingArrears, waterReading
      };
      const res = await fetch('/api/migration/tenant', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      
      showToast('success', data.message);
      setUnitCode(''); setFullName(''); setEmail(''); setPhone(''); setOpeningArrears(''); setWaterReading('');
    } catch (err: any) {
      showToast('error', err.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCsvUpload = async () => {
    if (!csvFile) return;
    setCsvUploading(true);

    try {
      const text = await csvFile.text();
      const lines = text.split('\n').filter(l => l.trim() !== '');
      if (lines.length < 2) throw new Error('CSV is empty or missing headers');

      const headers = lines[0].split(',').map(h => h.trim().toLowerCase());
      const expected = ['unitcode', 'floor', 'rent', 'garbage', 'waterrate', 'occupied', 'fullname', 'email', 'phone', 'arrears', 'waterreading'];
      if (!expected.every(h => headers.includes(h))) {
        throw new Error('CSV headers must match exactly: unitcode, floor, rent, garbage, waterrate, occupied, fullname, email, phone, arrears, waterreading');
      }

      const parsedUnits = [];
      for (let i = 1; i < lines.length; i++) {
        const cols = lines[i].split(',').map(c => c.trim());
        const row = headers.reduce((acc, h, idx) => ({ ...acc, [h]: cols[idx] }), {} as any);
        
        parsedUnits.push({
          unitCode: row.unitcode,
          floorNumber: row.floor,
          rentAmount: row.rent,
          garbageAmount: row.garbage,
          waterRate: row.waterrate,
          isOccupied: row.occupied.toLowerCase() === 'true' || row.occupied === '1' || row.occupied.toLowerCase() === 'yes',
          fullName: row.fullname,
          email: row.email,
          phone: row.phone,
          openingArrears: row.arrears || '0',
          waterReading: row.waterreading || '',
        });
      }

      const res = await fetch('/api/migration/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ units: parsedUnits }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);

      showToast('success', data.message);
      setCsvFile(null);
    } catch (err: any) {
      showToast('error', err.message);
    } finally {
      setCsvUploading(false);
    }
  };

  const recordArrearsPayment = async () => {
    if (!paymentAssignment) return;
    if (isRecordingPayment) return;
    setIsRecordingPayment(true);
    try {
      const response = await fetch('/api/migration/arrears-payment', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ assignmentId: paymentAssignment.id, amount: paymentForm.amount, mpesaReference: paymentForm.reference, paymentDate: paymentForm.date }) });
      const data = await response.json();
      if (!response.ok) { showToast('error', data.error || 'Unable to record payment.'); return; }
      showToast('success', `Payment recorded. Remaining arrears: Ksh ${Number(data.remainingArrears).toLocaleString()}.`);
      setPaymentAssignment(null);
      setPaymentForm({ amount: '', reference: '', date: new Date().toISOString().slice(0, 10) });
      await fetchAssignments();
    } finally {
      setIsRecordingPayment(false);
    }
  };

  const saveArrears = async (assignment: Assignment) => {
    if (assignment.arrears_editable === false) return;
    const amount = Number(arrearsEdits[assignment.id]);
    if (!Number.isFinite(amount) || amount < 0) {
      showToast('error', 'Enter a valid non-negative arrears amount.');
      return;
    }
    setSavingArrearsId(assignment.id);
    try {
      const response = await fetch('/api/migration/arrears', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates: [{ assignmentId: assignment.id, amount }] }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to update arrears.');
      setAssignments((current) => current.map((item) => item.id === assignment.id ? { ...item, opening_arrears: amount } : item));
      showToast('success', 'Arrears and the first-cycle draft invoice are up to date.');
    } catch (error: any) {
      showToast('error', error.message);
    } finally {
      setSavingArrearsId(null);
    }
  };

  return (
    <div className="space-y-8">
      {toast && (
        <div className={`fixed top-6 right-6 z-50 flex items-center gap-3 px-5 py-3 rounded-xl shadow-lg text-white text-sm font-medium ${toast.type === 'success' ? 'bg-emerald-600' : 'bg-red-500'}`}>
          {toast.type === 'success' ? <CheckCircle size={18} /> : <AlertTriangle size={18} />}
          {toast.msg}
        </div>
      )}

      <div>
        <h1 className="text-3xl font-bold text-slate-900">Apartment Migration Setup</h1>
        <p className="text-slate-500 mt-1">Add properties and tenants, then keep transition arrears current until the first invoices are released.</p>
      </div>

      <div className="flex flex-wrap gap-2 bg-slate-200 p-1 rounded-xl w-fit">
        <button onClick={() => setTab('manual')} className={`px-4 py-2 rounded-lg text-sm font-bold transition-all flex items-center gap-2 ${tab === 'manual' ? 'bg-white shadow-sm text-slate-900' : 'text-slate-500 hover:text-slate-700'}`}><Home size={16}/> Manual Entry</button>
        <button onClick={() => setTab('csv')} className={`px-4 py-2 rounded-lg text-sm font-bold transition-all flex items-center gap-2 ${tab === 'csv' ? 'bg-white shadow-sm text-slate-900' : 'text-slate-500 hover:text-slate-700'}`}><FileSpreadsheet size={16}/> Bulk Import</button>
        <button onClick={() => setTab('arrears')} className={`px-4 py-2 rounded-lg text-sm font-bold transition-all flex items-center gap-2 ${tab === 'arrears' ? 'bg-white shadow-sm text-slate-900' : 'text-slate-500 hover:text-slate-700'}`}><Edit3 size={16}/> Arrears Editor</button>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-8">
        {canManageDepositDefaults && <div className="mb-8 p-5 border border-amber-200 bg-amber-50 rounded-xl">
          <h2 className="font-bold text-amber-900">Migration Deposit Defaults</h2>
          <p className="text-sm text-amber-800 mt-1 mb-4">These defaults apply to migrated tenants when the import does not provide deposit amounts.</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            <label className="text-sm font-medium text-slate-700">Universal Security Deposit (KES)<input type="number" value={securityDeposit} onChange={(e) => setSecurityDeposit(e.target.value)} className="mt-1 w-full border border-slate-300 rounded-lg px-3 py-2 outline-none focus:border-amber-500" /></label>
            <label className="text-sm font-medium text-slate-700">Universal Water Deposit (KES)<input type="number" value={waterDeposit} onChange={(e) => setWaterDeposit(e.target.value)} className="mt-1 w-full border border-slate-300 rounded-lg px-3 py-2 outline-none focus:border-amber-500" /></label>
          </div>
          <button onClick={saveSettings} disabled={isSavingSettings} className="mt-4 bg-amber-600 hover:bg-amber-700 disabled:opacity-60 text-white font-bold px-4 py-2 rounded-lg transition-colors">{isSavingSettings ? 'Saving...' : 'Save Deposit Defaults'}</button>
        </div>}
        
        {/* MANUAL ENTRY */}
        {tab === 'manual' && (
          <div className="max-w-3xl">
            <h2 className="text-xl font-bold text-slate-900 mb-2">Add Unit & Tenant</h2>
            <p className="text-slate-500 text-sm mb-6">Create a unit and immediately assign a tenant to it. The tenant will receive an email invite to set their password.</p>
            
            <form onSubmit={handleManualSubmit} className="space-y-6">
              {/* Unit Details */}
              <div className="p-5 border border-slate-200 rounded-xl bg-slate-50 space-y-4">
                <h3 className="font-bold text-slate-800 border-b border-slate-200 pb-2">1. Unit Details</h3>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                  <div><label className="block text-xs font-bold text-slate-700 mb-1">Unit Code *</label><input type="text" value={unitCode} onChange={e => setUnitCode(e.target.value)} required className="w-full border rounded-lg px-3 py-2 text-sm outline-none" /></div>
                  <div><label className="block text-xs font-bold text-slate-700 mb-1">Floor</label><input type="text" value={floorNumber} onChange={e => setFloorNumber(e.target.value)} className="w-full border rounded-lg px-3 py-2 text-sm outline-none" /></div>
                  <div><label className="block text-xs font-bold text-slate-700 mb-1">Rent Amount *</label><input type="number" value={rentAmount} onChange={e => setRentAmount(e.target.value)} required className="w-full border rounded-lg px-3 py-2 text-sm outline-none" /></div>
                  <div><label className="block text-xs font-bold text-slate-700 mb-1">Garbage Fee *</label><input type="number" value={garbageAmount} onChange={e => setGarbageAmount(e.target.value)} required className="w-full border rounded-lg px-3 py-2 text-sm outline-none" /></div>
                  <div><label className="block text-xs font-bold text-slate-700 mb-1">Water Rate (per unit)</label><input type="number" value={waterRate} onChange={e => setWaterRate(e.target.value)} required className="w-full border rounded-lg px-3 py-2 text-sm outline-none" /></div>
                  <div className="flex items-center mt-6">
                    <label className="flex items-center gap-2 cursor-pointer text-sm font-bold text-slate-700">
                      <input type="checkbox" checked={isOccupied} onChange={e => setIsOccupied(e.target.checked)} className="w-4 h-4 text-emerald-600 rounded" />
                      Unit is Occupied
                    </label>
                  </div>
                </div>
              </div>

              {/* Tenant Details */}
              {isOccupied && (
                <div className="p-5 border border-emerald-200 rounded-xl bg-emerald-50/30 space-y-4">
                  <h3 className="font-bold text-emerald-900 border-b border-emerald-200 pb-2">2. Existing Tenant Details</h3>
                  <div className="grid grid-cols-2 gap-4">
                    <div><label className="block text-xs font-bold text-slate-700 mb-1">Full Name *</label><input type="text" value={fullName} onChange={e => setFullName(e.target.value)} required={isOccupied} className="w-full border rounded-lg px-3 py-2 text-sm outline-none" /></div>
                    <div><label className="block text-xs font-bold text-slate-700 mb-1">Email Address *</label><input type="email" value={email} onChange={e => setEmail(e.target.value)} required={isOccupied} className="w-full border rounded-lg px-3 py-2 text-sm outline-none" /></div>
                    <div><label className="block text-xs font-bold text-slate-700 mb-1">Phone Number *</label><input type="tel" value={phone} onChange={e => setPhone(e.target.value)} required={isOccupied} className="w-full border rounded-lg px-3 py-2 text-sm outline-none" /></div>
                    <div><label className="block text-xs font-bold text-slate-700 mb-1">Initial Water Meter Reading</label><input type="number" value={waterReading} onChange={e => setWaterReading(e.target.value)} className="w-full border rounded-lg px-3 py-2 text-sm outline-none" /></div>
                    <div className="col-span-2">
                      <label className="block text-xs font-bold text-slate-700 mb-1">Opening Arrears Balance (KES)</label>
                      <input type="number" min="0" value={openingArrears} onChange={e => setOpeningArrears(e.target.value)} className="w-full border rounded-lg px-3 py-2 text-sm outline-none max-w-[200px]" />
                      <p className="text-xs text-slate-500 mt-1">You can continue editing this balance in the Arrears Editor until the first invoice is released.</p>
                    </div>
                  </div>
                </div>
              )}
              
              <button type="submit" disabled={isSubmitting} className="flex items-center justify-center gap-2 bg-slate-900 hover:bg-slate-800 text-white font-bold px-6 py-3 rounded-xl transition-colors mt-4">
                <Send size={18} /> {isSubmitting ? 'Saving...' : (isOccupied ? 'Add Unit & Invite Tenant' : 'Add Vacant Unit')}
              </button>
            </form>
          </div>
        )}

        {/* CSV IMPORT */}
        {tab === 'csv' && (
          <div className="max-w-xl">
            <div className="flex items-start gap-4 bg-blue-50 border border-blue-200 rounded-xl p-5 mb-6">
              <FileSpreadsheet size={24} className="text-blue-500 shrink-0 mt-1" />
              <div>
                <h3 className="font-bold text-blue-900">Upload Property CSV File</h3>
                <p className="text-sm text-blue-800 mt-1">Your CSV must have these exact column headers (all lowercase):<br/><br/><code className="bg-white px-2 py-1 rounded text-blue-900 font-mono text-xs font-bold">unitcode, floor, rent, garbage, waterrate, occupied, fullname, email, phone, arrears, waterreading</code></p>
                <p className="text-xs text-blue-700 mt-2">Use occupied = TRUE or FALSE. Leave tenant fields empty if FALSE.</p>
              </div>
            </div>

            <div className="border-2 border-dashed border-slate-300 rounded-xl p-8 text-center bg-slate-50">
              <input type="file" accept=".csv" onChange={(e) => setCsvFile(e.target.files?.[0] || null)} className="hidden" id="csv-upload" />
              <label htmlFor="csv-upload" className="cursor-pointer flex flex-col items-center gap-3">
                <div className="w-12 h-12 bg-white rounded-full flex items-center justify-center border border-slate-200 shadow-sm">
                  <Upload className="text-slate-400" size={20} />
                </div>
                <span className="text-sm font-medium text-emerald-600 hover:text-emerald-700 underline">Browse files</span>
                <span className="text-xs text-slate-400">{csvFile ? csvFile.name : 'Select a .csv file'}</span>
              </label>
            </div>

            <button onClick={handleCsvUpload} disabled={!csvFile || csvUploading} className="flex items-center justify-center gap-2 w-full bg-slate-900 hover:bg-slate-800 disabled:bg-slate-300 disabled:cursor-not-allowed text-white font-bold py-3 rounded-xl transition-colors mt-4">
              <Send size={18} /> {csvUploading ? 'Importing...' : 'Run Import'}
            </button>
          </div>
        )}

        {/* ARREARS EDITOR */}
        {tab === 'arrears' && (
          <div>
            <div className="flex justify-between items-center mb-4">
              <div>
                <h2 className="text-xl font-bold text-slate-900">Transition Arrears Editor</h2>
                <p className="text-slate-500 text-sm">Transition ledger for migrated tenants. Manual and M-Pesa payments share the same balance and payment history.</p>
                {!arrearsEditable && <p className="text-amber-700 text-sm mt-1">Transition balances have moved to the monthly ledger.</p>}
              </div>
            </div>

            <div className="overflow-x-auto border border-slate-200 rounded-xl">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 border-b border-slate-200 text-slate-600">
                  <tr>
                    <th className="p-4 font-medium">Unit</th>
                    <th className="p-4 font-medium">Tenant</th>
                    <th className="p-4 font-medium">Arrears (KES)</th>
                    <th className="p-4 font-medium text-right">Payment</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {loading ? (
                    <tr><td colSpan={4} className="p-8 text-center text-slate-400 animate-pulse">Loading...</td></tr>
                  ) : assignments.length === 0 ? (
                    <tr><td colSpan={4} className="p-8 text-center text-slate-400">No migrated tenants found.</td></tr>
                  ) : assignments.map(a => (
                    <tr key={a.id} className="hover:bg-slate-50">
                      <td className="p-4 font-bold text-slate-900">{a.units?.unit_code || '-'}</td>
                      <td className="p-4 text-slate-700">{a.users?.full_name || '-'}</td>
                      <td className="p-4">
                        <div className="flex items-center gap-2">
                          <input type="number" min="0" disabled={a.arrears_editable === false} value={arrearsEdits[a.id] ?? String(a.opening_arrears || 0)} onChange={(event) => setArrearsEdits((current) => ({ ...current, [a.id]: event.target.value }))} className="w-36 border border-slate-300 rounded-lg px-2 py-1.5 font-mono disabled:bg-slate-100" aria-label={`Arrears for ${a.units?.unit_code || 'unit'}`} />
                          <button onClick={() => saveArrears(a)} disabled={a.arrears_editable === false || savingArrearsId === a.id} className="inline-flex items-center gap-1 border border-slate-300 text-slate-700 px-2.5 py-1.5 rounded-lg font-semibold disabled:opacity-50"><Save size={14} />{savingArrearsId === a.id ? 'Saving' : 'Save'}</button>
                        </div>
                        {a.payments && a.payments.length > 0 && <div className="mt-2 space-y-1 text-xs text-slate-500">{a.payments.slice(0, 3).map((payment) => <p key={payment.id}>{payment.payment_date} · {payment.method === 'stk_push' ? 'M-Pesa' : 'Manual'} · {Number(payment.amount).toLocaleString()} KES{payment.mpesa_reference ? ` · ${payment.mpesa_reference}` : ''}</p>)}</div>}
                      </td>
                      <td className="p-4 text-right"><button onClick={() => setPaymentAssignment(a)} disabled={a.arrears_editable === false} className="border border-emerald-300 text-emerald-700 px-3 py-1.5 rounded-lg font-bold disabled:opacity-40 disabled:cursor-not-allowed">Record Payment</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

      </div>

      {paymentAssignment && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6">
            <div className="flex justify-between items-center mb-5"><h2 className="text-xl font-bold">Record Arrears Payment</h2><button onClick={() => setPaymentAssignment(null)} className="text-slate-400">X</button></div>
            <p className="text-sm text-slate-600 mb-4">{paymentAssignment.units?.unit_code || '-'} — {paymentAssignment.users?.full_name || '-'}. Current arrears: Ksh {Number(paymentAssignment.opening_arrears || 0).toLocaleString()}.</p>
            <div className="space-y-4"><label className="block text-sm">Amount Paid<input type="number" min="0" value={paymentForm.amount} onChange={(e) => setPaymentForm({ ...paymentForm, amount: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label><label className="block text-sm">M-Pesa Reference<input value={paymentForm.reference} onChange={(e) => setPaymentForm({ ...paymentForm, reference: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label><label className="block text-sm">Payment Date<input type="date" value={paymentForm.date} onChange={(e) => setPaymentForm({ ...paymentForm, date: e.target.value })} className="mt-1 w-full border rounded-lg px-3 py-2" /></label></div>
            <button onClick={recordArrearsPayment} disabled={isRecordingPayment} className="mt-5 w-full bg-emerald-600 text-white font-bold py-3 rounded-xl disabled:opacity-60 disabled:cursor-not-allowed">{isRecordingPayment ? 'Saving Payment...' : 'Save Payment & Update Arrears'}</button>
          </div>
        </div>
      )}
    </div>
  );
}
