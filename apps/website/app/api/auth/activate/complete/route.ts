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
    const { tenantId, claimId, password } = body;

    if (!tenantId || !claimId || !password) {
      return NextResponse.json({ error: 'Missing required fields.' }, { status: 400 });
    }

    if (!/^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z\d]).{8,}$/.test(password)) {
      return NextResponse.json({ error: 'Password must be at least 8 characters and include uppercase, lowercase, number, and special character.' }, { status: 400 });
    }

    // 1. Verify the code hasn't been used while they were typing
    const { data: claimCode } = await supabaseAdmin
      .from('claim_codes')
      .select('used_at, expires_at, unit_id')
      .eq('id', claimId)
      .single();

    if (!claimCode || claimCode.used_at || new Date(claimCode.expires_at) < new Date()) {
      return NextResponse.json({ error: 'This code is invalid or has already been used.' }, { status: 400 });
    }

    const { data: assignment } = await supabaseAdmin
      .from('unit_assignments')
      .select('tenant_id')
      .eq('unit_id', claimCode.unit_id)
      .eq('tenant_id', tenantId)
      .eq('status', 'active')
      .maybeSingle();

    if (!assignment) return NextResponse.json({ error: 'This activation code is not assigned to this tenant.' }, { status: 403 });

    // 2. Update Auth User Password
    const { error: updateAuthError } = await supabaseAdmin.auth.admin.updateUserById(tenantId, {
      password: password,
    });

    if (updateAuthError) {
      return NextResponse.json({ error: updateAuthError.message || 'Failed to set password.' }, { status: 400 });
    }

    // 3. Mark user as Active
    await supabaseAdmin
      .from('users')
      .update({ status: 'active' })
      .eq('id', tenantId);

    // 4. Mark code as used
    await supabaseAdmin
      .from('claim_codes')
      .update({
        used_by_user_id: tenantId,
        used_at: new Date().toISOString(),
      })
      .eq('id', claimId);

    return NextResponse.json({
      success: true,
      message: 'Account activated successfully. You can now log in.',
    });

  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
