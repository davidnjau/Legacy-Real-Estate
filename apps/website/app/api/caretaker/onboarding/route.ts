import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';

const admin = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.SUPABASE_SERVICE_ROLE_KEY!, { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } });

export async function GET() {
  const auth = await requireApiRole(['caretaker']);
  if (auth.response) return auth.response;
  const { data, error } = await admin.from('users').select('full_name, tutorial_completed').eq('id', auth.currentUser!.user.id).maybeSingle();
  if (error || !data) return NextResponse.json({ error: error?.message || 'Profile not found' }, { status: 500 });
  return NextResponse.json({ fullName: data.full_name || '', tutorialCompleted: Boolean(data.tutorial_completed) });
}

export async function POST(request: Request) {
  const auth = await requireApiRole(['caretaker']);
  if (auth.response) return auth.response;
  const body = await request.json().catch(() => ({}));
  const { error } = await admin.from('users').update({ tutorial_completed: body.tutorialCompleted !== false, updated_at: new Date().toISOString() }).eq('id', auth.currentUser!.user.id);
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json({ success: true });
}
