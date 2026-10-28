import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { unitCode, code } = body;

    if (!unitCode || !code) {
      return NextResponse.json({ error: 'Unit Code and Activation Code are required.' }, { status: 400 });
    }

    // 1. Verify Unit Code
    const { data: unit } = await supabaseAdmin
      .from('units')
      .select('id, unit_code')
      .ilike('unit_code', unitCode.trim())
      .single();

    if (!unit) {
      return NextResponse.json({ error: 'Invalid Unit Code.' }, { status: 400 });
    }

    // 2. Verify Claim Code
    const { data: claimCode } = await supabaseAdmin
      .from('claim_codes')
      .select('id, expires_at, used_at')
      .eq('code', code.trim().toUpperCase())
      .eq('unit_id', unit.id)
      .single();

    if (!claimCode) {
      return NextResponse.json({ error: 'Invalid Activation Code.' }, { status: 400 });
    }
    if (claimCode.used_at) {
      return NextResponse.json({ error: 'This activation code has already been used.' }, { status: 400 });
    }
    if (new Date(claimCode.expires_at) < new Date()) {
      return NextResponse.json({ error: 'This activation code has expired. Please see the landlord for a new one.' }, { status: 400 });
    }

    // 3. Get Tenant Info
    const { data: assignment } = await supabaseAdmin
      .from('unit_assignments')
      .select('tenant_id, users(full_name, email, status)')
      .eq('unit_id', unit.id)
      .eq('status', 'active')
      .limit(1)
      .single();

    if (!assignment || !assignment.users) {
      return NextResponse.json({ error: 'No active tenant found for this unit.' }, { status: 400 });
    }

    const userData = Array.isArray(assignment.users) ? assignment.users[0] : assignment.users;

    if (!userData || userData.status !== 'dormant') {
      return NextResponse.json({ error: 'This account has already been activated. Please log in normally.' }, { status: 400 });
    }

    return NextResponse.json({
      success: true,
      claimId: claimCode.id,
      tenantId: assignment.tenant_id,
      fullName: userData.full_name,
      email: userData.email,
    });

  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
