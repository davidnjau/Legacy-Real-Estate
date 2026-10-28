import { createClient } from '@supabase/supabase-js';
import { createServerClient } from '@supabase/ssr';
import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

export async function POST(request: Request) {
  const authorization = request.headers.get('authorization');
  let authenticatedUser: { id: string } | null = null;

  if (authorization?.startsWith('Bearer ')) {
    const token = authorization.slice('Bearer '.length);
    const { data: { user } } = await supabaseAdmin.auth.getUser(token);
    authenticatedUser = user ? { id: user.id } : null;
  }

  const cookieStore = await cookies();
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll: () => cookieStore.getAll(),
        setAll() {},
      },
    }
  );

  const { data: { user } } = await supabase.auth.getUser();
  const effectiveUser = authenticatedUser || user;
  if (!effectiveUser) return NextResponse.json({ error: 'Activation session expired. Request a new email.' }, { status: 401 });

  const body = await request.json();
  const claimId = String(body.claimId || '').trim();
  const fullName = String(body.fullName || '').trim();
  if (!fullName) return NextResponse.json({ error: 'Your full name is required.' }, { status: 400 });

  const { data: profile, error: profileLookupError } = await supabaseAdmin
    .from('users')
    .select('id, email, status')
    .eq('id', effectiveUser.id)
    .eq('role', 'tenant')
    .maybeSingle();
  if (profileLookupError) return NextResponse.json({ error: profileLookupError.message }, { status: 500 });
  if (!profile) return NextResponse.json({ error: 'Tenant account not found.' }, { status: 404 });

  const { data: assignment, error: assignmentError } = await supabaseAdmin
    .from('unit_assignments')
    .select('unit_id')
    .eq('tenant_id', effectiveUser.id)
    .eq('status', 'active')
    .order('move_in_date', { ascending: false })
    .limit(1)
    .maybeSingle();
  if (assignmentError) return NextResponse.json({ error: assignmentError.message }, { status: 500 });
  if (!assignment) return NextResponse.json({ error: 'No active tenancy is linked to this account.' }, { status: 403 });

  let settlement: { id: string; expected_move_in_date: string | null } | null = null;
  if (claimId) {
    const { data: claim, error: claimError } = await supabaseAdmin.from('claim_codes')
      .select('id, unit_id, expires_at, used_at')
      .eq('id', claimId)
      .maybeSingle();
    if (claimError) return NextResponse.json({ error: claimError.message }, { status: 500 });
    if (!claim || claim.unit_id !== assignment.unit_id || claim.used_at || new Date(claim.expires_at) < new Date()) {
      return NextResponse.json({ error: 'This activation link is invalid or expired.' }, { status: 400 });
    }
  } else {
    const { data, error: settlementError } = await supabaseAdmin
      .from('move_in_settlements')
      .select('id, status, expected_move_in_date')
      .eq('tenant_id', effectiveUser.id)
      .eq('unit_id', assignment.unit_id)
      .limit(1)
      .maybeSingle();
    if (settlementError) return NextResponse.json({ error: settlementError.message }, { status: 500 });
    if (data) {
      if (data.status !== 'paid') return NextResponse.json({ error: 'The move-in balance must be cleared before activation.' }, { status: 403 });
      settlement = data;
    } else if (profile.status !== 'dormant') {
      return NextResponse.json({ error: 'This tenant account is not eligible for activation.' }, { status: 403 });
    }
  }

  const { error: profileError } = await supabaseAdmin
    .from('users')
    .update({ status: 'active', full_name: fullName, updated_at: new Date().toISOString() })
    .eq('id', effectiveUser.id)
    .eq('role', 'tenant');

  if (profileError) return NextResponse.json({ error: profileError.message }, { status: 500 });

  if (claimId) {
    const { data: claim } = await supabaseAdmin
      .from('claim_codes')
      .select('id, expires_at, used_at')
      .eq('id', claimId)
      .maybeSingle();

    if (claim && !claim.used_at && new Date(claim.expires_at) >= new Date()) {
      await supabaseAdmin
        .from('claim_codes')
        .update({ used_by_user_id: effectiveUser.id, used_at: new Date().toISOString() })
        .eq('id', claim.id);
    }
  }

  if (settlement) {
    const nariobiDate = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Africa/Nairobi',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(new Date());
    const unitStatus = settlement.expected_move_in_date && settlement.expected_move_in_date > nariobiDate
      ? 'booked'
      : 'occupied';
    const { error: settlementUpdateError } = await supabaseAdmin.from('move_in_settlements')
      .update({ status: 'active', activated_at: new Date().toISOString(), updated_at: new Date().toISOString() })
      .eq('id', settlement.id);
    if (settlementUpdateError) return NextResponse.json({ error: settlementUpdateError.message }, { status: 500 });

    const { error: unitUpdateError } = await supabaseAdmin.from('units')
      .update({ status: unitStatus, updated_at: new Date().toISOString() })
      .eq('id', assignment.unit_id)
      .eq('status', 'booked');
    if (unitUpdateError) return NextResponse.json({ error: unitUpdateError.message }, { status: 500 });
  }

  return NextResponse.json({ success: true });
}