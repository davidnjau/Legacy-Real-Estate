import { NextResponse } from 'next/server';
import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

type DjangoTicket = {
  id: number;
  property_id: number;
  property_name: string;
  category: string;
  description: string;
  priority: string;
  status: string;
  unit_code: string | null;
  reported_by_name: string;
  assigned_to: number | null;
  created_at: string;
  assigned_to_name: string | null;
  can_update: boolean;
  evidence: Array<{ name: string; download_path: string }>;
};

const pendingStatuses = new Set([
  'open',
  'assigned',
  'quotation',
  'awaiting_landlord_approval',
]);

export async function GET(request: Request) {
  try {
    const response = await forwardDjangoRequest(request, '/maintenance-tickets');
    if (!response.ok) return response;
    const payload = await response.json() as { tickets?: DjangoTicket[] };
    const tickets = Array.isArray(payload.tickets) ? payload.tickets : [];
    const formatted = tickets.map((ticket) => ({
      id: String(ticket.id),
      propertyId: ticket.property_id,
      propertyName: ticket.property_name,
      ticketId: `TCK-${String(ticket.id).padStart(3, '0')}`,
      category: ticket.category,
      description: ticket.description,
      priority: ticket.priority,
      status: ticket.status,
      unitCode: ticket.unit_code || '-',
      tenantName: ticket.reported_by_name || '-',
      createdAt: ticket.created_at,
      assignedToId: ticket.assigned_to,
      canUpdate: ticket.can_update,
      fundiName: ticket.assigned_to_name || '-',
      cost: 0,
      evidence: ticket.evidence || [],
    }));
    return NextResponse.json({
      pending: formatted.filter((ticket) => pendingStatuses.has(ticket.status)),
      inProgress: formatted.filter((ticket) => ticket.status === 'in_progress'),
      resolved: formatted.filter((ticket) => (
        ticket.status === 'completed' || ticket.status === 'closed'
      )),
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load maintenance tickets.');
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/maintenance-tickets/create', {
      method: 'POST',
      body,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to create the maintenance ticket.');
  }
}

export async function PATCH(request: Request) {
  try {
    const body = await request.json() as {
      ticketId?: unknown;
      status?: unknown;
      assignedTo?: unknown;
      fundiName?: unknown;
      repairCost?: unknown;
    };
    const ticketId = String(body.ticketId ?? '');
    if (
      !/^\d+$/.test(ticketId)
      || typeof body.status !== 'string'
      || !['assigned', 'in_progress', 'completed'].includes(body.status)
    ) {
      return NextResponse.json({ error: 'A valid ticket ID and maintenance status are required.' }, { status: 400 });
    }
    const assignedTo = body.assignedTo;
    if (
      assignedTo !== undefined
      && typeof assignedTo !== 'number'
      && typeof assignedTo !== 'string'
    ) {
      return NextResponse.json({ error: 'The assignee ID is invalid.' }, { status: 400 });
    }
    let resolution: { fundi_name: string; repair_cost: string } | null = null;
    if (body.status === 'completed') {
      const repairCost = Number(body.repairCost);
      if (
        typeof body.fundiName !== 'string'
        || !body.fundiName.trim()
        || (typeof body.repairCost !== 'number' && typeof body.repairCost !== 'string')
        || (typeof body.repairCost === 'string' && !body.repairCost.trim())
        || !Number.isFinite(repairCost)
        || repairCost < 0
      ) {
        return NextResponse.json(
          { error: 'A technician name and valid repair cost are required to complete a ticket.' },
          { status: 400 },
        );
      }
      resolution = {
        fundi_name: body.fundiName.trim(),
        repair_cost: String(body.repairCost),
      };
    }
    return await forwardDjangoRequest(
      request,
      `/maintenance-tickets/${ticketId}/status`,
      {
        method: 'POST',
        body: {
          status: body.status,
          ...(assignedTo !== undefined ? { assigned_to: assignedTo } : {}),
          ...(resolution || {}),
        },
      },
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to update the maintenance ticket.');
  }
}
