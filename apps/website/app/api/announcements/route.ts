import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../lib/api-auth';
import { getActivePropertyId } from '../../../lib/property-access';

const admin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

export async function GET() {
  const auth = await requireApiRole(['landlord', 'caretaker', 'tenant']);
  if (auth.response) return auth.response;

  let propertyId: string | null = null;
  let tenantUnitIds: string[] | null = null;
  if (auth.currentUser!.profile.role === 'tenant') {
    const { data: assignments, error: assignmentError } = await admin
      .from('unit_assignments')
      .select('unit_id, units!inner(property_id)')
      .eq('tenant_id', auth.currentUser!.user.id)
      .eq('status', 'active');
    if (assignmentError) return NextResponse.json({ error: assignmentError.message }, { status: 500 });
    const currentAssignment = assignments?.[0];
    const unit = Array.isArray(currentAssignment?.units) ? currentAssignment.units[0] : currentAssignment?.units;
    propertyId = unit?.property_id || null;
    tenantUnitIds = (assignments || []).map((assignment) => assignment.unit_id);
    if (!propertyId || tenantUnitIds.length === 0) return NextResponse.json({ announcements: [] });
  } else {
    const scope = await getActivePropertyId(admin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
    propertyId = scope.propertyId;
  }

  const { data: announcements, error } = await admin
    .from('property_announcements')
    .select('id, title, message, audience, expires_at, created_at, created_by')
    .eq('property_id', propertyId)
    .gt('expires_at', new Date().toISOString())
    .order('created_at', { ascending: false })
    .limit(100);
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });

  const announcementIds = (announcements || []).map((announcement) => announcement.id);
  const { data: targets, error: targetError } = announcementIds.length
    ? await admin.from('property_announcement_units')
        .select('announcement_id, unit_id, units(unit_code)')
        .in('announcement_id', announcementIds)
    : { data: [], error: null };
  if (targetError) return NextResponse.json({ error: targetError.message }, { status: 500 });

  const allowedUnits = tenantUnitIds ? new Set(tenantUnitIds) : null;
  const targetMap = new Map<string, { unitId: string; unitCode: string }[]>();
  for (const target of targets || []) {
    if (allowedUnits && !allowedUnits.has(target.unit_id)) continue;
    const unit = Array.isArray(target.units) ? target.units[0] : target.units;
    const list = targetMap.get(target.announcement_id) || [];
    list.push({ unitId: target.unit_id, unitCode: unit?.unit_code || '' });
    targetMap.set(target.announcement_id, list);
  }

  const visibleAnnouncements = (announcements || [])
    .filter((announcement) => !allowedUnits || announcement.audience === 'property' || targetMap.has(announcement.id))
    .map((announcement) => ({
      ...announcement,
      targets: targetMap.get(announcement.id) || [],
    }));

  return NextResponse.json({ announcements: visibleAnnouncements });
}

export async function POST(request: Request) {
  const auth = await requireApiRole(['caretaker']);
  if (auth.response) return auth.response;
  const scope = await getActivePropertyId(admin, auth.currentUser!.user.id);
  if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

  try {
    const body = await request.json();
    const title = String(body.title || '').trim();
    const message = String(body.message || '').trim();
    const audience = body.audience === 'units' ? 'units' : body.audience === 'property' ? 'property' : null;
    const expiresAt = new Date(String(body.expiresAt || ''));
    const unitIds = Array.isArray(body.unitIds)
      ? [...new Set(body.unitIds.map((id: unknown) => String(id).trim()).filter(Boolean))]
      : [];
    if (!title || title.length > 120 || !message || message.length > 4000 || !audience || Number.isNaN(expiresAt.getTime()) || expiresAt <= new Date()) {
      return NextResponse.json({ error: 'Provide a title, message, audience, and future expiry date.' }, { status: 400 });
    }
    if (audience === 'units' && (unitIds.length === 0 || unitIds.length > 500)) {
      return NextResponse.json({ error: 'Choose between 1 and 500 units for this announcement.' }, { status: 400 });
    }

    if (audience === 'units') {
      const { data: units, error: unitError } = await admin.from('units')
        .select('id')
        .eq('property_id', scope.propertyId)
        .in('id', unitIds);
      if (unitError) return NextResponse.json({ error: unitError.message }, { status: 500 });
      if ((units || []).length !== unitIds.length) return NextResponse.json({ error: 'One or more selected units are outside the assigned property.' }, { status: 403 });
    }

    const { data: announcement, error: insertError } = await admin.from('property_announcements')
      .insert({
        property_id: scope.propertyId,
        created_by: auth.currentUser!.user.id,
        title,
        message,
        audience,
        expires_at: expiresAt.toISOString(),
      })
      .select('id, title, message, audience, expires_at, created_at, created_by')
      .single();
    if (insertError || !announcement) return NextResponse.json({ error: insertError?.message || 'Unable to save announcement.' }, { status: 500 });

    if (audience === 'units') {
      const { error: targetError } = await admin.from('property_announcement_units')
        .insert(unitIds.map((unitId: string) => ({ announcement_id: announcement.id, unit_id: unitId })));
      if (targetError) {
        const { error: cleanupError } = await admin.from('property_announcements').delete().eq('id', announcement.id);
        if (cleanupError) console.error('Failed to roll back incomplete announcement:', cleanupError);
        return NextResponse.json({ error: targetError.message }, { status: 500 });
      }
    }

    return NextResponse.json({ announcement }, { status: 201 });
  } catch (error) {
    console.error('Failed to create announcement:', error);
    return NextResponse.json({ error: 'Unable to create the announcement.' }, { status: 500 });
  }
}
