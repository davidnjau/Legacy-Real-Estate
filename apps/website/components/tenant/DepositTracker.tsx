// components/tenant/DepositTracker.tsx
'use client';
import { ShieldCheck, Clock, CheckCircle2, AlertCircle } from 'lucide-react';

interface Deduction {
  desc: string;
  amount: number;
}

interface DepositTrackerProps {
  initialDeposit: number;
  status: 'HELD' | 'PROCESSING' | 'REFUNDED';
  deductions?: Deduction[];
  refundReference?: string;
}

export function DepositTracker({
  initialDeposit,
  status,
  deductions = [],
  refundReference,
}: DepositTrackerProps) {
  const totalDeductions = deductions.reduce((sum, item) => sum + item.amount, 0);
  const netRefund = initialDeposit - totalDeductions;

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
      <div className="p-6 border-b border-slate-100 flex justify-between items-center">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-emerald-50 text-emerald-600 rounded-xl flex items-center justify-center">
            <ShieldCheck size={22} />
          </div>
          <div>
            <h3 className="font-bold text-slate-900">Security Deposit Status</h3>
            <p className="text-xs text-slate-500">Held securely under escrow</p>
          </div>
        </div>
        
        <span className={`px-3 py-1 text-xs font-bold rounded-full ${
          status === 'HELD' ? 'bg-blue-100 text-blue-700' :
          status === 'PROCESSING' ? 'bg-amber-100 text-amber-700' :
          'bg-emerald-100 text-emerald-700'
        }`}>
          {status}
        </span>
      </div>

      <div className="p-6 space-y-6">
        <div className="flex justify-between items-center bg-slate-50 p-4 rounded-xl border border-slate-100">
          <span className="text-sm font-medium text-slate-600">Initial Deposit Paid</span>
          <span className="font-bold text-slate-900 text-lg">KES {initialDeposit.toLocaleString()}</span>
        </div>

        {deductions.length > 0 && (
          <div>
            <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-3">Itemized Move-out Deductions</h4>
            <ul className="space-y-2">
              {deductions.map((item, idx) => (
                <li key={idx} className="flex justify-between text-sm py-2 px-3 bg-red-50/50 border border-red-100 rounded-lg text-slate-700">
                  <span>{item.desc}</span>
                  <span className="font-semibold text-red-600">- KES {item.amount.toLocaleString()}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="pt-4 border-t border-slate-100 flex justify-between items-center">
          <div>
            <span className="text-sm font-bold text-slate-900">Net Refundable Amount</span>
            {refundReference && (
              <p className="text-xs text-slate-500 font-mono mt-0.5">M-Pesa Ref: {refundReference}</p>
            )}
          </div>
          <span className="text-xl font-extrabold text-emerald-600">
            KES {netRefund.toLocaleString()}
          </span>
        </div>
      </div>
    </div>
  );
}