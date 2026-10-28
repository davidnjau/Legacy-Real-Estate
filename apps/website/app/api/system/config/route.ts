import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';

// We use the admin client because this route needs to read landlord data
// without requiring a user to be signed in.
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
    // Check if any landlord exists
    const { data: landlord, error: userError } = await supabaseAdmin
      .from('users')
      .select('id, property_name, active_property_id')
      .eq('role', 'landlord')
      .limit(1)
      .maybeSingle();

    if (userError) throw userError;

    if (!landlord) {
      return NextResponse.json({
        isSetup: false,
        propertyName: 'Rental Management System',
        transitionMonthEnded: false,
      });
    }

    // Check the landlord's active property billing settings to see if the transition month ended.
    const { data: billingSettings } = landlord?.active_property_id
      ? await supabaseAdmin
          .from('property_billing_settings')
          .select('first_online_billing_month')
          .eq('property_id', landlord.active_property_id)
          .maybeSingle()
      : { data: null };

    let transitionMonthEnded = false;
    if (billingSettings?.first_online_billing_month) {
      const now = new Date();
      const currentMonth = now.toISOString().slice(0, 7);
      if (currentMonth >= billingSettings.first_online_billing_month) {
        transitionMonthEnded = true;
      }
    }

    return NextResponse.json({
      isSetup: true,
      propertyName: landlord.property_name || 'Rental Management System',
      transitionMonthEnded,
    });
  } catch (error: any) {
    console.error('System config fetch failed:', error);
    return NextResponse.json({ error: 'Failed to load system configuration' }, { status: 500 });
  }
}
