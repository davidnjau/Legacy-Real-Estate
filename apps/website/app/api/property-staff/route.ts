import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../lib/api-auth';
import { getActivePropertyId } from '../../../lib/property-access';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

export async function GET() {
  const auth = await requireApiRole(['landlord']);
  if (auth.response) return auth.response;
  const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
  if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

  const { data, error } = await supabaseAdmin.from('property_staff')
    .select('id, full_name, email, phone, staff_role, notes, created_at')
    .eq('property_id', scope.propertyId)
    .order('full_name');
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json({ staff: data || [] });
}

export async function POST(request: Request) {
  const auth = await requireApiRole(['landlord']);
  if (auth.response) return auth.response;
  const landlordId = auth.currentUser!.user.id;
  const scope = await getActivePropertyId(supabaseAdmin, landlordId);
  if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

  const body = await request.json();
  const fullName = String(body.fullName || '').trim();
  const email = String(body.email || '').trim().toLowerCase();
  const phone = String(body.phone || '').trim();
  const staffRole = String(body.staffRole || '').trim();
  const notes = String(body.notes || '').trim();
  if (!fullName || !staffRole || (!email && !phone)) {
    return NextResponse.json({ error: 'Name, staff role, and at least one contact detail are required.' }, { status: 400 });
  }
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return NextResponse.json({ error: 'Enter a valid email address.' }, { status: 400 });
  }
  if (staffRole.length > 80 || fullName.length > 150 || email.length > 254 || phone.length > 40 || notes.length > 1000) {
    return NextResponse.json({ error: 'One or more staff details exceed the allowed length.' }, { status: 400 });
  }

  const { data, error } = await supabaseAdmin.from('property_staff').insert({
    property_id: scope.propertyId,
    created_by: landlordId,
    full_name: fullName,
    email: email || null,
    phone: phone || null,
    staff_role: staffRole,
    notes: notes || null,
  }).select('id, full_name, email, phone, staff_role, notes, created_at').single();
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json({ staff: data }, { status: 201 });
}

export async function DELETE(request: Request) {
  const auth = await requireApiRole(['landlord']);
  if (auth.response) return auth.response;
  const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
  if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
  const { staffId } = await request.json();
  if (typeof staffId !== 'string' || !staffId) return NextResponse.json({ error: 'Staff ID is required.' }, { status: 400 });

  const { data, error } = await supabaseAdmin.from('property_staff').delete()
    .eq('id', staffId).eq('property_id', scope.propertyId).select('id').maybeSingle();
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  if (!data) return NextResponse.json({ error: 'Staff member not found in the active property.' }, { status: 404 });
  return NextResponse.json({ success: true });
}
