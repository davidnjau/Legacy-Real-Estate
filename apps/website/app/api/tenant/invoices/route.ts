import { createClient as createSupabaseClient } from '@supabase/supabase-js';
import { createServerClient } from '@supabase/ssr';
import { NextResponse } from 'next/server';
import { cookies } from 'next/headers';
import { publishDraftInvoices } from '../../../../lib/billing-generate';

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

    const { data: activeAssignment } = await supabaseAdmin
      .from('unit_assignments')
      .select('id, unit_id, units!inner(property_id)')
      .eq('tenant_id', tenantId)
      .eq('status', 'active')
      .limit(1)
      .maybeSingle();
    const propertyId = Array.isArray(activeAssignment?.units)
      ? activeAssignment.units[0]?.property_id || null
      : (activeAssignment?.units as any)?.property_id || null;

    const { data: moveInSettlement } = await supabaseAdmin
      .from('move_in_settlements')
      .select('id, status, total_required, total_paid, outstanding_balance, deposit_amount, rent_amount, garbage_amount, water_deposit_amount')
      .eq('tenant_id', tenantId)
      .order('created_at', { ascending: false })
      .limit(1)
      .neq('status', 'cancelled')
      .maybeSingle();

    const { data: tenantProfile } = await supabaseAdmin
      .from('users')
      .select('phone')
      .eq('id', tenantId)
      .maybeSingle();

    const { data: assignment, error: assignmentError } = await supabaseAdmin
      .from('unit_assignments')
      .select('id, unit_id, opening_arrears, migration_arrears_consumed_at, is_migrated, units(unit_code)')
      .eq('tenant_id', tenantId)
      .eq('status', 'active')
      .limit(1)
      .maybeSingle();
    if (assignmentError) return NextResponse.json({ error: assignmentError.message }, { status: 500 });

    const assignedUnit = Array.isArray(assignment?.units) ? assignment.units[0] : assignment?.units;
    const assignmentIsMigrated = Boolean(assignment?.is_migrated);
    const isPreLive = Boolean(assignmentIsMigrated && !assignment?.migration_arrears_consumed_at);
    const { data: migratedAssignments, error: migratedAssignmentsError } = await supabaseAdmin
      .from('unit_assignments')
      .select('id')
      .eq('tenant_id', tenantId)
      .eq('is_migrated', true);
    if (migratedAssignmentsError) return NextResponse.json({ error: migratedAssignmentsError.message }, { status: 500 });
    const migratedAssignmentIds = (migratedAssignments || []).map((item) => item.id);
    const { data: transitionPayments, error: transitionPaymentsError } = migratedAssignmentIds.length
      ? await supabaseAdmin.from('migration_arrears_payments')
          .select('id, amount, mpesa_reference, payment_date, method, created_at')
          .in('assignment_id', migratedAssignmentIds)
          .order('payment_date', { ascending: false })
          .order('created_at', { ascending: false })
      : { data: [], error: null };
    if (transitionPaymentsError) return NextResponse.json({ error: transitionPaymentsError.message }, { status: 500 });

    const { data: billingSettings } = propertyId
      ? await supabaseAdmin
          .from('property_billing_settings')
          .select('paybill_number, account_number, first_online_billing_month')
          .eq('property_id', propertyId)
          .maybeSingle()
      : { data: null };

    const now = new Date().toISOString();
    const dueDraftsQuery = propertyId
      ? supabaseAdmin.from('invoices').select('billing_month').eq('tenant_id', tenantId).eq('property_id', propertyId)
      : supabaseAdmin.from('invoices').select('billing_month').eq('tenant_id', tenantId);

    const { data: dueDrafts, error: dueDraftsError } = await dueDraftsQuery
      .in('status', ['draft', 'partial', 'paid', 'unpaid'])
      .is('published_at', null)
      .lte('publish_at', now);
    if (dueDraftsError) return NextResponse.json({ error: dueDraftsError.message }, { status: 500 });
    for (const month of [...new Set((dueDrafts || []).map((invoice) => invoice.billing_month))]) {
      await publishDraftInvoices(supabaseAdmin, month, propertyId || undefined);
    }

    const scopedInvoiceQuery = propertyId
      ? supabaseAdmin.from('invoices').select('*').eq('tenant_id', tenantId).eq('property_id', propertyId)
      : supabaseAdmin.from('invoices').select('*').eq('tenant_id', tenantId);

    // Only return published invoices whose publish date has passed.
    const { data: invoices, error: invoiceError } = await scopedInvoiceQuery
      .in('status', ['published', 'paid', 'partial'])
      .lte('publish_at', now)
      .order('billing_month', { ascending: false })
      .limit(12);

    if (invoiceError) {
      return NextResponse.json({ error: invoiceError.message }, { status: 500 });
    }

    const [{ data: tenantInvoices, error: tenantInvoicesError }, { data: tenantSettlements, error: tenantSettlementsError }] = await Promise.all([
      propertyId ? supabaseAdmin.from('invoices').select('id, billing_month').eq('tenant_id', tenantId).eq('property_id', propertyId) : supabaseAdmin.from('invoices').select('id, billing_month').eq('tenant_id', tenantId),
      supabaseAdmin.from('move_in_settlements').select('id').eq('tenant_id', tenantId).neq('status', 'cancelled'),
    ]);
    if (tenantInvoicesError || tenantSettlementsError) return NextResponse.json({ error: tenantInvoicesError?.message || tenantSettlementsError?.message }, { status: 500 });
    const allInvoiceIds = (tenantInvoices || []).map((invoice) => invoice.id);
    const settlementIds = (tenantSettlements || []).map((item) => item.id);
    const [{ data: invoicePayments, error: invoicePaymentsError }, { data: moveInPayments, error: moveInPaymentsError }] = await Promise.all([
      allInvoiceIds.length
        ? supabaseAdmin.from('invoice_payments').select('id, invoice_id, amount, method, reference, paid_at').in('invoice_id', allInvoiceIds)
        : Promise.resolve({ data: [], error: null }),
      settlementIds.length
        ? supabaseAdmin.from('move_in_payments').select('id, settlement_id, amount, method, mpesa_reference, created_at').in('settlement_id', settlementIds).eq('status', 'success')
        : Promise.resolve({ data: [], error: null }),
    ]);
    if (invoicePaymentsError || moveInPaymentsError) {
      return NextResponse.json({ error: invoicePaymentsError?.message || moveInPaymentsError?.message }, { status: 500 });
    }

    const invoiceMonthById = new Map((tenantInvoices || []).map((invoice) => [invoice.id, invoice.billing_month]));
    const paymentHistory = [
      ...(invoicePayments || []).map((payment) => ({
        id: payment.id,
        date: payment.paid_at,
        month: String(payment.paid_at || '').slice(0, 7),
        amount: Number(payment.amount || 0),
        reference: payment.reference || '',
        method: payment.method,
        category: 'Monthly invoice',
        invoiceMonth: invoiceMonthById.get(payment.invoice_id) || null,
      })),
      ...(moveInPayments || []).map((payment) => ({
        id: payment.id,
        date: payment.created_at,
        month: String(payment.created_at || '').slice(0, 7),
        amount: Number(payment.amount || 0),
        reference: payment.mpesa_reference || '',
        method: payment.method,
        category: 'Move-in settlement',
        invoiceMonth: null,
      })),
      ...transitionPayments.map((payment) => ({
        id: payment.id,
        date: payment.created_at || payment.payment_date,
        month: String(payment.payment_date || '').slice(0, 7),
        amount: Number(payment.amount || 0),
        reference: payment.mpesa_reference || '',
        method: payment.method,
        category: 'Transition arrears',
        invoiceMonth: null,
      })),
    ].sort((left, right) => right.date.localeCompare(left.date));

    const currentInvoice = invoices?.[0] || null;

    // Format the response
    const formattedInvoices = (invoices || []).map((invoice) => ({
      id: invoice.id,
      month: invoice.billing_month,
      baseRent: Number(invoice.rent_amount || 0),
      garbage: Number(invoice.garbage_amount || 0),
      recurringCharges: (invoice.recurring_charges || []) as { name: string; amount: number }[],
      waterUnits: Number(invoice.water_units || 0),
      waterRate: Number(invoice.water_rate || 0),
      waterTotal: Number(invoice.water_amount || 0),
      priorArrears: Number(invoice.carried_arrears_amount || 0),
      totalDue: Number(invoice.total_amount_due || 0),
      amountPaid: Number(invoice.amount_paid || 0),
      balance: Number(invoice.outstanding_balance || 0),
      status: String(invoice.status || '').toUpperCase(),
      dueDate: invoice.due_date,
      publishedAt: invoice.published_at,
    }));

    return NextResponse.json({
      isPreLive,
      transitionArrears: isPreLive && assignment ? {
        assignmentId: assignment.id,
        unitCode: assignedUnit?.unit_code || null,
        balance: Number(assignment.opening_arrears || 0),
        payments: transitionPayments.map((payment) => ({
          id: payment.id,
          date: payment.payment_date,
          amount: Number(payment.amount || 0),
          reference: payment.mpesa_reference || '',
          method: payment.method,
        })),
      } : null,
      current: currentInvoice
        ? {
            id: currentInvoice.id,
            month: currentInvoice.billing_month,
            baseRent: Number(currentInvoice.rent_amount || 0),
            garbage: Number(currentInvoice.garbage_amount || 0),
            recurringCharges: (currentInvoice.recurring_charges || []) as { name: string; amount: number }[],
            waterUnits: Number(currentInvoice.water_units || 0),
            waterRate: Number(currentInvoice.water_rate || 0),
            waterTotal: Number(currentInvoice.water_amount || 0),
            priorArrears: Number(currentInvoice.carried_arrears_amount || 0),
            totalDue: Number(currentInvoice.total_amount_due || 0),
            amountPaid: Number(currentInvoice.amount_paid || 0),
            balance: Number(currentInvoice.outstanding_balance || 0),
            status: String(currentInvoice.status || '').toUpperCase(),
            dueDate: currentInvoice.due_date,
            publishedAt: currentInvoice.published_at,
          }
        : null,
      history: formattedInvoices.slice(1),
      paymentHistory,
      tenantPhone: tenantProfile?.phone || null,
      moveInSettlement: moveInSettlement ? {
        status: moveInSettlement.status,
        totalRequired: Number(moveInSettlement.total_required || 0),
        totalPaid: Number(moveInSettlement.total_paid || 0),
        outstandingBalance: Number(moveInSettlement.outstanding_balance || 0),
        deposit: Number(moveInSettlement.deposit_amount || 0),
        rent: Number(moveInSettlement.rent_amount || 0),
        garbage: Number(moveInSettlement.garbage_amount || 0),
        waterDeposit: Number(moveInSettlement.water_deposit_amount || 0),
      } : null,
      transitionSettings: billingSettings || null,
    });
  } catch (error: any) {
    console.error('Failed to fetch tenant invoices:', error);
    return NextResponse.json({ error: 'Failed to fetch invoices.' }, { status: 500 });
  }
}
