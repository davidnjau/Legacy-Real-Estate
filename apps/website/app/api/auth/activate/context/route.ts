import { createClient as createSupabaseAdmin } from '@supabase/supabase-js';
import { createServerClient } from '@supabase/ssr';
import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';

const supabaseAdmin = createSupabaseAdmin(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

export async function GET(request: Request) {
  const authorization = request.headers.get('authorization');
  if (authorization?.startsWith('Bearer ')) {
    const token = authorization.slice('Bearer '.length);
    const { data: { user } } = await supabaseAdmin.auth.getUser(token);

    if (!user) return NextResponse.json({ error: 'Activation session expired.' }, { status: 401 });
    return getTenantContext(request, user.id, user.email || '');
  }

  const cookieStore = await cookies();
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    { cookies: { getAll: () => cookieStore.getAll(), setAll() {} } }
  );

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: 'Activation session expired.' }, { status: 401 });

  return getTenantContext(request, user.id, user.email || '');
}

async function getTenantContext(request: Request, userId: string, userEmail: string) {

  const claimId = new URL(request.url).searchParams.get('claimId');
  const { data: profile } = await supabaseAdmin
    .from('users')
    .select('id, full_name, email, role, status')
    .eq('id', userId)
    .eq('role', 'tenant')
    .maybeSingle();

  if (!profile) {
    const { data: emailProfile } = userEmail
      ? await supabaseAdmin
        .from('users')
        .select('id, full_name, email, role, status')
        .ilike('email', userEmail)
        .eq('role', 'tenant')
        .maybeSingle()
      : { data: null };

    console.error('[tenant-activation] Auth/profile mismatch', {
      authUserId: userId,
      authEmail: userEmail,
      profileIdByEmail: emailProfile?.id || null,
    });

    return NextResponse.json({
      error: emailProfile
        ? 'Your Auth account is linked to a different tenant profile ID. Contact the landlord to repair the account link.'
        : 'No tenant profile exists for this email address. The landlord must recreate or repair the tenant profile.',
    }, { status: 404 });
  }

  const { data: assignment, error: assignmentError } = await supabaseAdmin
    .from('unit_assignments')
    .select('unit_id, units(unit_code)')
    .eq('tenant_id', userId)
    .eq('status', 'active')
    .order('move_in_date', { ascending: false })
    .limit(1)
    .maybeSingle();
  if (assignmentError) return NextResponse.json({ error: assignmentError.message }, { status: 500 });
  if (!assignment) return NextResponse.json({ error: 'No active tenancy is linked to this account. Contact the property manager.' }, { status: 404 });

  if (claimId) {
    const { data: claim } = await supabaseAdmin
      .from('claim_codes')
      .select('id, unit_id, expires_at, used_at')
      .eq('id', claimId)
      .maybeSingle();
    const { data: assignment } = claim
      ? await supabaseAdmin.from('unit_assignments').select('tenant_id').eq('unit_id', claim.unit_id).eq('tenant_id', userId).eq('status', 'active').maybeSingle()
      : { data: null };

    if (!claim || !assignment || claim.used_at || new Date(claim.expires_at) < new Date()) {
      return NextResponse.json({ error: 'This activation link is invalid or expired.' }, { status: 400 });
    }
  } else {
    const { data: settlement, error: settlementError } = await supabaseAdmin
      .from('move_in_settlements')
      .select('id, status')
      .eq('tenant_id', userId)
      .eq('unit_id', assignment.unit_id)
      .limit(1)
      .maybeSingle();
    if (settlementError) return NextResponse.json({ error: settlementError.message }, { status: 500 });
    if (settlement && settlement.status !== 'paid') {
      return NextResponse.json({ error: 'The move-in balance must be cleared before this account can be activated.' }, { status: 403 });
    }
    if (!settlement && profile.status !== 'dormant') {
      return NextResponse.json({ error: 'This tenant account is already active or is not eligible for activation.' }, { status: 403 });
    }
  }

  const unit = Array.isArray(assignment.units) ? assignment.units[0] : assignment.units;
  return NextResponse.json({
    tenantId: profile.id,
    fullName: profile.full_name,
    email: profile.email || userEmail,
    unitCode: unit?.unit_code || '',
  });
}
