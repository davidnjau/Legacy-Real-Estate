// app/api/maintenance/comment/route.ts
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
 * POST /api/maintenance/comment
 * Body: { ticketId: string, message: string }
 *
 * Adds a comment/reply to a maintenance ticket from the authenticated user.
 * The `sender_role` is determined by the user's profile role.
 */
export async function POST(request: Request) {
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

    const body = await request.json();
    const ticketId = String(body.ticketId || '').trim();
    const message = String(body.message || '').trim();

    if (!ticketId || !message) {
      return NextResponse.json({ error: 'Ticket ID and message are required.' }, { status: 400 });
    }

    // Get user role from profile
    const { data: profile } = await supabaseAdmin
      .from('users')
      .select('role, full_name')
      .eq('id', user.id)
      .maybeSingle();

    const role = profile?.role || 'tenant';
    const senderName = profile?.full_name || 'Unknown';

    // Validate ticket exists
    const { data: ticket } = await supabaseAdmin
      .from('maintenance_tickets')
      .select('id, status')
      .eq('id', ticketId)
      .maybeSingle();

    if (!ticket) {
      return NextResponse.json({ error: 'Ticket not found.' }, { status: 404 });
    }

    // Insert comment
    const { data: comment, error: insertError } = await supabaseAdmin
      .from('maintenance_comments')
      .insert({
        ticket_id: ticketId,
        sender_id: user.id,
        sender_role: role,
        sender_name: senderName,
        message,
        created_at: new Date().toISOString(),
      })
      .select()
      .single();

    if (insertError) {
      console.error('Comment insert error:', insertError);
      return NextResponse.json({ error: insertError.message }, { status: 500 });
    }

    // If landlord/caretaker is commenting, auto-move ticket to in_progress if still pending
    if ((role === 'landlord' || role === 'caretaker') && ticket.status === 'pending') {
      await supabaseAdmin
        .from('maintenance_tickets')
        .update({ status: 'in_progress', updated_at: new Date().toISOString() })
        .eq('id', ticketId);
    }

    return NextResponse.json({ success: true, comment });
  } catch (error: any) {
    console.error('Comment POST failed:', error);
    return NextResponse.json({ error: error.message || 'Failed to post comment.' }, { status: 500 });
  }
}

/**
 * GET /api/maintenance/comment?ticketId=xxx
 *
 * Returns all comments for the specified ticket.
 */
export async function GET(request: Request) {
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

    const { searchParams } = new URL(request.url);
    const ticketId = searchParams.get('ticketId');

    if (!ticketId) {
      return NextResponse.json({ error: 'ticketId is required.' }, { status: 400 });
    }

    const { data: comments, error } = await supabaseAdmin
      .from('maintenance_comments')
      .select('*')
      .eq('ticket_id', ticketId)
      .order('created_at', { ascending: true });

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    return NextResponse.json({ comments: comments || [] });
  } catch (error: any) {
    console.error('Comment GET failed:', error);
    return NextResponse.json({ error: error.message || 'Failed to fetch comments.' }, { status: 500 });
  }
}
