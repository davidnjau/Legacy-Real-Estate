// components/tenant/BillCard.tsx
'use client';
import { useState } from 'react';
import { Receipt, Smartphone, CheckCircle2, AlertCircle } from 'lucide-react';

interface BillCardProps {
  month: string;
  rent: number;
  garbage: number;
  waterUnits: number;
  waterRate: number;
  arrears: number;
  status: 'PAID' | 'UNPAID' | 'PARTIAL';
}

export function BillCard({
  month,
  rent,
  garbage,
  waterUnits,
  waterRate,
  arrears,
  status,
}: BillCardProps) {
  const [isPaying, setIsPaying] = useState(false);
  const [phone, setPhone] = useState('');
  const [paymentInitiated, setPaymentInitiated] = useState(false);

  const waterCost = waterUnits * waterRate;
  const totalPayable = rent + garbage + waterCost + arrears;

  const handleStkPush = (e: React.FormEvent) => {
    e.preventDefault();
    setIsPaying(true);
    // Simulate STK push request to Daraja API
    setTimeout(() => {
      setIsPaying(false);
      setPaymentInitiated(true);
    }, 2000);
  };

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
      <div className="bg-slate-900 p-6 text-white flex justify-between items-center">
        <div>
          <span className="text-xs uppercase tracking-wider text-emerald-400 font-bold">Current Statement</span>
          <h2 className="text-xl font-bold mt-1">{month} Billing</h2>
        </div>
        <span className={`px-3 py-1 text-xs font-bold rounded-full ${
          status === 'PAID' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' :
          status === 'PARTIAL' ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30' :
          'bg-red-500/20 text-red-400 border border-red-500/30'
        }`}>
          {status}
        </span>
      </div>

      <div className="p-6 space-y-4">
        <div className="space-y-3 text-sm">
          <div className="flex justify-between py-1 border-b border-slate-100">
            <span className="text-slate-500">Base Rent</span>
            <span className="font-semibold text-slate-900">KES {rent.toLocaleString()}</span>
          </div>
          <div className="flex justify-between py-1 border-b border-slate-100">
            <span className="text-slate-500">Garbage Collection</span>
            <span className="font-semibold text-slate-900">KES {garbage.toLocaleString()}</span>
          </div>
          <div className="flex justify-between py-1 border-b border-slate-100">
            <span className="text-slate-500">Water ({waterUnits} Units @ {waterRate})</span>
            <span className="font-semibold text-slate-900">KES {waterCost.toLocaleString()}</span>
          </div>
          {arrears > 0 && (
            <div className="flex justify-between py-1 border-b border-slate-100 text-red-600">
              <span className="font-medium">Carried Arrears</span>
              <span className="font-bold">KES {arrears.toLocaleString()}</span>
            </div>
          )}
          <div className="flex justify-between pt-2 text-base font-bold text-slate-900">
            <span>Total Payable</span>
            <span className="text-emerald-600">KES {totalPayable.toLocaleString()}</span>
          </div>
        </div>

        {status !== 'PAID' && !paymentInitiated && (
          <form onSubmit={handleStkPush} className="mt-6 pt-6 border-t border-slate-100 space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">M-Pesa Phone Number</label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <Smartphone size={16} className="text-slate-400" />
                </div>
                <input
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="07XX XXX XXX"
                  required
                  className="block w-full pl-9 pr-3 py-2 border border-slate-300 rounded-lg text-sm outline-none focus:ring-2 focus:ring-emerald-500"
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={isPaying}
              className="w-full flex items-center justify-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-2.5 px-4 rounded-lg transition-colors text-sm disabled:opacity-70 shadow-sm"
            >
              <Receipt size={16} />
              {isPaying ? 'Sending STK Push...' : `Pay KES ${totalPayable.toLocaleString()} via M-Pesa`}
            </button>
          </form>
        )}

        {paymentInitiated && (
          <div className="mt-6 p-4 bg-emerald-50 border border-emerald-100 rounded-xl flex items-start gap-3">
            <CheckCircle2 className="w-5 h-5 text-emerald-600 shrink-0 mt-0.5" />
            <div className="text-sm text-emerald-900">
              <p className="font-bold">STK Push Sent!</p>
              <p className="text-emerald-700 mt-0.5">Please check your phone and enter your M-Pesa PIN to complete the transaction.</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}