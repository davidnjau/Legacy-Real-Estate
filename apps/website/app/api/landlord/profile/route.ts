import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { normalizeKenyanPhoneForStorage } from '../../../../lib/phone';

const admin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

export async function GET() {
  const auth = await requireApiRole(['landlord']);
  if (auth.response) return auth.response;
  const { data, error } = await admin.from('users')
    .select('full_name, email, phone')
    .eq('id', auth.currentUser!.user.id)
    .maybeSingle();
  if (error || !data) return NextResponse.json({ error: error?.message || 'Profile not found.' }, { status: 404 });
  return NextResponse.json({ profile: data });
}

export async function PATCH(request: Request) {
  const auth = await requireApiRole(['landlord']);
  if (auth.response) return auth.response;
  const body = await request.json();
  const fullName = String(body.fullName || '').trim();
  const phone = normalizeKenyanPhoneForStorage(String(body.phone || ''));
  if (!fullName || fullName.length > 120 || !phone) {
    return NextResponse.json({ error: 'Enter a name of 120 characters or fewer and a valid Kenyan phone number.' }, { status: 400 });
  }

  const { data, error } = await admin.from('users')
    .update({ full_name: fullName, phone, updated_at: new Date().toISOString() })
    .eq('id', auth.currentUser!.user.id)
    .select('full_name, email, phone')
    .single();
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json({ profile: data });
}