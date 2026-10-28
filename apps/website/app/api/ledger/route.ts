import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../lib/api-auth';
import { getInvoiceReleaseAt } from '../../../lib/billing-schedule';
import { publishDraftInvoices } from '../../../lib/billing-generate';
import { getActivePropertyId } from '../../../lib/property-access';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  }
);

export async function GET(request: Request) {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;
    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const { searchParams } = new URL(request.url);
    const requestedMonth = searchParams.get('month');
    
    // Default to the current calendar month if none is provided
    const month = requestedMonth || new Date().toISOString().slice(0, 7);
    const now = new Date();
    const releaseAt = getInvoiceReleaseAt(month);
    const invoiceIsReleased = new Date(releaseAt) <= now;

    if (invoiceIsReleased) {
      await publishDraftInvoices(supabaseAdmin, month, scope.propertyId);
    }

    // Fetch invoices for the requested month
    const { data: invoices, error: invoicesError } = await supabaseAdmin
      .from('invoices')
      .select('*')
      .eq('billing_month', month)
      .eq('property_id', scope.propertyId)
      .in('status', ['published', 'paid', 'partial', 'unpaid'])
      .lte('publish_at', now.toISOString());

    if (invoicesError) {
      return NextResponse.json({ error: invoicesError.message }, { status: 500 });
    }

    const invoiceIds = (invoices || []).map((invoice) => invoice.id);
    const { data: invoicePayments, error: invoicePaymentError } = invoiceIds.length
      ? await supabaseAdmin.from('invoice_payments')
          .select('id, invoice_id, amount, method, reference, paid_at')
          .in('invoice_id', invoiceIds)
          .order('paid_at', { ascending: false })
      : { data: [], error: null };
    if (invoicePaymentError) return NextResponse.json({ error: invoicePaymentError.message }, { status: 500 });
    const invoicePaymentsById = new Map<string, any[]>();
    for (const payment of invoicePayments || []) {
      const payments = invoicePaymentsById.get(payment.invoice_id) || [];
      payments.push(payment);
      invoicePaymentsById.set(payment.invoice_id, payments);
    }

    const { data: moveInSettlements, error: settlementError } = await supabaseAdmin
      .from('move_in_settlements')
      .select('id, unit_id, tenant_id, total_required, total_paid, outstanding_balance, status, claim_code_issued_at, units(unit_code), users(full_name)')
      .in('unit_id', (await supabaseAdmin.from('units').select('id').eq('property_id', scope.propertyId)).data?.map((unit) => unit.id) || [])
      .not('claim_code_issued_at', 'is', null)
      .neq('status', 'cancelled');
    if (settlementError) return NextResponse.json({ error: settlementError.message }, { status: 500 });

    const settlementIds = (moveInSettlements || []).map((settlement) => settlement.id);
    const { data: moveInPayments, error: moveInPaymentError } = settlementIds.length
      ? await supabaseAdmin.from('move_in_payments')
          .select('id, settlement_id, amount, method, mpesa_reference, created_at, status')
          .in('settlement_id', settlementIds)
          .eq('status', 'success')
          .order('created_at', { ascending: false })
      : { data: [], error: null };
    if (moveInPaymentError) return NextResponse.json({ error: moveInPaymentError.message }, { status: 500 });
    const moveInPaymentsBySettlement = new Map<string, any[]>();
    for (const payment of moveInPayments || []) {
      const payments = moveInPaymentsBySettlement.get(payment.settlement_id) || [];
      payments.push(payment);
      moveInPaymentsBySettlement.set(payment.settlement_id, payments);
    }

    let totalNewCharges = 0;
    let totalArrearsBroughtIn = 0;
    let totalExpected = 0;
    let totalPaid = 0;
    let totalBalance = 0;

    const ledgerRows = (invoices || []).map((invoice) => {
      // Calculate individual invoice metrics
      const rent = Number(invoice.rent_amount || 0);
      const garbage = Number(invoice.garbage_amount || 0);
      const water = Number(invoice.water_amount || 0);
      
      const newCharges = rent + garbage + water;
      const priorArrears = Number(invoice.carried_arrears_amount || 0);
      const expected = Number(invoice.total_amount_due || 0); // Should equal newCharges + priorArrears
      const paid = Number(invoice.amount_paid || 0);
      const balance = Number(invoice.outstanding_balance || 0);

      // Add to running totals
      totalNewCharges += newCharges;
      totalArrearsBroughtIn += priorArrears;
      totalExpected += expected;
      totalPaid += paid;
      totalBalance += balance;

      return {
        id: invoice.id,
        unitId: invoice.unit_id,
        tenantId: invoice.tenant_id,
        newCharges,
        priorArrears,
        expected,
        paid,
        balance,
        status: invoice.status,
        paymentHistory: invoicePaymentsById.get(invoice.id) || [],
        unitCode: '',
        tenantName: '',
      };
    });

    // Enrich rows with Unit Codes and Tenant Names
    for (const row of ledgerRows) {
      if (row.unitId) {
        const { data: unit } = await supabaseAdmin.from('units').select('unit_code').eq('id', row.unitId).single();
        row.unitCode = unit?.unit_code || '-';
      }
      
      if (row.tenantId) {
        const { data: user } = await supabaseAdmin.from('users').select('full_name').eq('id', row.tenantId).single();
        row.tenantName = user?.full_name || 'Unassigned / System';
      } else {
        row.tenantName = '-';
      }
    }

    // Sort rows alphabetically by unitCode
    ledgerRows.sort((a, b) => a.unitCode.localeCompare(b.unitCode));

    const moveInLedger = (moveInSettlements || []).map((item: any) => ({
      id: `move-in-${item.id}`,
      unitId: item.unit_id,
      tenantId: item.tenant_id,
      newCharges: Number(item.total_required || 0),
      priorArrears: 0,
      expected: Number(item.total_required || 0),
      paid: Number(item.total_paid || 0),
      balance: Number(item.outstanding_balance || 0),
      status: `move_in_${item.status}`,
      paymentHistory: (moveInPaymentsBySettlement.get(item.id) || []).map((payment) => ({
        id: payment.id,
        date: payment.created_at,
        amount: Number(payment.amount || 0),
        reference: payment.mpesa_reference || '',
        method: payment.method,
      })),
      payments: moveInPaymentsBySettlement.get(item.id) || [],
      unitCode: Array.isArray(item.units) ? item.units[0]?.unit_code || '-' : item.units?.unit_code || '-',
      tenantName: Array.isArray(item.users) ? item.users[0]?.full_name || item.tenant_id || '-' : item.users?.full_name || item.tenant_id || '-',
    }));

    return NextResponse.json({
      month,
      releaseAt,
      invoiceIsReleased,
      summary: {
        totalNewCharges,
        totalArrearsBroughtIn,
        totalExpected,
        totalPaid,
        totalBalance,
        moveInRequired: (moveInSettlements || []).reduce((sum, item) => sum + Number(item.total_required || 0), 0),
        moveInPaid: (moveInSettlements || []).reduce((sum, item) => sum + Number(item.total_paid || 0), 0),
        moveInBalance: (moveInSettlements || []).reduce((sum, item) => sum + Number(item.outstanding_balance || 0), 0),
      },
      ledger: ledgerRows,
      moveInLedger,
    });

  } catch (error: any) {
    console.error('Ledger fetch failed:', error);
    return NextResponse.json({ error: 'Failed to fetch ledger data.' }, { status: 500 });
  }
}
