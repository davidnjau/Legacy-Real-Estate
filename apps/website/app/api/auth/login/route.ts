import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
  }
);

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const email = String(body.email || '').trim().toLowerCase();
    const password = String(body.password || '');

    if (!email || !password) {
      return NextResponse.json(
        { error: 'Email and password are required.' },
        { status: 400 }
      );
    }

    const { data: sessionData, error: signInError } = await supabaseAdmin.auth.signInWithPassword({
      email,
      password,
    });

    if (signInError || !sessionData.session || !sessionData.user) {
      return NextResponse.json(
        { error: signInError?.message || 'Invalid login credentials.' },
        { status: 401 }
      );
    }

    const { data: profile, error: profileError } = await supabaseAdmin
      .from('users')
      .select('role, full_name, email, status')
      .eq('id', sessionData.user.id)
      .maybeSingle();

    if (profileError) {
      console.error('Profile fetch error:', profileError);
      return NextResponse.json(
        { error: 'Failed to fetch your profile. Please try again or contact support.' },
        { status: 500 }
      );
    }

    if (!profile) {
      console.error('No profile found for user:', sessionData.user.id);
      return NextResponse.json(
        { error: 'Your account profile could not be found in our system. Please contact the landlord.' },
        { status: 404 }
      );
    }

    return NextResponse.json({
      success: true,
      user: {
        id: sessionData.user.id,
        email: sessionData.user.email,
        role: profile.role,
        fullName: profile.full_name,
      },
      session: {
        access_token: sessionData.session.access_token,
        refresh_token: sessionData.session.refresh_token,
      },
    });
  } catch (error: any) {
    console.error('Login failed:', error);
    return NextResponse.json(
      { error: error?.message || 'An unexpected error occurred during login.' },
      { status: 500 }
    );
  }
}
