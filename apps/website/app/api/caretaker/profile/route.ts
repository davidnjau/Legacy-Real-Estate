import { createClient as createSupabaseAdmin } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';

const supabaseAdmin = createSupabaseAdmin(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

export async function GET() {
  const auth = await requireApiRole(['caretaker']);
  if (auth.response) return auth.response;
  const userId = auth.currentUser!.user.id;
  const { data, error } = await supabaseAdmin.from('users').select('full_name, email, phone, role, status, created_at').eq('id', userId).single();
  if (error || !data) return NextResponse.json({ error: 'Unable to load your profile.' }, { status: 500 });
  return NextResponse.json({ profile: data });
}

export async function PATCH(request: Request) {
  const auth = await requireApiRole(['caretaker']);
  if (auth.response) return auth.response;
  const body = await request.json();
  const fullName = String(body.fullName || '').trim();
  const email = String(body.email || '').trim().toLowerCase();
  const phone = String(body.phone || '').trim();
  if (!fullName || !email || !phone) return NextResponse.json({ error: 'Name, email, and phone are required.' }, { status: 400 });

  const userId = auth.currentUser!.user.id;
  const { error: authError } = await supabaseAdmin.auth.admin.updateUserById(userId, { email, user_metadata: { full_name: fullName, phone } });
  if (authError) return NextResponse.json({ error: authError.message }, { status: 400 });
  const { error } = await supabaseAdmin.from('users').update({ full_name: fullName, email, phone, updated_at: new Date().toISOString() }).eq('id', userId);
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json({ success: true, message: 'Profile updated successfully.' });
}
