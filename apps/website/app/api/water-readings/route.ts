import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../lib/api-auth';

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

async function getLandlordUserId() {
  const { data, error } = await supabaseAdmin
    .from('users')
    .select('id')
    .eq('role', 'landlord')
    .limit(1)
    .single();

  if (error || !data) {
    throw new Error('No landlord account found in the users table.');
  }

  return data.id;
}

export async function GET() {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;

    const { data, error } = await supabaseAdmin
      .from('water_readings')
      .select('*')
      .order('reading_date', { ascending: false });

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    return NextResponse.json({ readings: data || [] });
  } catch (error: any) {
    console.error('Failed to fetch water readings:', error);
    return NextResponse.json({ error: 'Failed to fetch water readings.' }, { status: 500 });
  }
}

export async function POST(request: Request) {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;

    const body = await request.json();
    const readings = Array.isArray(body.readings) ? body.readings : [];

    if (!readings.length) {
      return NextResponse.json({ error: 'A list of water readings is required.' }, { status: 400 });
    }

    const submittedByUserId = auth.currentUser?.user.id;
    const today = new Date().toISOString().slice(0, 10);
    const rows: any[] = [];

    for (const entry of readings) {
      const unitId = String(entry.unitId || '').trim();
      const value = Number(entry.reading);

      if (!unitId || Number.isNaN(value) || value < 0) {
        return NextResponse.json({ error: 'Each reading must include a valid unit ID and non-negative value.' }, { status: 400 });
      }

      const { data: assignment, error: assignmentError } = await supabaseAdmin
        .from('unit_assignments')
        .select('tenant_id')
        .eq('unit_id', unitId)
        .eq('status', 'active')
        .limit(1)
        .single();

      if (assignmentError || !assignment) {
        return NextResponse.json({ error: `No active tenant is assigned to the unit ${unitId}.` }, { status: 400 });
      }

      rows.push({
        unit_id: unitId,
        tenant_id: assignment.tenant_id,
        submitted_by_user_id: submittedByUserId,
        reading_date: today,
        meter_reading: value,
        status: 'submitted',
      });
    }

    const { error: insertError } = await supabaseAdmin.from('water_readings').upsert(rows, {
      onConflict: 'unit_id,reading_date',
      ignoreDuplicates: false,
    });

    if (insertError) {
      return NextResponse.json({ error: insertError.message }, { status: 500 });
    }

    return NextResponse.json({
      success: true,
      message: `${rows.length} water reading(s) were saved successfully.`,
    });
  } catch (error: any) {
    console.error('Water reading save failed:', error);
    return NextResponse.json({ error: error.message || 'Failed to save water readings.' }, { status: 500 });
  }
}
