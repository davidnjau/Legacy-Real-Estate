import { createClient as createSupabaseAdmin } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';

const supabaseAdmin = createSupabaseAdmin(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

export async function GET() {
  const auth = await requireApiRole(['tenant']);
  if (auth.response) return auth.response;

  const tenantId = auth.currentUser!.user.id;
  const { data: profile, error: profileError } = await supabaseAdmin
    .from('users')
    .select('full_name, email, phone')
    .eq('id', tenantId)
    .single();

  const { data: assignment, error: assignmentError } = await supabaseAdmin
    .from('unit_assignments')
    .select('move_in_date, deposit_paid, units(unit_code, floor_number, deposit_amount)')
    .eq('tenant_id', tenantId)
    .eq('status', 'active')
    .order('created_at', { ascending: false })
    .limit(1)
    .maybeSingle();

  const { data: settlement } = await supabaseAdmin
    .from('move_in_settlements')
    .select('deposit_amount, water_deposit_amount')
    .eq('tenant_id', tenantId)
    .order('created_at', { ascending: false })
    .limit(1)
    .maybeSingle();

  if (profileError || assignmentError || !profile) {
    return NextResponse.json({ error: 'Unable to load your profile.' }, { status: 500 });
  }

  const unit = Array.isArray(assignment?.units) ? assignment.units[0] : assignment?.units;
  return NextResponse.json({
    profile: {
      fullName: profile.full_name,
      email: profile.email,
      phone: profile.phone,
      moveInDate: assignment?.move_in_date || null,
      securityDeposit: Number(settlement?.deposit_amount || unit?.deposit_amount || assignment?.deposit_paid || 0),
      waterDeposit: Number(settlement?.water_deposit_amount || 0),
      deposit: Number(settlement?.deposit_amount || unit?.deposit_amount || assignment?.deposit_paid || 0),
      unitCode: unit?.unit_code || null,
      floor: unit?.floor_number || null,
    },
  });
}

export async function PATCH(request: Request) {
  const auth = await requireApiRole(['tenant']);
  if (auth.response) return auth.response;

  const tenantId = auth.currentUser!.user.id;
  const body = await request.json();
  const fullName = String(body.fullName || '').trim();
  const email = String(body.email || '').trim().toLowerCase();
  const phone = String(body.phone || '').trim();

  if (!fullName || !email || !phone) {
    return NextResponse.json({ error: 'Name, email, and phone are required.' }, { status: 400 });
  }

  const { error: authError } = await supabaseAdmin.auth.admin.updateUserById(tenantId, {
    email,
    user_metadata: { full_name: fullName, phone },
  });

  if (authError) {
    return NextResponse.json({ error: authError.message }, { status: 400 });
  }

  const { error: profileError } = await supabaseAdmin
    .from('users')
    .update({ full_name: fullName, email, phone, updated_at: new Date().toISOString() })
    .eq('id', tenantId);

  if (profileError) {
    return NextResponse.json({ error: profileError.message }, { status: 500 });
  }

  return NextResponse.json({ success: true, message: 'Profile updated successfully.' });
}
