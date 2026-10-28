// components/landlord/DepositModal.tsx
'use client';
import { useState } from 'react';
import { X, Receipt } from 'lucide-react';

interface DepositModalProps {
  isOpen: boolean;
  onClose: () => void;
  unitId: string;
  initialDeposit: number;
  outstandingArrears: number;
}

export function DepositModal({ 
  isOpen, 
  onClose, 
  unitId, 
  initialDeposit, 
  outstandingArrears 
}: DepositModalProps) {
  const [damages, setDamages] = useState<{desc: string, amount: number}[]>([]);
  const [descInput, setDescInput] = useState('');
  const [amountInput, setAmountInput] = useState('');

  if (!isOpen) return null;

  const totalDamages = damages.reduce((sum, item) => sum + item.amount, 0);
  const netRefund = initialDeposit - outstandingArrears - totalDamages;

  const handleAddDamage = () => {
    if (descInput && amountInput) {
      setDamages([...damages, { desc: descInput, amount: Number(amountInput) }]);
      setDescInput('');
      setAmountInput('');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
      <div className="bg-white rounded-xl shadow-xl max-w-lg w-full p-6 relative">
        <button 
          onClick={onClose} 
          className="absolute top-4 right-4 text-slate-400 hover:text-slate-600 transition-colors"
        >
          <X size={20} />
        </button>
        
        <h2 className="text-xl font-bold text-slate-900 mb-6">Settle Deposit: Unit {unitId}</h2>
        
        <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-3 mb-6">
          <div className="flex justify-between text-sm items-center">
            <span className="text-slate-500 font-medium">Initial Deposit Held:</span>
            <span className="font-bold text-slate-900">KES {initialDeposit.toLocaleString()}</span>
          </div>
          <div className="flex justify-between text-sm items-center">
            <span className="text-slate-500 font-medium">Outstanding Arrears:</span>
            <span className="font-bold text-red-600">- KES {outstandingArrears.toLocaleString()}</span>
          </div>
        </div>

        <div className="mb-6">
          <label className="block text-sm font-medium text-slate-700 mb-2">Itemized Deductions (Damages)</label>
          <div className="flex gap-2 mb-3">
            <input 
              type="text" 
              value={descInput}
              onChange={(e) => setDescInput(e.target.value)}
              placeholder="Damage description" 
              className="flex-1 border border-slate-300 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-emerald-500" 
            />
            <input 
              type="number" 
              value={amountInput}
              onChange={(e) => setAmountInput(e.target.value)}
              placeholder="Amount" 
              className="w-24 border border-slate-300 rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-emerald-500" 
            />
            <button 
              onClick={handleAddDamage}
              className="bg-slate-900 hover:bg-slate-800 text-white px-3 py-2 rounded-lg text-sm font-medium transition-colors"
            >
              Add
            </button>
          </div>

          <ul className="space-y-2 max-h-32 overflow-y-auto">
            {damages.map((item, idx) => (
              <li key={idx} className="flex justify-between text-sm bg-slate-50 px-3 py-2 rounded-lg border border-slate-100 text-slate-600">
                <span>{item.desc}</span>
                <span className="font-medium text-red-600">- KES {item.amount.toLocaleString()}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="pt-6 border-t border-slate-200">
          <div className="flex justify-between items-center mb-6">
            <span className="font-bold text-slate-900 text-lg">Net Refund Due:</span>
            <span className={`font-bold text-2xl ${netRefund >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
              KES {netRefund.toLocaleString()}
            </span>
          </div>
          
          <button 
            onClick={onClose}
            className="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-3 rounded-xl transition-colors flex items-center justify-center gap-2 shadow-sm"
          >
            <Receipt size={18} />
            Finalize Offboarding
          </button>
        </div>
      </div>
    </div>
  );
}