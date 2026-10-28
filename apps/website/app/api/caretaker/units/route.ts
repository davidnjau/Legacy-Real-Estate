import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { isWaterReminderDue } from '../../../../lib/billing-schedule';
import { getActivePropertyId } from '../../../../lib/property-access';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

// GET /api/caretaker/units — room statuses for caretaker dashboard
export async function GET() {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;
    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const { data: units, error } = await supabaseAdmin
      .from('units')
      .select('id, unit_code, status, floor_number, rent_amount')
      .eq('property_id', scope.propertyId)
      .order('unit_code', { ascending: true });

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    const enriched = [];
    for (const unit of units || []) {
      let tenantName = null;
      let baselineNeeded = false;
      if (unit.status === 'occupied' || unit.status === 'notice_given') {
        const { data: assignment } = await supabaseAdmin
          .from('unit_assignments')
          .select('tenant_id')
          .eq('unit_id', unit.id)
          .in('status', ['active', 'notice_given'])
          .limit(1)
          .maybeSingle();

        if (assignment?.tenant_id) {
          const { data: user } = await supabaseAdmin
            .from('users')
            .select('full_name')
            .eq('id', assignment.tenant_id)
            .single();
          tenantName = user?.full_name || null;
        }

        // Check specifically for a baseline reading (not just any reading)
        const { count: baselineCount } = await supabaseAdmin
          .from('water_readings')
          .select('id', { count: 'exact', head: true })
          .eq('unit_id', unit.id)
          .eq('reading_type', 'baseline');
        baselineNeeded = !baselineCount;
      }

      enriched.push({
        id: unit.id,
        unitCode: unit.unit_code,
        status: unit.status,
        floor: unit.floor_number,
        rentAmount: unit.rent_amount,
        tenantName,
        baselineNeeded,
      });
    }

    // Check if water readings have already been published for the current billing cycle
    const currentMonth = new Date().toISOString().slice(0, 7);
    const unitIds = (units || []).map((unit) => unit.id);
    const { count: publishedReadingsCount } = unitIds.length ? await supabaseAdmin
      .from('water_readings')
      .select('id', { count: 'exact', head: true })
      .in('unit_id', unitIds)
      .eq('reading_type', 'monthly')
      .eq('billing_month', currentMonth)
      .eq('status', 'published') : { count: 0 };

    return NextResponse.json({
      units: enriched,
      waterReadingReminderDue: isWaterReminderDue(),
      waterReadingsPublished: (publishedReadingsCount || 0) > 0,
    });
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
