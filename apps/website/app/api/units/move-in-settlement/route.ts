import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { getActivePropertyId } from '../../../../lib/property-access';
import { createTenantActivation } from '../../../../lib/tenant-activation';
import { normalizeKenyanPhoneForStorage } from '../../../../lib/phone';

const admin = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.SUPABASE_SERVICE_ROLE_KEY!, { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } });

export async function POST(request: Request) {
  const auth = await requireApiRole(['landlord']);
  if (auth.response) return auth.response;
  try {
    const body = await request.json();
    const unitId = String(body.unitId || '').trim();
    const tenantName = String(body.tenantName || '').trim();
    const tenantEmail = String(body.tenantEmail || '').trim().toLowerCase();
    const tenantPhone = normalizeKenyanPhoneForStorage(String(body.tenantPhone || '').trim());
    const depositAmount = Number(body.depositAmount);
    const rentAmount = Number(body.rentAmount);
    const garbageAmount = Number(body.garbageAmount);
    const waterDepositAmount = Number(body.waterDepositAmount);
    const expectedMoveInDate = String(body.expectedMoveInDate || '').trim();
    const parsedMoveInDate = new Date(`${expectedMoveInDate}T00:00:00.000Z`);
    const todayInNairobi = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Africa/Nairobi',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(new Date());

    if (
      !unitId || !tenantName || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(tenantEmail) || !tenantPhone
      || !/^\d{4}-\d{2}-\d{2}$/.test(expectedMoveInDate)
      || Number.isNaN(parsedMoveInDate.getTime())
      || parsedMoveInDate.toISOString().slice(0, 10) !== expectedMoveInDate
      || expectedMoveInDate < todayInNairobi
      || rentAmount <= 0
      || [depositAmount, rentAmount, garbageAmount, waterDepositAmount].some((value) => !Number.isFinite(value) || value < 0)
    ) {
      return NextResponse.json({ error: 'Unit, tenant name, valid email and phone, a move-in date that is today or later, and non-negative charges are required.' }, { status: 400 });
    }

    const scope = await getActivePropertyId(admin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
    const { data: scopedUnit, error: unitError } = await admin.from('units')
      .select('id, status')
      .eq('id', unitId)
      .eq('property_id', scope.propertyId)
      .maybeSingle();
    if (unitError || !scopedUnit) return NextResponse.json({ error: 'Unit not found in the active property.' }, { status: 404 });
    if (scopedUnit.status !== 'vacant') return NextResponse.json({ error: 'Only a vacant unit can be reserved for a move-in.' }, { status: 409 });

    const { data: openSettlement, error: openSettlementError } = await admin.from('move_in_settlements')
      .select('id')
      .eq('unit_id', unitId)
      .in('status', ['payment_pending', 'partial', 'booked', 'paid', 'move_in_pending', 'reserved'])
      .limit(1)
      .maybeSingle();
    if (openSettlementError) return NextResponse.json({ error: openSettlementError.message }, { status: 500 });
    if (openSettlement) return NextResponse.json({ error: 'This unit already has an open move-in reservation.' }, { status: 409 });

    const { data: existingTenant, error: existingTenantError } = await admin.from('users')
      .select('id')
      .ilike('email', tenantEmail)
      .maybeSingle();
    if (existingTenantError) return NextResponse.json({ error: existingTenantError.message }, { status: 500 });
    if (existingTenant) return NextResponse.json({ error: 'This email is already registered. Use a different email or the tenant transfer workflow.' }, { status: 409 });

    const { data: recurringCharges, error: chargesError } = await admin.from('property_recurring_charges')
      .select('name, amount')
      .eq('property_id', scope.propertyId)
      .eq('is_active', true)
      .order('name');
    if (chargesError) return NextResponse.json({ error: chargesError.message }, { status: 500 });

    const utilityCharges = (recurringCharges || []).map((charge) => ({
      name: charge.name,
      amount: Number(charge.amount),
    }));
    const { data: reservation, error } = await admin.rpc('create_move_in_reservation', {
      p_unit_id: unitId,
      p_property_id: scope.propertyId,
      p_tenant_name: tenantName,
      p_tenant_email: tenantEmail,
      p_tenant_phone: tenantPhone,
      p_deposit_amount: depositAmount,
      p_rent_amount: rentAmount,
      p_garbage_amount: garbageAmount,
      p_water_deposit_amount: waterDepositAmount,
      p_utility_charges: utilityCharges,
      p_expected_move_in_date: expectedMoveInDate,
    });
    if (error) return NextResponse.json({ error: error.message }, { status: 500 });
    if (!reservation?.success) {
      const messages: Record<string, string> = {
        unit_not_found: 'Unit not found in the active property.',
        unit_not_vacant: 'This unit is no longer vacant.',
        unit_has_open_reservation: 'This unit already has an open move-in reservation.',
        email_has_open_reservation: 'This email already has an open move-in reservation.',
      };
      return NextResponse.json({ error: messages[reservation?.reason] || 'Unable to reserve this unit.' }, { status: 409 });
    }
    return NextResponse.json({ settlement: reservation.settlement }, { status: 201 });
  } catch (error) {
    console.error('Failed to create move-in reservation:', error);
    return NextResponse.json({ error: 'Unable to create the move-in reservation.' }, { status: 500 });
  }
}

export async function PATCH(request: Request) {
  const auth = await requireApiRole(['landlord']);
  if (auth.response) return auth.response;
  const scope = await getActivePropertyId(admin, auth.currentUser!.user.id);
  if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
  const body = await request.json();
  const settlementId = String(body.settlementId || '').trim();
  if (body.sendActivationEmail === true) {
    const { data: settlement, error: settlementError } = await admin.from('move_in_settlements')
      .select('*')
      .eq('id', settlementId)
      .maybeSingle();
    if (settlementError || !settlement) return NextResponse.json({ error: 'Settlement not found.' }, { status: 404 });
    const { data: scopedUnit, error: scopedUnitError } = await admin.from('units')
      .select('id, unit_code, property_id')
      .eq('id', settlement.unit_id)
      .eq('property_id', scope.propertyId)
      .maybeSingle();
    if (scopedUnitError || !scopedUnit) return NextResponse.json({ error: 'Settlement not found in the active property.' }, { status: 404 });
    if (
      settlement.status !== 'paid'
      || Number(settlement.outstanding_balance) > 0
      || Number(settlement.total_paid) < Number(settlement.total_required)
      || Number(settlement.deposit_paid_amount || 0)
        < Number(settlement.deposit_amount || 0) + Number(settlement.water_deposit_amount || 0)
    ) {
      return NextResponse.json({ error: 'The full move-in balance must be paid before sending an activation link.' }, { status: 409 });
    }
    if (!settlement.tenant_email || !settlement.expected_move_in_date) {
      return NextResponse.json({ error: 'A tenant email and expected move-in date are required before activation.' }, { status: 409 });
    }

    let tenantId = settlement.tenant_id as string | null;
    let createdAuthUser = false;
    let createdProfile = false;
    if (tenantId) {
      const { data: tenant, error: tenantError } = await admin.from('users')
        .select('id, email, full_name, status, role')
        .eq('id', tenantId)
        .maybeSingle();
      if (tenantError || !tenant || tenant.role !== 'tenant' || tenant.status !== 'dormant') {
        return NextResponse.json({ error: 'The reserved tenant account is unavailable for activation.' }, { status: 409 });
      }
    } else {
      const { data: existingProfile, error: existingProfileError } = await admin.from('users')
        .select('id')
        .ilike('email', settlement.tenant_email)
        .maybeSingle();
      if (existingProfileError) return NextResponse.json({ error: existingProfileError.message }, { status: 500 });
      if (existingProfile) return NextResponse.json({ error: 'This email is already linked to another account.' }, { status: 409 });

      const { data: authResult, error: authError } = await admin.auth.admin.createUser({
        email: settlement.tenant_email,
        email_confirm: true,
        user_metadata: { full_name: settlement.tenant_name, phone: settlement.tenant_phone },
      });
      if (authError || !authResult.user) {
        return NextResponse.json({ error: authError?.message || 'Unable to create the tenant account.' }, { status: 400 });
      }
      tenantId = authResult.user.id;
      createdAuthUser = true;

      const { error: profileError } = await admin.from('users').insert({
        id: tenantId,
        full_name: settlement.tenant_name,
        email: settlement.tenant_email,
        phone: settlement.tenant_phone,
        role: 'tenant',
        status: 'dormant',
      });
      if (profileError) {
        await admin.auth.admin.deleteUser(tenantId);
        return NextResponse.json({ error: profileError.message }, { status: 500 });
      }
      createdProfile = true;
    }

    const { data: assignment, error: assignmentLookupError } = await admin.from('unit_assignments')
      .select('id')
      .eq('unit_id', settlement.unit_id)
      .eq('tenant_id', tenantId)
      .eq('status', 'active')
      .maybeSingle();
    if (assignmentLookupError) {
      if (createdProfile && tenantId) await admin.from('users').delete().eq('id', tenantId);
      if (createdAuthUser && tenantId) await admin.auth.admin.deleteUser(tenantId);
      return NextResponse.json({ error: assignmentLookupError.message }, { status: 500 });
    }

    if (!assignment) {
      const { data: existingAssignment, error: existingAssignmentError } = await admin.from('unit_assignments')
        .select('id')
        .eq('unit_id', settlement.unit_id)
        .eq('status', 'active')
        .maybeSingle();
      if (existingAssignmentError) {
        if (createdProfile && tenantId) await admin.from('users').delete().eq('id', tenantId);
        if (createdAuthUser && tenantId) await admin.auth.admin.deleteUser(tenantId);
        return NextResponse.json({ error: existingAssignmentError.message }, { status: 500 });
      }
      if (existingAssignment) {
        if (createdProfile && tenantId) await admin.from('users').delete().eq('id', tenantId);
        if (createdAuthUser && tenantId) await admin.auth.admin.deleteUser(tenantId);
        return NextResponse.json({ error: 'This unit already has an active tenant assignment.' }, { status: 409 });
      }

      const { error: assignmentError } = await admin.from('unit_assignments').insert({
        unit_id: settlement.unit_id,
        tenant_id: tenantId,
        move_in_date: settlement.expected_move_in_date,
        status: 'active',
        deposit_paid: Number(settlement.deposit_paid_amount || 0),
        is_migrated: false,
      });
      if (assignmentError) {
        if (createdProfile && tenantId) await admin.from('users').delete().eq('id', tenantId);
        if (createdAuthUser && tenantId) await admin.auth.admin.deleteUser(tenantId);
        return NextResponse.json({ error: assignmentError.message }, { status: 500 });
      }
    }

    const { error: linkTenantError } = await admin.from('move_in_settlements')
      .update({ tenant_id: tenantId, updated_at: new Date().toISOString() })
      .eq('id', settlementId);
    if (linkTenantError) {
      if (!assignment) await admin.from('unit_assignments').delete().eq('unit_id', settlement.unit_id).eq('tenant_id', tenantId).eq('status', 'active');
      if (createdProfile && tenantId) await admin.from('users').delete().eq('id', tenantId);
      if (createdAuthUser && tenantId) await admin.auth.admin.deleteUser(tenantId);
      return NextResponse.json({ error: linkTenantError.message }, { status: 500 });
    }

    const { data: property, error: propertyError } = await admin.from('properties')
      .select('name')
      .eq('id', scope.propertyId)
      .maybeSingle();
    if (propertyError) return NextResponse.json({ error: propertyError.message }, { status: 500 });
    let activation: Awaited<ReturnType<typeof createTenantActivation>>;
    try {
      activation = await createTenantActivation(admin, {
        unitId: scopedUnit.id,
        unitCode: scopedUnit.unit_code,
        tenantId: tenantId!,
        fullName: settlement.tenant_name,
        email: settlement.tenant_email,
        origin: new URL(request.url).origin,
        propertyName: property?.name,
        createClaimCode: false,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unexpected activation email error.';
      console.error('Move-in activation email failed:', error);
      await admin.from('move_in_settlements')
        .update({ activation_email_error: message, updated_at: new Date().toISOString() })
        .eq('id', settlementId);
      return NextResponse.json({ error: `Tenant account is ready, but the activation email was not sent: ${message}` }, { status: 502 });
    }

    const emailSentAt = activation.emailSent ? new Date().toISOString() : null;
    const { data: updatedSettlement, error: emailStatusError } = await admin.from('move_in_settlements')
      .update({
        activation_email_sent_at: emailSentAt || settlement.activation_email_sent_at,
        activation_email_error: activation.emailError,
        updated_at: new Date().toISOString(),
      })
      .eq('id', settlementId)
      .select('id, activation_email_sent_at, activation_email_error')
      .single();
    if (emailStatusError) return NextResponse.json({ error: emailStatusError.message }, { status: 500 });
    if (!activation.emailSent) {
      return NextResponse.json({ error: `Tenant account is ready, but the activation email was not sent: ${activation.emailError || 'email provider error'}`, settlement: updatedSettlement }, { status: 502 });
    }

    return NextResponse.json({ success: true, message: `Activation link sent to ${settlement.tenant_email}.`, settlement: updatedSettlement });
  }

  const amount = Number(body.amount || 0);
  const reference = String(body.mpesaReference || '').trim().toUpperCase();
  if (!settlementId || !Number.isFinite(amount) || amount <= 0) return NextResponse.json({ error: 'Settlement and a positive payment amount are required.' }, { status: 400 });

  const { data: settlement, error: settlementError } = await admin.from('move_in_settlements').select('*').eq('id', settlementId).single();
  if (settlementError || !settlement) return NextResponse.json({ error: 'Settlement not found.' }, { status: 404 });
  const { data: scopedUnit } = await admin.from('units').select('id').eq('id', settlement.unit_id).eq('property_id', scope.propertyId).maybeSingle();
  if (!scopedUnit) return NextResponse.json({ error: 'Settlement not found in the active property.' }, { status: 404 });
  if (settlement.status === 'paid' || settlement.status === 'active') return NextResponse.json({ error: 'This settlement is already cleared.' }, { status: 400 });

  const { data: result, error: paymentError } = await admin.rpc('record_move_in_payment', {
    p_settlement_id: settlementId,
    p_amount: amount,
    p_method: 'manual',
    p_reference: reference || null,
    p_recorded_by: auth.currentUser!.user.id,
    p_checkout_request_id: null,
    p_is_callback: false,
  });
  if (paymentError) return NextResponse.json({ error: paymentError.message }, { status: 500 });
  if (!result?.applied) return NextResponse.json({ error: result?.reason || 'Payment could not be applied.' }, { status: 409 });

  const { data: updated, error: updateError } = await admin.from('move_in_settlements').select('*').eq('id', settlementId).single();
  if (updateError) return NextResponse.json({ error: updateError.message }, { status: 500 });
  return NextResponse.json({
    settlement: updated,
    bookingConfirmed: Number(updated.deposit_paid_amount || 0) >= Number(updated.deposit_amount || 0) + Number(updated.water_deposit_amount || 0),
    activationReady: Number(updated.outstanding_balance || 0) <= 0,
  });
}

export async function DELETE(request: Request) {
  const auth = await requireApiRole(['landlord']);
  if (auth.response) return auth.response;
  const scope = await getActivePropertyId(admin, auth.currentUser!.user.id);
  if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
  const body = await request.json();
  const settlementId = String(body.settlementId || '').trim();
  const reason = String(body.reason || '').trim();
  if (!settlementId || !reason) return NextResponse.json({ error: 'Settlement and cancellation reason are required.' }, { status: 400 });
  const { data: settlement } = await admin.from('move_in_settlements')
    .select('unit_id, tenant_id, activation_email_sent_at')
    .eq('id', settlementId)
    .maybeSingle();
  if (!settlement) return NextResponse.json({ error: 'Settlement not found.' }, { status: 404 });
  if (settlement.tenant_id || settlement.activation_email_sent_at) {
    return NextResponse.json({ error: 'This reservation already has a tenant account or activation email. Revoke the tenant access through the move-out workflow instead.' }, { status: 409 });
  }
  const { data: scopedUnit } = await admin.from('units').select('id').eq('id', settlement.unit_id).eq('property_id', scope.propertyId).maybeSingle();
  if (!scopedUnit) return NextResponse.json({ error: 'Settlement not found in the active property.' }, { status: 404 });
  const { data, error } = await admin.from('move_in_settlements').update({ status: 'cancelled', cancellation_reason: reason, cancelled_at: new Date().toISOString(), updated_at: new Date().toISOString() }).eq('id', settlementId).in('status', ['payment_pending', 'partial', 'booked', 'reserved', 'move_in_pending']).select().single();
  if (error || !data) return NextResponse.json({ error: error?.message || 'Settlement cannot be cancelled.' }, { status: 400 });

  const { data: activeAssignment, error: assignmentError } = await admin.from('unit_assignments')
    .select('id')
    .eq('unit_id', settlement.unit_id)
    .eq('status', 'active')
    .limit(1)
    .maybeSingle();
  if (assignmentError) return NextResponse.json({ error: assignmentError.message }, { status: 500 });
  if (!activeAssignment) {
    const { error: unitUpdateError } = await admin.from('units')
      .update({ status: 'vacant', updated_at: new Date().toISOString() })
      .eq('id', settlement.unit_id)
      .eq('status', 'booked');
    if (unitUpdateError) return NextResponse.json({ error: unitUpdateError.message }, { status: 500 });
  }
  return NextResponse.json({ settlement: data });
}

export async function GET() {
  const auth = await requireApiRole(['landlord']);
  if (auth.response) return auth.response;
  const scope = await getActivePropertyId(admin, auth.currentUser!.user.id);
  if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
  const { data: units, error: unitsError } = await admin.from('units').select('id').eq('property_id', scope.propertyId);
  if (unitsError) return NextResponse.json({ error: unitsError.message }, { status: 500 });
  const unitIds = (units || []).map((unit) => unit.id);
  const { data, error } = unitIds.length
    ? await admin.from('move_in_settlements').select('*, units(unit_code)').in('unit_id', unitIds).in('status', ['payment_pending', 'partial', 'booked', 'paid', 'move_in_pending', 'reserved']).order('created_at', { ascending: false })
    : { data: [], error: null };
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json({ settlements: data || [] });
}
