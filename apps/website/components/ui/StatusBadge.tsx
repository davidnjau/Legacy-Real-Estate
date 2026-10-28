// components/ui/StatusBadge.tsx
import React from 'react';

type StatusType = 
  | 'PAID' 
  | 'UNPAID' 
  | 'PARTIAL' 
  | 'VACANT' 
  | 'OCCUPIED' 
  | 'PENDING' 
  | 'RESOLVED' 
  | 'IN_PROGRESS';

interface StatusBadgeProps {
  status: StatusType;
  label?: string;
}

export function StatusBadge({ status, label }: StatusBadgeProps) {
  const styles: Record<StatusType, string> = {
    PAID: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    RESOLVED: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    OCCUPIED: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    
    UNPAID: 'bg-red-50 text-red-700 border-red-200',
    VACANT: 'bg-slate-100 text-slate-700 border-slate-200',
    
    PARTIAL: 'bg-amber-50 text-amber-700 border-amber-200',
    PENDING: 'bg-amber-50 text-amber-700 border-amber-200',
    IN_PROGRESS: 'bg-blue-50 text-blue-700 border-blue-200',
  };

  const displayLabel = label || status.replace('_', ' ');

  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold border tracking-wide uppercase ${styles[status] || 'bg-slate-100 text-slate-700 border-slate-200'}`}>
      {displayLabel}
    </span>
  );
}