import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { normalizeKenyanPhoneForStorage } from '../../../../lib/phone';

const admin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

function validPassword(value: string) {
  return /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z\d]).{8,}$/.test(value);
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const fullName = String(body.fullName || '').trim();
    const email = String(body.email || '').trim().toLowerCase();
    const phone = normalizeKenyanPhoneForStorage(body.phone);
    const password = String(body.password || '');
    const propertyName = String(body.propertyName || '').trim();
    if (!fullName || !email || !phone || !propertyName || !password) {
      return NextResponse.json({ error: 'Name, email, phone, property name, and password are required.' }, { status: 400 });
    }

    if (!validPassword(password)) {
      return NextResponse.json({ error: 'Password must be at least 8 characters and include uppercase, lowercase, number, and special character.' }, { status: 400 });
    }

    const { data: existingProfile } = await admin.from('users').select('id').eq('email', email).maybeSingle();
    if (existingProfile) return NextResponse.json({ error: 'An account already exists for this email.' }, { status: 409 });

    const { data: authData, error: authError } = await admin.auth.admin.createUser({
      email,
      password,
      email_confirm: true,
      user_metadata: { full_name: fullName, phone, property_name: propertyName, role: 'landlord' },
    });
    if (authError || !authData.user) return NextResponse.json({ error: authError?.message || 'Unable to create landlord account.' }, { status: 400 });

    const { error: profileError } = await admin.from('users').insert({
      id: authData.user.id,
      full_name: fullName,
      email,
      phone,
      role: 'landlord',
      status: 'active',
      password_hash: null,
      onboarding_completed: false,
      tutorial_completed: false,
      property_name: propertyName,
    });

    if (profileError) {
      await admin.auth.admin.deleteUser(authData.user.id);
      return NextResponse.json({ error: `Failed to create landlord profile: ${profileError.message}` }, { status: 500 });
    }

    const { data: property, error: propertyError } = await admin.from('properties')
      .insert({ owner_id: authData.user.id, name: propertyName })
      .select('id, name')
      .single();
    if (propertyError || !property) {
      await admin.auth.admin.deleteUser(authData.user.id);
      return NextResponse.json({ error: propertyError?.message || 'Unable to create the initial property.' }, { status: 500 });
    }

    const { error: membershipError } = await admin.from('property_members').insert({
      property_id: property.id,
      user_id: authData.user.id,
      member_role: 'owner',
    });
    if (membershipError) {
      await admin.from('properties').delete().eq('id', property.id).eq('owner_id', authData.user.id);
      await admin.auth.admin.deleteUser(authData.user.id);
      return NextResponse.json({ error: membershipError.message }, { status: 500 });
    }

    const { error: activePropertyError } = await admin.from('users')
      .update({ active_property_id: property.id })
      .eq('id', authData.user.id);
    if (activePropertyError) {
      await admin.from('properties').delete().eq('id', property.id).eq('owner_id', authData.user.id);
      await admin.auth.admin.deleteUser(authData.user.id);
      return NextResponse.json({ error: activePropertyError.message }, { status: 500 });
    }

    return NextResponse.json({ success: true, user: { id: authData.user.id, email, role: 'landlord' }, property });
  } catch (error: any) {
    console.error('Landlord registration failed:', error);
    return NextResponse.json({ error: error.message || 'Unable to create landlord account.' }, { status: 500 });
  }
}
