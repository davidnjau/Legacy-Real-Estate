import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../lib/api-auth';
import { getActivePropertyId } from '../../../lib/property-access';

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

export async function GET() {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;

    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const { data: units, error: unitsError } = await supabaseAdmin
      .from('units')
      .select('*')
      .eq('property_id', scope.propertyId)
      .order('unit_code', { ascending: true });

    if (unitsError) {
      return NextResponse.json({ error: unitsError.message }, { status: 500 });
    }

    const unitIds = (units || []).map((unit) => unit.id);
    const { data: assignments, error: assignmentsError } = unitIds.length
      ? await supabaseAdmin
          .from('unit_assignments')
          .select('id, unit_id, tenant_id, move_in_date, status, expected_move_out_date')
          .in('unit_id', unitIds)
          .eq('status', 'active')
      : { data: [], error: null };

    if (assignmentsError) {
      return NextResponse.json({ error: assignmentsError.message }, { status: 500 });
    }

    const tenantIds = [...new Set((assignments || []).map((assignment) => assignment.tenant_id).filter(Boolean))];
    let tenantProfiles: Record<string, { full_name: string; email: string; phone: string; status: string }> = {};

    if (tenantIds.length) {
      const { data: profiles, error: profilesError } = await supabaseAdmin
        .from('users')
        .select('id, full_name, email, phone, status')
        .in('id', tenantIds);

      if (profilesError) {
        return NextResponse.json({ error: profilesError.message }, { status: 500 });
      }

      tenantProfiles = Object.fromEntries((profiles || []).map((profile) => [profile.id, profile]));
    }

    const { data: settlements, error: settlementsError } = unitIds.length
      ? await supabaseAdmin
          .from('move_in_settlements')
          .select('unit_id, tenant_name, tenant_email, deposit_amount, water_deposit_amount, expected_move_in_date, status')
          .in('unit_id', unitIds)
          .in('status', ['paid', 'active', 'booked', 'payment_pending', 'partial'])
          .order('created_at', { ascending: false })
      : { data: [], error: null };

    if (settlementsError) {
      return NextResponse.json({ error: settlementsError.message }, { status: 500 });
    }

    const settlementMap = new Map();
    for (const s of settlements || []) {
      if (!settlementMap.has(s.unit_id)) settlementMap.set(s.unit_id, s);
    }

    const assignmentMap = new Map();

    for (const assignment of assignments || []) {
      const profile = tenantProfiles[assignment.tenant_id];
      assignmentMap.set(assignment.unit_id, {
        tenantId: assignment.tenant_id || null,
        tenantName: profile?.full_name || '-',
        tenantEmail: profile?.email || '-',
        tenantPhone: profile?.phone || '-',
        tenantStatus: profile?.status || null,
        moveInDate: assignment.move_in_date || '-',
        expectedMoveOutDate: assignment.expected_move_out_date || null,
      });
    }

    const mappedUnits = (units || []).map((unit) => ({
      id: unit.id,
      unitCode: unit.unit_code,
      roomName: unit.room_name,
      floor: unit.floor_number || '-',
      status: unit.status,
      rentAmount: unit.rent_amount,
      garbageAmount: unit.garbage_amount,
      depositAmount: Number(settlementMap.get(unit.id)?.deposit_amount ?? unit.deposit_amount ?? 0),
      securityDeposit: Number(settlementMap.get(unit.id)?.deposit_amount ?? unit.deposit_amount ?? 0),
      waterDeposit: Number(settlementMap.get(unit.id)?.water_deposit_amount ?? unit.water_deposit_amount ?? 0),
      monthlyWaterRate: unit.monthly_water_rate,
      tenantId: assignmentMap.get(unit.id)?.tenantId || null,
      currentTenant: assignmentMap.get(unit.id)?.tenantName || '-',
      tenantEmail: assignmentMap.get(unit.id)?.tenantEmail || settlementMap.get(unit.id)?.tenant_email || '-',
      tenantPhone: assignmentMap.get(unit.id)?.tenantPhone || '-',
      tenantStatus: assignmentMap.get(unit.id)?.tenantStatus || null,
      currentTenantName: assignmentMap.get(unit.id)?.tenantId
        ? assignmentMap.get(unit.id)?.tenantName || '-'
        : settlementMap.get(unit.id)?.tenant_name || '-',
      moveInDate: assignmentMap.get(unit.id)?.moveInDate || settlementMap.get(unit.id)?.expected_move_in_date || '-',
      expectedMoveOutDate: assignmentMap.get(unit.id)?.expectedMoveOutDate || null,
    }));

    return NextResponse.json({ units: mappedUnits });
  } catch (error: any) {
    console.error('Failed to fetch units:', error);
    return NextResponse.json({ error: 'Failed to fetch units.' }, { status: 500 });
  }
}
