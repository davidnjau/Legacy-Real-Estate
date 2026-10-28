// app/api/units/my-status/route.ts
import { createClient as createSupabaseAdmin } from '@supabase/supabase-js';
import { createServerClient } from '@supabase/ssr';
import { NextResponse } from 'next/server';
import { cookies } from 'next/headers';

const supabaseAdmin = createSupabaseAdmin(
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

/**
 * GET /api/units/my-status
 *
 * Returns the tenant's current unit status. Used by the tenant layout to
 * detect offboarding and render the appropriate lock-down screen.
 */
export async function GET() {
  try {
    const cookieStore = await cookies();
    const supabase = createServerClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
      {
        cookies: {
          getAll() { return cookieStore.getAll(); },
          setAll() {},
        },
      }
    );

    const { data: { user } } = await supabase.auth.getUser();
    if (!user) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    // Find the active unit assignment for this tenant
    const { data: assignment } = await supabaseAdmin
      .from('unit_assignments')
      .select('unit_id, status, move_in_date')
      .eq('tenant_id', user.id)
      .order('created_at', { ascending: false })
      .limit(1)
      .maybeSingle();

    if (!assignment) {
      return NextResponse.json({ status: 'no_unit', unitCode: null });
    }

    // Get the actual unit status
    const { data: unit } = await supabaseAdmin
      .from('units')
      .select('unit_code, status')
      .eq('id', assignment.unit_id)
      .single();

    return NextResponse.json({
      status: unit?.status || 'unknown',
      unitCode: unit?.unit_code || null,
      assignmentStatus: assignment.status,
      moveInDate: assignment.move_in_date,
    });
  } catch (error: any) {
    console.error('my-status fetch failed:', error);
    return NextResponse.json({ error: 'Failed to fetch unit status.' }, { status: 500 });
  }
}
