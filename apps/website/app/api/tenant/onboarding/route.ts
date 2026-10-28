import { createClient as createSupabaseClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';

const admin = createSupabaseClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

export async function GET() {
  const auth = await requireApiRole(['tenant']);
  if (auth.response) return auth.response;
  
  const tenantId = auth.currentUser!.user.id;

  const { data, error } = await admin
    .from('users')
    .select('full_name, tutorial_completed')
    .eq('id', tenantId)
    .maybeSingle();

  if (error || !data) return NextResponse.json({ error: error?.message || 'Profile not found' }, { status: 500 });

  // Resolve the tenant's active property, then read the property-specific billing settings.
  const { data: assignment } = await admin
    .from('unit_assignments')
    .select('unit_id, units!inner(property_id)')
    .eq('tenant_id', tenantId)
    .eq('status', 'active')
    .limit(1)
    .maybeSingle();

  const propertyId = Array.isArray(assignment?.units)
    ? assignment.units[0]?.property_id || null
    : (assignment?.units as any)?.property_id || null;
  const { data: billingSettings } = propertyId
    ? await admin
        .from('property_billing_settings')
        .select('first_online_billing_month')
        .eq('property_id', propertyId)
        .maybeSingle()
    : { data: null };

  // Get move in settlement
  const { data: moveInSettlement } = await admin
    .from('move_in_settlements')
    .select('status, total_required, total_paid, outstanding_balance, deposit_amount, rent_amount, garbage_amount, water_deposit_amount')
    .eq('tenant_id', tenantId)
    .order('created_at', { ascending: false })
    .limit(1)
    .maybeSingle();

  // Check if tenant is migrated
  let isMigrated = false;
  const { data: assignmentState } = await admin
    .from('unit_assignments')
    .select('units(is_migrated)')
    .eq('tenant_id', tenantId)
    .eq('status', 'active')
    .limit(1)
    .maybeSingle();
  
  if (assignmentState?.units && !Array.isArray(assignmentState.units) && (assignmentState.units as any).is_migrated) {
    isMigrated = true;
  }

  return NextResponse.json({
    fullName: data.full_name || '',
    tutorialCompleted: Boolean(data.tutorial_completed),
    isMigrated,
    moveInSettlement,
    transitionSettings: billingSettings
  });
}

export async function POST(request: Request) {
  const auth = await requireApiRole(['tenant']);
  if (auth.response) return auth.response;
  
  const body = await request.json().catch(() => ({}));
  
  const { error } = await admin
    .from('users')
    .update({ tutorial_completed: body.tutorialCompleted !== false, updated_at: new Date().toISOString() })
    .eq('id', auth.currentUser!.user.id);
    
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json({ success: true });
}
