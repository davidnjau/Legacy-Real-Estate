import { createClient as createSupabaseClient } from '@supabase/supabase-js';
import { createServerClient } from '@supabase/ssr';
import { NextResponse } from 'next/server';
import { cookies } from 'next/headers';

const supabaseAdmin = createSupabaseClient(
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
    const cookieStore = await cookies();

    const supabase = createServerClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
      {
        cookies: {
          getAll() {
            return cookieStore.getAll();
          },
          setAll() {
            // No-op in read-only server route.
          },
        },
      }
    );

    const {
      data: { user },
      error: userError,
    } = await supabase.auth.getUser();

    if (userError || !user) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const tenantId = user.id;
    const body = await request.json();
    const { category, description, priority } = body;

    if (!category || !description) {
      return NextResponse.json(
        { error: 'Category and description are required.' },
        { status: 400 }
      );
    }

    // Get tenant's current unit
    const { data: assignment, error: assignmentError } = await supabaseAdmin
      .from('unit_assignments')
      .select('unit_id')
      .eq('tenant_id', tenantId)
      .eq('status', 'active')
      .maybeSingle();

    if (assignmentError || !assignment) {
      return NextResponse.json(
        { error: 'Unable to find your assigned unit.' },
        { status: 400 }
      );
    }

    // Create the maintenance ticket
    const { data: ticket, error: ticketError } = await supabaseAdmin
      .from('maintenance_tickets')
      .insert({
        unit_id: assignment.unit_id,
        tenant_id: tenantId,
        category,
        description,
        priority: priority || 'normal',
        status: 'pending',
        created_at: new Date().toISOString(),
      })
      .select()
      .single();

    if (ticketError) {
      throw ticketError;
    }

    return NextResponse.json({
      success: true,
      message: 'Maintenance request submitted successfully.',
      ticket,
    });
  } catch (error: any) {
    console.error('Failed to create maintenance ticket:', error);
    return NextResponse.json(
      { error: error.message || 'Failed to submit maintenance request.' },
      { status: 500 }
    );
  }
}
