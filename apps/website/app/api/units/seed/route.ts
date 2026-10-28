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

export async function POST() {
  try {
    const auth = await requireApiRole(['landlord']);
    if (auth.response) return auth.response;
    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
    const units = [
      { property_id: scope.propertyId, unit_code: 'A1', room_name: 'Unit A1', status: 'occupied', rent_amount: 12000, garbage_amount: 300, monthly_water_rate: 150, deposit_amount: 12000 },
      { property_id: scope.propertyId, unit_code: 'A2', room_name: 'Unit A2', status: 'vacant', rent_amount: 12000, garbage_amount: 300, monthly_water_rate: 150, deposit_amount: 12000 },
      { property_id: scope.propertyId, unit_code: 'B1', room_name: 'Unit B1', status: 'occupied', rent_amount: 15000, garbage_amount: 300, monthly_water_rate: 150, deposit_amount: 15000 },
      { property_id: scope.propertyId, unit_code: 'B2', room_name: 'Unit B2', status: 'vacant', rent_amount: 15000, garbage_amount: 300, monthly_water_rate: 150, deposit_amount: 15000 },
    ];

    const { error } = await supabaseAdmin.from('units').upsert(units, { onConflict: 'unit_code' });

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    return NextResponse.json({ success: true, message: 'Seed units created successfully.' });
  } catch (error: any) {
    console.error('Seed units failed:', error);
    return NextResponse.json({ error: 'Failed to seed units.' }, { status: 500 });
  }
}
