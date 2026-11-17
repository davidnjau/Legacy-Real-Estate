import { NextResponse } from 'next/server';
import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

type DjangoLedgerResponse = {
  month: string;
  summary: { total_spend: string; total_repairs: number };
  ledger: Array<{
    id: number;
    ticket_id: string;
    unit_code: string;
    tenant_name: string;
    category: string;
    description: string;
    fundi_name: string;
    cost: string | null;
    resolved_at: string;
  }>;
};

export async function GET(request: Request) {
  try {
    const query = new URL(request.url).search;
    const response = await forwardDjangoRequest(request, `/maintenance-ledger${query}`);
    if (!response.ok) return response;
    const payload = await response.json() as DjangoLedgerResponse;
    return NextResponse.json({
      month: payload.month,
      summary: {
        totalSpend: Number(payload.summary.total_spend),
        totalRepairs: payload.summary.total_repairs,
      },
      ledger: payload.ledger.map((row) => ({
        id: String(row.id),
        ticketId: row.ticket_id,
        unitCode: row.unit_code,
        tenantName: row.tenant_name,
        category: row.category,
        description: row.description,
        fundiName: row.fundi_name,
        cost: row.cost === null ? null : Number(row.cost),
        resolvedAt: row.resolved_at,
      })),
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load maintenance ledger data.');
  }
}
