// types/index.ts
export type UnitStatus = 'OCCUPIED' | 'VACANT' | 'MAINTENANCE';

export type InvoiceStatus = 'PAID' | 'UNPAID' | 'PARTIAL';

export type TicketStatus = 'PENDING' | 'IN_PROGRESS' | 'RESOLVED';

export interface Unit {
  id: string;
  name: string;
  status: UnitStatus;
  tenantName?: string;
  tenantPhone?: string;
  depositHeld: number;
  claimCode?: string;
}

export interface Invoice {
  id: string;
  unitId: string;
  tenantId: string;
  month: string;
  rent: number;
  garbage: number;
  waterUnits: number;
  waterRate: number;
  arrears: number;
  status: InvoiceStatus;
}

export interface LedgerRecord {
  id: string;
  unitId: string;
  tenant: string;
  billed: number;
  arrears: number;
  paid: number;
  balance: number;
  status: InvoiceStatus;
}

export interface MaintenanceTicket {
  id: string;
  unitId: string;
  tenantName: string;
  category: string;
  description: string;
  status: TicketStatus;
  priority: 'LOW' | 'MEDIUM' | 'HIGH';
  createdAt: string;
}