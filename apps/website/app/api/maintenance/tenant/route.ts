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

export async function GET() {
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

    const { data: tickets, error: ticketError } = await supabaseAdmin
      .from('maintenance_tickets')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('created_at', { ascending: false });

    if (ticketError) {
      return NextResponse.json({ error: ticketError.message }, { status: 500 });
    }

    return NextResponse.json({
      tickets: (tickets || []).map((ticket) => ({
        id: ticket.id,
        ticketId: `TCK-${String(ticket.id).slice(-3).toUpperCase()}`,
        category: ticket.category,
        description: ticket.description,
        priority: ticket.priority,
        status: ticket.status,
        createdAt: ticket.created_at,
        updatedAt: ticket.updated_at,
      })),
    });
  } catch (error: any) {
    console.error('Failed to fetch maintenance tickets:', error);
    return NextResponse.json(
      { error: 'Failed to fetch maintenance tickets.' },
      { status: 500 }
    );
  }
}
