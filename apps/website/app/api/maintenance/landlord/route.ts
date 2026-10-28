import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { getActivePropertyId, getPropertyUnitIds } from '../../../../lib/property-access';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
  }
);

export async function GET() {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;
    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
    const { unitIds, error: unitsError } = await getPropertyUnitIds(supabaseAdmin, scope.propertyId);
    if (unitsError) return NextResponse.json({ error: unitsError }, { status: 500 });

    // Fetch all open maintenance tickets (for landlord board)
    const { data: tickets, error: ticketError } = unitIds?.length ? await supabaseAdmin
      .from('maintenance_tickets')
      .select('*, unit_id, tenant_id, fundi_name, cost')
      .in('unit_id', unitIds)
      .order('created_at', { ascending: false })
      : { data: [], error: null };

    if (ticketError) {
      return NextResponse.json({ error: ticketError.message }, { status: 500 });
    }

    // Group by status
    const pending = [];
    const inProgress = [];
    const resolved = [];

    for (const ticket of tickets || []) {
      const unit = await supabaseAdmin
        .from('units')
        .select('unit_code')
        .eq('id', ticket.unit_id)
        .maybeSingle();

      const tenant = await supabaseAdmin
        .from('users')
        .select('full_name')
        .eq('id', ticket.tenant_id)
        .maybeSingle();

      const formatted = {
        id: ticket.id,
        ticketId: `TCK-${String(ticket.id).slice(-3).toUpperCase()}`,
        category: ticket.category,
        description: ticket.description,
        priority: ticket.priority,
        status: ticket.status,
        unitCode: unit.data?.unit_code || '-',
        tenantName: tenant.data?.full_name || '-',
        createdAt: ticket.created_at,
        fundiName: ticket.fundi_name || '-',
        cost: Number(ticket.cost || 0),
      };

      if (ticket.status === 'pending') {
        pending.push(formatted);
      } else if (ticket.status === 'in_progress') {
        inProgress.push(formatted);
      } else if (ticket.status === 'resolved') {
        resolved.push(formatted);
      }
    }

    return NextResponse.json({
      pending,
      inProgress,
      resolved,
    });
  } catch (error: any) {
    console.error('Failed to fetch landlord maintenance tickets:', error);
    return NextResponse.json(
      { error: 'Failed to fetch maintenance tickets.' },
      { status: 500 }
    );
  }
}

  export async function PATCH(request: Request) {
    try {
      const auth = await requireApiRole(['landlord', 'caretaker']);
      if (auth.response) return auth.response;
      const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
      if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

      const body = await request.json();
      const { ticketId, status, fundiName, cost } = body;
  
      if (!ticketId || !status) {
        return NextResponse.json(
          { error: 'Ticket ID and status are required.' },
          { status: 400 }
        );
      }
  
      const validStatuses = ['pending', 'in_progress', 'resolved'];
      if (!validStatuses.includes(status)) {
        return NextResponse.json(
          { error: 'Invalid status. Must be pending, in_progress, or resolved.' },
          { status: 400 }
        );
      }

      const { data: ticket, error: ticketLookupError } = await supabaseAdmin.from('maintenance_tickets')
        .select('unit_id')
        .eq('id', ticketId)
        .maybeSingle();
      if (ticketLookupError || !ticket?.unit_id) return NextResponse.json({ error: 'Ticket not found.' }, { status: 404 });
      const { data: scopedUnit } = await supabaseAdmin.from('units').select('id').eq('id', ticket.unit_id).eq('property_id', scope.propertyId).maybeSingle();
      if (!scopedUnit) return NextResponse.json({ error: 'Ticket not found in the active property.' }, { status: 404 });
  
      const payload: any = {
        status,
        updated_at: new Date().toISOString(),
      };
  
      if (status === 'resolved') {
        if (fundiName) payload.fundi_name = fundiName;
        if (cost !== undefined) payload.cost = cost;
      }
  
      const { error: updateError } = await supabaseAdmin
        .from('maintenance_tickets')
        .update(payload)
        .eq('id', ticketId);

    if (updateError) {
      throw updateError;
    }

    return NextResponse.json({
      success: true,
      message: `Ticket status updated to ${status}.`,
    });
  } catch (error: any) {
    console.error('Failed to update maintenance ticket:', error);
    return NextResponse.json(
      { error: error.message || 'Failed to update ticket status.' },
      { status: 500 }
    );
  }
}
