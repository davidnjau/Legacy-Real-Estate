import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
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

// The day-of-month tenants must vacate by to avoid overstay charges.
// Matches the rent payment grace period deadline.
const GRACE_PERIOD_DAY = 10;

export async function POST(request: Request) {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;

    const body = await request.json();
    const unitId = String(body.unitId || '').trim();
    const action = String(body.action || '').trim();

    if (!unitId || !action) {
      return NextResponse.json({ error: 'Unit ID and action are required.' }, { status: 400 });
    }

    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
    const { data: scopedUnit, error: scopeError } = await supabaseAdmin.from('units')
      .select('id')
      .eq('id', unitId)
      .eq('property_id', scope.propertyId)
      .maybeSingle();
    if (scopeError) return NextResponse.json({ error: scopeError.message }, { status: 500 });
    if (!scopedUnit) return NextResponse.json({ error: 'Unit not found in the active property.' }, { status: 404 });

    const validActions = [
      'log_notice',
      'revoke_notice',
      'offboard_tenant',
      'immediate_evacuation',
      'mark_maintenance',
      'mark_vacant',
    ];
    if (!validActions.includes(action)) {
      return NextResponse.json({ error: 'Invalid action.' }, { status: 400 });
    }

    const now = new Date();
    const nowIso = now.toISOString();

    // ─────────────────────────────────────────────────────────────────────────
    // ACTION: log_notice
    // Records that a tenant has given a 1-month move-out notice.
    // Sets expected_move_out_date to the 10th of NEXT month (grace deadline).
    // Changes unit status to 'notice_given'.
    // ─────────────────────────────────────────────────────────────────────────
    if (action === 'log_notice') {
      // Calculate the 10th of next month as the move-out deadline
      const nextMonth = new Date(now.getFullYear(), now.getMonth() + 1, GRACE_PERIOD_DAY);
      const expectedMoveOutDate = nextMonth.toISOString().slice(0, 10);

      // Find the active assignment
      const { data: assignment } = await supabaseAdmin
        .from('unit_assignments')
        .select('id')
        .eq('unit_id', unitId)
        .eq('status', 'active')
        .maybeSingle();

      if (!assignment) {
        return NextResponse.json({ error: 'No active tenant assignment found.' }, { status: 404 });
      }

      // Update assignment with expected move-out date
      await supabaseAdmin
        .from('unit_assignments')
        .update({ expected_move_out_date: expectedMoveOutDate })
        .eq('id', assignment.id);

      // Update unit status to notice_given
      const { data: updatedUnit, error: updateError } = await supabaseAdmin
        .from('units')
        .update({ status: 'notice_given' })
        .eq('id', unitId)
        .select()
        .single();

      if (updateError) {
        return NextResponse.json({ error: 'Failed to update unit status.' }, { status: 500 });
      }

      return NextResponse.json({
        success: true,
        unit: updatedUnit,
        expectedMoveOutDate,
        message: `Move-out notice logged. Tenant must vacate by ${expectedMoveOutDate}.`,
      });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // ACTION: revoke_notice
    // Cancels a previously logged move-out notice. Returns unit to 'occupied'.
    // ─────────────────────────────────────────────────────────────────────────
    if (action === 'revoke_notice') {
      const { data: assignment } = await supabaseAdmin
        .from('unit_assignments')
        .select('id')
        .eq('unit_id', unitId)
        .eq('status', 'active')
        .maybeSingle();

      if (assignment) {
        await supabaseAdmin
          .from('unit_assignments')
          .update({ expected_move_out_date: null })
          .eq('id', assignment.id);
      }

      const { data: updatedUnit } = await supabaseAdmin
        .from('units')
        .update({ status: 'occupied' })
        .eq('id', unitId)
        .select()
        .single();

      return NextResponse.json({
        success: true,
        unit: updatedUnit,
        message: 'Move-out notice has been revoked. Unit is now active.',
      });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // ACTION: offboard_tenant
    // Records physical move-out (tenant has handed over keys).
    // Detects if they have overstayed beyond the grace period deadline.
    // Moves unit to 'deposit_pending' (separate deposit settlement step).
    // Body params:
    //   overstayPenalty?: number  – Landlord-negotiated penalty for overstaying
    // ─────────────────────────────────────────────────────────────────────────
    if (action === 'offboard_tenant') {
      const overstayPenalty = Number(body.overstayPenalty || 0);

      const { data: assignment } = await supabaseAdmin
        .from('unit_assignments')
        .select('id, expected_move_out_date')
        .eq('unit_id', unitId)
        .eq('status', 'active')
        .maybeSingle();

      if (!assignment) {
        return NextResponse.json({ error: 'No active tenant assignment found.' }, { status: 404 });
      }

      const todayStr = now.toISOString().slice(0, 10);

      // Detect overstay: if today is past expected move-out date, flag it
      let isOverstay = false;
      if (assignment.expected_move_out_date && todayStr > assignment.expected_move_out_date) {
        isOverstay = true;
      }

      await supabaseAdmin
        .from('unit_assignments')
        .update({
          actual_move_out_date: todayStr,
          // Store overstay penalty in the assignment for deposit settlement to pick up
          ...(overstayPenalty > 0 ? { overstay_penalty: overstayPenalty } : {}),
        })
        .eq('id', assignment.id);

      const { data: updatedUnit, error: updateError } = await supabaseAdmin
        .from('units')
        .update({ status: 'deposit_pending' })
        .eq('id', unitId)
        .select()
        .single();

      if (updateError) {
        return NextResponse.json({ error: 'Failed to update unit status.' }, { status: 500 });
      }

      return NextResponse.json({
        success: true,
        unit: updatedUnit,
        isOverstay,
        message: isOverstay
          ? `Tenant has been offboarded (overstayed). Penalty of KES ${overstayPenalty.toLocaleString()} will be applied during deposit settlement.`
          : 'Tenant has been offboarded successfully. Proceed to settle the deposit.',
      });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // ACTION: immediate_evacuation
    // Bypasses the notice period. Instantly records move-out.
    // Can optionally void the current month's invoice OR record a prorated
    // rent credit to be applied during deposit settlement.
    // Body params:
    //   reason: string            – Why the tenant was immediately evacuated
    //   cancelCurrentInvoice?: boolean  – Void the current draft/published invoice
    //   proratedRefundAmount?: number   – Credit amount to carry into deposit settlement
    // ─────────────────────────────────────────────────────────────────────────
    if (action === 'immediate_evacuation') {
      const reason = String(body.reason || '').trim();
      const cancelCurrentInvoice = Boolean(body.cancelCurrentInvoice);
      const proratedRefundAmount = Number(body.proratedRefundAmount || 0);

      if (!reason) {
        return NextResponse.json({ error: 'A reason for immediate evacuation is required.' }, { status: 400 });
      }

      const { data: assignment } = await supabaseAdmin
        .from('unit_assignments')
        .select('id')
        .eq('unit_id', unitId)
        .eq('status', 'active')
        .maybeSingle();

      if (!assignment) {
        return NextResponse.json({ error: 'No active tenant assignment found.' }, { status: 404 });
      }

      const todayStr = now.toISOString().slice(0, 10);
      const currentBillingMonth = now.toISOString().slice(0, 7); // YYYY-MM

      // Optionally void the current month's invoice
      if (cancelCurrentInvoice) {
        await supabaseAdmin
          .from('invoices')
          .update({ status: 'cancelled' })
          .eq('unit_id', unitId)
          .eq('billing_month', currentBillingMonth)
          .in('status', ['draft', 'published']);
      }

      // Record the move-out with evacuation reason and any prorated credit
      await supabaseAdmin
        .from('unit_assignments')
        .update({
          actual_move_out_date: todayStr,
          expected_move_out_date: todayStr,
          evacuation_reason: reason,
          prorated_refund_amount: proratedRefundAmount > 0 ? proratedRefundAmount : null,
        })
        .eq('id', assignment.id);

      const { data: updatedUnit, error: updateError } = await supabaseAdmin
        .from('units')
        .update({ status: 'deposit_pending' })
        .eq('id', unitId)
        .select()
        .single();

      if (updateError) {
        return NextResponse.json({ error: 'Failed to update unit status.' }, { status: 500 });
      }

      return NextResponse.json({
        success: true,
        unit: updatedUnit,
        invoiceCancelled: cancelCurrentInvoice,
        message: `Tenant has been immediately evacuated. Reason: ${reason}. Proceed to settle the deposit.`,
      });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // LEGACY ACTIONS: mark_maintenance, mark_vacant
    // ─────────────────────────────────────────────────────────────────────────
    let nextStatus: string;
    if (action === 'mark_maintenance') nextStatus = 'maintenance';
    else nextStatus = 'vacant';

    const { data: updatedUnit, error: updateError } = await supabaseAdmin
      .from('units')
      .update({ status: nextStatus })
      .eq('id', unitId)
      .select()
      .single();

    if (updateError || !updatedUnit) {
      return NextResponse.json({ error: 'Unable to update unit status.' }, { status: 500 });
    }

    return NextResponse.json({ success: true, unit: updatedUnit });
  } catch (error: any) {
    console.error('Unit action failed:', error);
    return NextResponse.json({ error: error.message || 'Unable to update unit.' }, { status: 500 });
  }
}
