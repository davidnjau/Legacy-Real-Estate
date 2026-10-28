import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { syncDraftInvoiceArrears } from '../../../../lib/billing-generate';
import { getActivePropertyId, getPropertyUnitIds } from '../../../../lib/property-access';
import { getInvoiceReleaseAt } from '../../../../lib/billing-schedule';

const admin = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.SUPABASE_SERVICE_ROLE_KEY!, { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } });

export async function GET() {
  const auth = await requireApiRole(['landlord', 'caretaker']);
  if (auth.response) return auth.response;

  const scope = await getActivePropertyId(admin, auth.currentUser!.user.id);
  if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
  const { unitIds: propertyUnitIds, error: unitScopeError } = await getPropertyUnitIds(admin, scope.propertyId);
  if (unitScopeError) return NextResponse.json({ error: unitScopeError }, { status: 500 });

  const { data: assignments, error: assignmentError } = propertyUnitIds?.length ? await admin
    .from('unit_assignments')
    .select('id, unit_id, tenant_id, opening_arrears, migration_arrears_consumed_at, is_migrated, status')
    .in('unit_id', propertyUnitIds)
    .eq('status', 'active')
    : { data: [], error: null };
  if (assignmentError) return NextResponse.json({ error: assignmentError.message }, { status: 500 });

  const unitIds = [...new Set((assignments || []).map((item) => item.unit_id).filter(Boolean))];
  const tenantIds = [...new Set((assignments || []).map((item) => item.tenant_id).filter(Boolean))];
  const [{ data: units, error: unitsError }, { data: users, error: usersError }] = await Promise.all([
    unitIds.length ? admin.from('units').select('id, unit_code, is_migrated').in('id', unitIds) : Promise.resolve({ data: [], error: null }),
    tenantIds.length ? admin.from('users').select('id, full_name, status').in('id', tenantIds) : Promise.resolve({ data: [], error: null }),
  ]);
  if (unitsError || usersError) return NextResponse.json({ error: unitsError?.message || usersError?.message }, { status: 500 });

  const unitMap = new Map((units || []).map((unit) => [unit.id, unit]));
  const userMap = new Map((users || []).map((user) => [user.id, user]));
  const migratedAssignments = (assignments || []).map((assignment) => ({
    id: assignment.id,
    unit_id: assignment.unit_id,
    tenant_id: assignment.tenant_id,
    opening_arrears: Number(assignment.opening_arrears || 0),
    migration_arrears_consumed_at: assignment.migration_arrears_consumed_at,
    is_migrated: assignment.is_migrated,
    units: unitMap.get(assignment.unit_id),
    users: userMap.get(assignment.tenant_id),
  })).filter((assignment) => assignment.is_migrated === true);

  const assignmentIds = migratedAssignments.map((assignment) => assignment.id);
  const { data: paymentRows, error: paymentError } = assignmentIds.length
    ? await admin.from('migration_arrears_payments')
        .select('id, assignment_id, amount, mpesa_reference, payment_date, method, created_at')
        .in('assignment_id', assignmentIds)
        .order('payment_date', { ascending: false })
        .order('created_at', { ascending: false })
    : { data: [], error: null };
  if (paymentError) return NextResponse.json({ error: paymentError.message }, { status: 500 });
  const paymentsByAssignment = new Map<string, any[]>();
  for (const payment of paymentRows || []) {
    const payments = paymentsByAssignment.get(payment.assignment_id) || [];
    payments.push(payment);
    paymentsByAssignment.set(payment.assignment_id, payments);
  }

  const { data: settings, error: settingsError } = await admin.from('property_billing_settings').select('first_online_billing_month').eq('property_id', scope.propertyId).maybeSingle();
  if (settingsError) return NextResponse.json({ error: settingsError.message }, { status: 500 });
  const cutoffPassed = Boolean(settings?.first_online_billing_month
    && new Date(getInvoiceReleaseAt(settings.first_online_billing_month)) <= new Date());
  const releasedAssignments = new Set<string>();
  if (settings?.first_online_billing_month && migratedAssignments.length > 0) {
    const { data: releasedInvoices, error: releasedError } = await admin
      .from('invoices')
      .select('unit_id, tenant_id')
      .eq('billing_month', settings.first_online_billing_month)
      .not('published_at', 'is', null)
      .in('unit_id', [...new Set(migratedAssignments.map((assignment) => assignment.unit_id))]);
    if (releasedError) return NextResponse.json({ error: releasedError.message }, { status: 500 });
    for (const invoice of releasedInvoices || []) releasedAssignments.add(`${invoice.unit_id}:${invoice.tenant_id}`);
  }

  const assignmentsWithEditState = migratedAssignments.map((assignment) => ({
    ...assignment,
    arrears_editable: !cutoffPassed
      && !assignment.migration_arrears_consumed_at
      && !releasedAssignments.has(`${assignment.unit_id}:${assignment.tenant_id}`),
  }));

  return NextResponse.json({
    assignments: assignmentsWithEditState.map((assignment) => ({
      ...assignment,
      payments: paymentsByAssignment.get(assignment.id) || [],
    })),
    arrearsEditable: assignmentsWithEditState.some((assignment) => assignment.arrears_editable),
  });
}

export async function PATCH(request: Request) {
  const auth = await requireApiRole(['landlord', 'caretaker']);
  if (auth.response) return auth.response;
  const scope = await getActivePropertyId(admin, auth.currentUser!.user.id);
  if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
  const { unitIds, error: unitScopeError } = await getPropertyUnitIds(admin, scope.propertyId);
  if (unitScopeError) return NextResponse.json({ error: unitScopeError }, { status: 500 });
  const body = await request.json();
  const updates = Array.isArray(body.updates) ? body.updates : [];
  if (!updates.length) return NextResponse.json({ error: 'No arrears updates supplied.' }, { status: 400 });

  const { data: settings } = await admin.from('property_billing_settings').select('first_online_billing_month').eq('property_id', scope.propertyId).maybeSingle();
  const firstMonth = settings?.first_online_billing_month;
  if (firstMonth && new Date(getInvoiceReleaseAt(firstMonth)) <= new Date()) {
    return NextResponse.json({ error: 'The transition month has ended. Opening arrears are locked for the new billing cycle.' }, { status: 409 });
  }
  for (const update of updates) {
    const assignmentId = String(update.assignmentId || '').trim();
    const amount = Number(update.amount);
    if (!assignmentId || !Number.isFinite(amount) || amount < 0) return NextResponse.json({ error: 'Each arrears update needs a valid assignment and non-negative amount.' }, { status: 400 });

    const { data: currentAssignment, error: assignmentError } = await admin
      .from('unit_assignments')
      .select('unit_id, tenant_id, migration_arrears_consumed_at')
      .eq('id', assignmentId)
      .in('unit_id', unitIds || [])
      .eq('status', 'active')
      .eq('is_migrated', true)
      .single();
    if (assignmentError || !currentAssignment) return NextResponse.json({ error: 'Migration assignment not found.' }, { status: 404 });
    if (currentAssignment.migration_arrears_consumed_at) return NextResponse.json({ error: 'Transition arrears have already been carried into the monthly ledger.' }, { status: 409 });

    if (firstMonth) {
      const { data: releasedInvoice, error: releasedError } = await admin
        .from('invoices')
        .select('id')
        .eq('unit_id', currentAssignment.unit_id)
        .eq('tenant_id', currentAssignment.tenant_id)
        .eq('billing_month', firstMonth)
        .not('published_at', 'is', null)
        .maybeSingle();
      if (releasedError) return NextResponse.json({ error: releasedError.message }, { status: 500 });
      if (releasedInvoice) return NextResponse.json({ error: 'The first invoice is released. Record this payment in the monthly ledger.' }, { status: 409 });
    }
    
    const { error: updateError } = await admin
      .from('unit_assignments')
      .update({ opening_arrears: amount, updated_at: new Date().toISOString() })
      .eq('id', assignmentId)
      .eq('status', 'active');
      
    if (updateError) return NextResponse.json({ error: updateError.message }, { status: 500 });

    if (firstMonth) {
      try {
        await syncDraftInvoiceArrears(admin, firstMonth, currentAssignment.unit_id, currentAssignment.tenant_id, amount);
      } catch (error: any) {
        return NextResponse.json({ error: error.message || 'Failed to sync the draft invoice.' }, { status: 500 });
      }
    }
  }
  return NextResponse.json({ success: true });
}