import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { getActivePropertyId } from '../../../../lib/property-access';
import { getInvoiceReleaseAt } from '../../../../lib/billing-schedule';

const admin = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.SUPABASE_SERVICE_ROLE_KEY!, { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } });

export async function POST(request: Request) {
  const auth = await requireApiRole(['landlord', 'caretaker']);
  if (auth.response) return auth.response;
  const scope = await getActivePropertyId(admin, auth.currentUser!.user.id);
  if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
  const body = await request.json();
  const assignmentId = String(body.assignmentId || '').trim();
  const amount = Number(body.amount);
  const reference = String(body.mpesaReference || '').trim().toUpperCase() || null;
  const paymentDate = String(body.paymentDate || '').trim();

  if (!assignmentId || !Number.isFinite(amount) || amount <= 0 || !/^\d{4}-\d{2}-\d{2}$/.test(paymentDate)) {
    return NextResponse.json({ error: 'Assignment, positive amount, and valid payment date are required.' }, { status: 400 });
  }

  const { data: assignment, error: assignmentError } = await admin
    .from('unit_assignments')
    .select('id, unit_id, tenant_id, opening_arrears, migration_arrears_consumed_at, is_migrated')
    .eq('id', assignmentId)
    .eq('status', 'active')
    .eq('is_migrated', true)
    .single();
  if (assignmentError || !assignment || !assignment.is_migrated) return NextResponse.json({ error: 'Migration assignment not found.' }, { status: 404 });

  const { data: scopedUnit, error: scopedUnitError } = await admin.from('units').select('id').eq('id', assignment.unit_id).eq('property_id', scope.propertyId).maybeSingle();
  if (scopedUnitError || !scopedUnit) return NextResponse.json({ error: 'Migration assignment not found in the active property.' }, { status: 404 });

  const { data: settings } = await admin.from('property_billing_settings').select('first_online_billing_month').eq('property_id', scope.propertyId).maybeSingle();
  if (settings?.first_online_billing_month) {
    if (new Date(getInvoiceReleaseAt(settings.first_online_billing_month)) <= new Date()) {
      return NextResponse.json({ error: 'The transition month has ended. Record this payment in the monthly ledger.' }, { status: 409 });
    }
    const { data: releasedInvoice, error: releasedError } = await admin
      .from('invoices')
      .select('id')
      .eq('unit_id', assignment.unit_id)
      .eq('tenant_id', assignment.tenant_id)
      .eq('billing_month', settings.first_online_billing_month)
      .not('published_at', 'is', null)
      .maybeSingle();
    if (releasedError) return NextResponse.json({ error: releasedError.message }, { status: 500 });
    if (releasedInvoice) return NextResponse.json({ error: 'The first invoice is released. Record this payment in the monthly ledger.' }, { status: 409 });
  }
  if (assignment.migration_arrears_consumed_at) return NextResponse.json({ error: 'Transition arrears have been carried into the monthly ledger.' }, { status: 409 });

  const currentBalance = Number(assignment.opening_arrears || 0);
  if (amount > currentBalance) return NextResponse.json({ error: `Payment cannot exceed the current arrears balance of ${currentBalance}.` }, { status: 400 });

  const { data: result, error: recordError } = await admin.rpc('record_migration_arrears_payment', {
    p_assignment_id: assignmentId,
    p_amount: amount,
    p_reference: reference,
    p_payment_date: paymentDate,
    p_recorded_by: auth.currentUser!.user.id,
    p_method: 'manual',
  });
  if (recordError) return NextResponse.json({ error: recordError.message }, { status: 500 });
  if (!result?.applied) return NextResponse.json({ error: result?.reason || 'Payment could not be applied.' }, { status: 409 });

  return NextResponse.json({ success: true, remainingArrears: Number(result.remaining_arrears ?? Math.max(0, currentBalance - amount)) });
}