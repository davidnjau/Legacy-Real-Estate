// components/landlord/WaterBatchTable.tsx
'use client';
import { useState } from 'react';
import { Droplet } from 'lucide-react';

interface DraftInvoice {
  id: string;
  tenant: string;
  rent: number;
  garbage: number;
  arrears: number;
}

interface WaterBatchTableProps {
  drafts: DraftInvoice[];
  waterRate: number;
}

export function WaterBatchTable({ drafts, waterRate }: WaterBatchTableProps) {
  const [readings, setReadings] = useState<Record<string, number>>({});

  const handleReadingChange = (id: string, value: string) => {
    setReadings(prev => ({ ...prev, [id]: Number(value) || 0 }));
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left border-collapse">
        <thead>
          <tr className="border-b border-slate-200 text-sm text-slate-500 bg-slate-50">
            <th className="p-4 font-medium">Unit</th>
            <th className="p-4 font-medium">Fixed (Rent + Garbage)</th>
            <th className="p-4 font-medium">Carried Arrears</th>
            <th className="p-4 font-medium bg-blue-50/50">Water Meter Input</th>
            <th className="p-4 font-medium text-right">Total Payable</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200 bg-white">
          {drafts.map((draft) => {
            const waterUnits = readings[draft.id] || 0;
            const waterCost = waterUnits * waterRate;
            const total = draft.rent + draft.garbage + draft.arrears + waterCost;

            return (
              <tr key={draft.id} className="hover:bg-slate-50 transition-colors">
                <td className="p-4 font-bold text-slate-900">{draft.id}</td>
                <td className="p-4 text-slate-700">KES {(draft.rent + draft.garbage).toLocaleString()}</td>
                <td className="p-4 text-red-600 font-medium">
                  {draft.arrears > 0 ? `KES ${draft.arrears.toLocaleString()}` : '-'}
                </td>
                <td className="p-4 bg-blue-50/30">
                  <div className="flex items-center gap-2">
                    <Droplet size={16} className="text-blue-500" />
                    <input 
                      type="number"
                      min="0"
                      placeholder="Units"
                      onChange={(e) => handleReadingChange(draft.id, e.target.value)}
                      className="w-24 border border-slate-300 rounded-md px-2 py-1 text-sm outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                </td>
                <td className="p-4 text-right font-bold text-emerald-600">
                  KES {total.toLocaleString()}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}