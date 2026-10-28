import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { getActivePropertyId } from '../../../../lib/property-access';

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

export async function POST(request: Request) {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;

    const body = await request.json();
    const unitId = String(body.unitId || '').trim();
    const status = String(body.status || '').trim();

    if (!unitId || !status) {
      return NextResponse.json({ error: 'Unit ID and status are required.' }, { status: 400 });
    }

    const allowedStatuses = ['vacant', 'occupied', 'offboarding', 'maintenance'];

    if (!allowedStatuses.includes(status)) {
      return NextResponse.json({ error: 'Invalid unit status value.' }, { status: 400 });
    }

    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const { data: updatedUnit, error: updateError } = await supabaseAdmin
      .from('units')
      .update({ status, updated_at: new Date().toISOString() })
      .eq('id', unitId)
      .eq('property_id', scope.propertyId)
      .select()
      .single();

    if (updateError || !updatedUnit) {
      return NextResponse.json({ error: 'Failed to update unit occupancy status.' }, { status: 500 });
    }

    return NextResponse.json({ success: true, unit: updatedUnit });
  } catch (error: any) {
    console.error('Occupancy update failed:', error);
    return NextResponse.json({ error: 'Failed to update occupancy.' }, { status: 500 });
  }
}
