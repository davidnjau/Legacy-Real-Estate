import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

// POST /api/auth/register-caretaker
export async function POST(request: Request) {
  try {
    const body = await request.json();
    const fullName = String(body.fullName || '').trim();
    const email = String(body.email || '').trim().toLowerCase();
    const phone = String(body.phone || '').trim();
    const inviteCode = String(body.inviteCode || '').trim().toUpperCase();
    const password = String(body.password || '');

    if (!fullName || !email || !phone || !inviteCode || !password) {
      return NextResponse.json({ error: 'All fields are required.' }, { status: 400 });
    }

    if (!/^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z\d]).{8,}$/.test(password)) {
      return NextResponse.json({ error: 'Password must be at least 8 characters and include uppercase, lowercase, number, and special character.' }, { status: 400 });
    }

    // Validate invite code
    const { data: invite, error: inviteError } = await supabaseAdmin
      .from('caretaker_invites')
      .select('*')
      .eq('code', inviteCode)
      .eq('status', 'active')
      .maybeSingle();

    if (inviteError || !invite) {
      return NextResponse.json({ error: 'Invalid or expired invite code.' }, { status: 400 });
    }

    if (new Date(invite.expires_at) < new Date()) {
      await supabaseAdmin.from('caretaker_invites').update({ status: 'expired' }).eq('id', invite.id);
      return NextResponse.json({ error: 'This invite code has expired (valid for 48 hours).' }, { status: 410 });
    }

    if (invite.used_by) {
      return NextResponse.json({ error: 'This invite code has already been used.' }, { status: 409 });
    }
    if (!invite.property_id) return NextResponse.json({ error: 'This invite is not linked to a property. Ask the landlord to issue a new invite.' }, { status: 409 });

    // Create auth account
    const { data: authUser, error: createAuthError } = await supabaseAdmin.auth.admin.createUser({
      email,
      password,
      email_confirm: true,
      user_metadata: { full_name: fullName, phone, role: 'caretaker' },
    });

    if (createAuthError || !authUser?.user) {
      return NextResponse.json({ error: createAuthError?.message || 'Unable to create caretaker account.' }, { status: 400 });
    }

    // Create user profile with role = 'caretaker'
    const { error: profileError } = await supabaseAdmin.from('users').insert({
      id: authUser.user.id,
      full_name: fullName,
      email,
      phone,
      role: 'caretaker',
      status: 'active',
      password_hash: null,
    });

    if (profileError) {
      await supabaseAdmin.auth.admin.deleteUser(authUser.user.id);
      return NextResponse.json({ error: 'Failed to create caretaker profile.' }, { status: 500 });
    }

    const { error: membershipError } = await supabaseAdmin.from('property_members').insert({
      property_id: invite.property_id,
      user_id: authUser.user.id,
      member_role: 'caretaker',
    });
    if (membershipError) {
      await supabaseAdmin.auth.admin.deleteUser(authUser.user.id);
      return NextResponse.json({ error: 'Failed to attach caretaker account to the invited property.' }, { status: 500 });
    }

    await supabaseAdmin.from('users').update({ active_property_id: invite.property_id }).eq('id', authUser.user.id);

    // Mark the invite code as used
    await supabaseAdmin.from('caretaker_invites').update({
      used_by: authUser.user.id,
      used_at: new Date().toISOString(),
      status: 'used',
    }).eq('id', invite.id);

    return NextResponse.json({
      success: true,
      message: 'Caretaker account created successfully.',
      user: { id: authUser.user.id, email, role: 'caretaker' },
    });
  } catch (error: any) {
    console.error('Caretaker registration failed:', error);
    return NextResponse.json({ error: error?.message || 'An unexpected error occurred.' }, { status: 500 });
  }
}
