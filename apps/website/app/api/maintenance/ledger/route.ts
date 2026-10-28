import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { getActivePropertyId, getPropertyUnitIds } from '../../../../lib/property-access';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  }
);

export async function GET(request: Request) {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;
    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
    const { unitIds, error: unitsError } = await getPropertyUnitIds(supabaseAdmin, scope.propertyId);
    if (unitsError) return NextResponse.json({ error: unitsError }, { status: 500 });

    const { searchParams } = new URL(request.url);
    const requestedMonth = searchParams.get('month');
    
    // Default to current month if not specified
    const month = requestedMonth || new Date().toISOString().slice(0, 7);

    // Parse out year/month to construct date boundaries
    const [yearStr, monthStr] = month.split('-');
    const year = parseInt(yearStr, 10);
    const monthIdx = parseInt(monthStr, 10) - 1; // 0-indexed

    const startOfMonth = new Date(Date.UTC(year, monthIdx, 1)).toISOString();
    const endOfMonth = new Date(Date.UTC(year, monthIdx + 1, 0, 23, 59, 59, 999)).toISOString();

    const { data: tickets, error } = unitIds?.length ? await supabaseAdmin
      .from('maintenance_tickets')
      .select('id, category, description, fundi_name, cost, updated_at, unit_id, tenant_id')
      .in('unit_id', unitIds)
      .eq('status', 'resolved')
      .gte('updated_at', startOfMonth)
      .lte('updated_at', endOfMonth)
      .order('updated_at', { ascending: false })
      : { data: [], error: null };

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    let totalSpend = 0;

    const ledgerRows = [];

    for (const ticket of tickets || []) {
      const cost = Number(ticket.cost || 0);
      totalSpend += cost;

      let unitCode = '-';
      let tenantName = 'Unassigned';

      if (ticket.unit_id) {
        const { data: unit } = await supabaseAdmin.from('units').select('unit_code').eq('id', ticket.unit_id).single();
        if (unit) unitCode = unit.unit_code;
      }
      if (ticket.tenant_id) {
        const { data: user } = await supabaseAdmin.from('users').select('full_name').eq('id', ticket.tenant_id).single();
        if (user) tenantName = user.full_name;
      }

      ledgerRows.push({
        id: ticket.id,
        ticketId: `TCK-${String(ticket.id).slice(-3).toUpperCase()}`,
        unitCode,
        tenantName,
        category: ticket.category,
        description: ticket.description,
        fundiName: ticket.fundi_name || 'N/A',
        cost,
        resolvedAt: ticket.updated_at,
      });
    }

    return NextResponse.json({
      month,
      summary: {
        totalSpend,
        totalRepairs: ledgerRows.length,
      },
      ledger: ledgerRows,
    });
  } catch (error: any) {
    console.error('Failed to fetch maintenance ledger:', error);
    return NextResponse.json({ error: 'Failed to fetch maintenance ledger data.' }, { status: 500 });
  }
}
