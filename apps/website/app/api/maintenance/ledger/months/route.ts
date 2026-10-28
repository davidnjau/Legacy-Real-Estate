import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../../lib/api-auth';
import { getActivePropertyId, getPropertyUnitIds } from '../../../../../lib/property-access';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
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

    const { data, error } = unitIds?.length ? await supabaseAdmin
      .from('maintenance_tickets')
      .select('updated_at')
      .in('unit_id', unitIds)
      .eq('status', 'resolved')
      .order('updated_at', { ascending: false })
      : { data: [], error: null };

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    const uniqueMonths = [...new Set((data || []).map(row => {
      // row.updated_at is ISO string e.g. 2026-09-03T12:00:00Z
      return row.updated_at ? row.updated_at.slice(0, 7) : null;
    }).filter(Boolean))];

    if (uniqueMonths.length === 0) {
      uniqueMonths.push(new Date().toISOString().slice(0, 7));
    }

    return NextResponse.json({ months: uniqueMonths });
  } catch (error: any) {
    console.error('Failed to fetch maintenance ledger months:', error);
    return NextResponse.json({ error: 'Failed to fetch available months.' }, { status: 500 });
  }
}
