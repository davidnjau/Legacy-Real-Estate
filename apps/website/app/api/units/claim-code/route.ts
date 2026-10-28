import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { generateTenantActivationCode } from '../../../../lib/tenant-activation';
import { getActivePropertyId } from '../../../../lib/property-access';

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
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;

    const body = await request.json();
    const unitId = String(body.unitId || '').trim();

    if (!unitId) {
      return NextResponse.json({ error: 'Unit ID is required.' }, { status: 400 });
    }

    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const { data: unit, error: unitError } = await supabaseAdmin
      .from('units')
      .select('id, status, unit_code')
      .eq('id', unitId)
      .eq('property_id', scope.propertyId)
      .single();

    if (unitError || !unit) {
      return NextResponse.json({ error: 'Unit not found.' }, { status: 404 });
    }

    const { data: assignment } = await supabaseAdmin
      .from('unit_assignments')
      .select('tenant_id, status')
      .eq('unit_id', unit.id)
      .eq('status', 'active')
      .maybeSingle();
    let moveInSettlementId: string | null = null;

    if (!['vacant', 'booked'].includes(unit.status) && !assignment) {
      return NextResponse.json({ error: 'Only vacant units or occupied dormant tenant accounts can receive claim codes.' }, { status: 400 });
    }

    if (assignment) {
      const { data: tenant } = await supabaseAdmin
        .from('users')
        .select('status')
        .eq('id', assignment.tenant_id)
        .maybeSingle();

      if (tenant?.status !== 'dormant') {
        return NextResponse.json({ error: 'This tenant account is already active.' }, { status: 400 });
      }
    } else {
      const { data: settlement } = await supabaseAdmin
        .from('move_in_settlements')
        .select('id, status, total_paid, deposit_amount, water_deposit_amount, activation_override, claim_code_issued_at')
        .eq('unit_id', unit.id)
        .in('status', ['payment_pending', 'partial', 'booked', 'paid', 'move_in_pending', 'reserved'])
        .order('created_at', { ascending: false })
        .limit(1)
        .maybeSingle();

      const depositsPaid = settlement
        && Number(settlement.total_paid || 0) >= Number(settlement.deposit_amount || 0) + Number(settlement.water_deposit_amount || 0);
      const canActivate = settlement && (settlement.status === 'paid' || (settlement.activation_override === true && depositsPaid));
      if (!canActivate) {
        return NextResponse.json({ error: 'Clear the move-in settlement or authorize an activation override before generating a claim code.' }, { status: 400 });
      }
      if (settlement.claim_code_issued_at) return NextResponse.json({ error: 'A claim code has already been issued for this move-in settlement.' }, { status: 409 });
      moveInSettlementId = settlement.id;
    }

    const code = assignment
      ? generateTenantActivationCode(unit.unit_code)
      : `${unit.unit_code}-${Math.random().toString(36).slice(2, 6).toUpperCase()}`;
    const expiresAt = new Date(Date.now() + 48 * 60 * 60 * 1000).toISOString();
    const claimCodeIssuedAt = moveInSettlementId ? new Date().toISOString() : null;

    if (moveInSettlementId && claimCodeIssuedAt) {
      const { data: reservedSettlement, error: reserveError } = await supabaseAdmin
        .from('move_in_settlements')
        .update({ claim_code_issued_at: claimCodeIssuedAt, updated_at: claimCodeIssuedAt })
        .eq('id', moveInSettlementId)
        .is('claim_code_issued_at', null)
        .select('id')
        .maybeSingle();
      if (reserveError) return NextResponse.json({ error: reserveError.message }, { status: 500 });
      if (!reservedSettlement) return NextResponse.json({ error: 'A claim code has already been issued for this move-in settlement.' }, { status: 409 });
    }

    const { data: insertedCode, error: codeError } = await supabaseAdmin
      .from('claim_codes')
      .insert({
        unit_id: unit.id,
        code,
        expires_at: expiresAt,
      })
      .select()
      .single();

    if (codeError || !insertedCode) {
      if (moveInSettlementId && claimCodeIssuedAt) {
        await supabaseAdmin.from('move_in_settlements')
          .update({ claim_code_issued_at: null, updated_at: new Date().toISOString() })
          .eq('id', moveInSettlementId)
          .eq('claim_code_issued_at', claimCodeIssuedAt);
      }
      return NextResponse.json({ error: 'Failed to create claim code.' }, { status: 500 });
    }

    return NextResponse.json({
      success: true,
      claimCode: insertedCode.code,
      expiresAt,
    });
  } catch (error: any) {
    console.error('Claim code generation failed:', error);
    return NextResponse.json({ error: 'Failed to generate unit claim code.' }, { status: 500 });
  }
}

export async function GET() {
  try {
    const auth = await requireApiRole(['landlord']);
    if (auth.response) return auth.response;

    const { data: codes, error: codeError } = await supabaseAdmin
      .from('claim_codes')
      .select('id, unit_id, code, expires_at, used_at')
      .is('used_at', null)
      .gt('expires_at', new Date().toISOString())
      .order('expires_at', { ascending: true });

    if (codeError) return NextResponse.json({ error: codeError.message }, { status: 500 });

    const unitIds = [...new Set((codes || []).map((code) => code.unit_id))];
    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
    const { data: units } = unitIds.length
      ? await supabaseAdmin.from('units').select('id, unit_code').in('id', unitIds).eq('property_id', scope.propertyId)
      : { data: [] };
    const unitMap = new Map((units || []).map((unit) => [unit.id, unit.unit_code]));

    const { data: assignments } = unitIds.length
      ? await supabaseAdmin.from('unit_assignments').select('unit_id, tenant_id').in('unit_id', unitIds).eq('status', 'active')
      : { data: [] };
    const tenantIds = [...new Set((assignments || []).map((assignment) => assignment.tenant_id))];
    const { data: tenants } = tenantIds.length
      ? await supabaseAdmin.from('users').select('id, full_name, email, status').in('id', tenantIds)
      : { data: [] };
    const assignmentMap = new Map((assignments || []).map((assignment) => [assignment.unit_id, assignment.tenant_id]));
    const tenantMap = new Map((tenants || []).map((tenant) => [tenant.id, tenant]));

    return NextResponse.json({
      codes: (codes || []).map((code) => {
        const tenant = tenantMap.get(assignmentMap.get(code.unit_id) || '');
        return {
          id: code.id,
          unitCode: unitMap.get(code.unit_id) || '-',
          code: code.code,
          expiresAt: code.expires_at,
          tenantName: tenant?.full_name || '-',
          tenantEmail: tenant?.email || '-',
          tenantStatus: tenant?.status || null,
        };
      }).filter((code) => code.tenantStatus === 'dormant'),
    });
  } catch (error: any) {
    console.error('Activation code list failed:', error);
    return NextResponse.json({ error: error.message || 'Failed to load activation codes.' }, { status: 500 });
  }
}
