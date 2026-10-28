import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../lib/api-auth';
import { isWaterReminderDue } from '../../../lib/billing-schedule';
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

    const currentMonth = new Date().toISOString().slice(0, 7);

    const scopedUnits = supabaseAdmin.from('units').select('*').eq('property_id', scope.propertyId);
    const { data: propertyUnits, error: propertyUnitsError } = await scopedUnits;

    if (propertyUnitsError) return NextResponse.json({ error: propertyUnitsError.message }, { status: 500 });
    const unitIds = (propertyUnits || []).map((unit) => unit.id);

    const { data: invoices, error: invoicesError } = await supabaseAdmin
      .from('invoices')
      .select('*')
      .eq('billing_month', currentMonth)
      .eq('property_id', scope.propertyId);

    if (invoicesError) {
      return NextResponse.json({ error: invoicesError.message }, { status: 500 });
    }

    const { data: moveInSettlements, error: settlementError } = unitIds.length ? await supabaseAdmin
      .from('move_in_settlements')
      .select('total_paid, outstanding_balance, status')
      .in('unit_id', unitIds) : { data: [], error: null };

    if (settlementError) return NextResponse.json({ error: settlementError.message }, { status: 500 });

    const units = propertyUnits || [];
    const totalUnits = units.length;
    const occupiedUnits = units.filter((unit) => unit.status === 'occupied').length;

    const totalExpectedRevenue = (invoices || []).reduce((sum, invoice) => sum + Number(invoice.total_amount_due || 0), 0);
    const totalCollected = (invoices || []).reduce((sum, invoice) => sum + Number(invoice.amount_paid || 0), 0);
    const outstandingArrears = (invoices || []).reduce((sum, invoice) => sum + Number(invoice.outstanding_balance || 0), 0);
    const moveInCollected = (moveInSettlements || []).reduce((sum, settlement) => sum + Number(settlement.total_paid || 0), 0);
    const moveInOutstanding = (moveInSettlements || []).filter((settlement) => settlement.status !== 'cancelled').reduce((sum, settlement) => sum + Number(settlement.outstanding_balance || 0), 0);

    const ledger = (invoices || []).map((invoice) => {
      const rent = Number(invoice.rent_amount || 0);
      const garbage = Number(invoice.garbage_amount || 0);
      const water = Number(invoice.water_amount || 0);
      
      const newCharges = rent + garbage + water;
      const priorArrears = Number(invoice.carried_arrears_amount || 0);
      const expected = Number(invoice.total_amount_due || 0);
      const paid = Number(invoice.amount_paid || 0);
      const balance = Number(invoice.outstanding_balance || 0);

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
        unitCode: '',
        tenantName: '',
      };
    });

    for (const row of ledger) {
      const { data: unit } = await supabaseAdmin.from('units').select('unit_code').eq('id', row.unitId).single();
      const { data: user } = await supabaseAdmin.from('users').select('full_name').eq('id', row.tenantId).single();
      row.unitCode = unit?.unit_code || '-';
      row.tenantName = user?.full_name || 'Unassigned / System';
    }

    const { data: pendingMaintenance } = unitIds.length ? await supabaseAdmin
      .from('maintenance_tickets')
      .select('id')
      .in('unit_id', unitIds)
      .neq('status', 'resolved') : { data: [] };

    const offboardingUnits = units?.filter((unit) => unit.status === 'offboarding').length || 0;

    const today = new Date();
    const lastDayOfMonth = new Date(today.getFullYear(), today.getMonth() + 1, 0);
    const daysUntilEnd = lastDayOfMonth.getDate() - today.getDate();
    const hasPublishedInvoices = (invoices || []).some(inv => ['published', 'paid', 'partial'].includes(inv.status));
    const isWaterReadingWindow = isWaterReminderDue() && !hasPublishedInvoices;

    return NextResponse.json({
      currentMonth,
      summary: {
        totalExpectedRevenue,
        totalCollected,
        moveInCollected,
        moveInOutstanding,
        totalCollectedIncludingMoveIn: totalCollected + moveInCollected,
        outstandingArrears,
        occupancyRate: totalUnits ? (occupiedUnits / totalUnits) * 100 : 0,
        occupiedUnits,
        totalUnits,
      },
      pendingActionsData: {
        maintenanceCount: pendingMaintenance?.length || 0,
        offboardingCount: offboardingUnits,
        isWaterReadingWindow,
      },
      ledger,
    });
  } catch (error: any) {
    console.error('Dashboard fetch failed:', error);
    return NextResponse.json({ error: 'Failed to load dashboard data.' }, { status: 500 });
  }
}
