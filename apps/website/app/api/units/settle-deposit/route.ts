// app/api/units/settle-deposit/route.ts
import { createClient } from '@supabase/supabase-js';
import { createServerClient } from '@supabase/ssr';
import { NextResponse } from 'next/server';
import { cookies } from 'next/headers';
import { requireApiRole } from '../../../../lib/api-auth';

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

/**
 * POST /api/units/settle-deposit
 *
 * Body: {
 *   unitId: string,
 *   mpesaRef: string,
 *   damages: Array<{ description: string; amount: number }>,
 *   depositHeld: number,
 * }
 *
 * Workflow:
 *  1. Validate the unit is in 'deposit_pending' status
 *  2. Fetch outstanding rent arrears automatically from unpaid invoices
 *  3. Read any overstay penalty or prorated refund credit from the assignment
 *  4. Calculate final net refund:
 *       Net = Deposit + Prorated Credit - Damages - Arrears - Overstay Penalty
 *  5. Record the deposit settlement in deposit_settlements table
 *  6. Mark the unit_assignment as 'ended'
 *  7. Set the unit status to 'vacant'
 */
export async function POST(request: Request) {
  try {
    const auth = await requireApiRole(['landlord']);
    if (auth.response) return auth.response;

    const cookieStore = await cookies();
    const supabase = createServerClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
      {
        cookies: {
          getAll() { return cookieStore.getAll(); },
          setAll() {},
        },
      }
    );

    const { data: { user } } = await supabase.auth.getUser();
    if (!user) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const body = await request.json();
    const unitId = String(body.unitId || '').trim();
    const mpesaRef = String(body.mpesaRef || '').trim().toUpperCase();
    const damages: { description: string; amount: number }[] = body.damages || [];
    const depositHeld = Number(body.depositHeld || 0);

    if (!unitId || !mpesaRef) {
      return NextResponse.json({ error: 'Unit ID and M-Pesa reference are required.' }, { status: 400 });
    }

    // 1. Verify the unit is in deposit_pending status
    const { data: unit, error: unitError } = await supabaseAdmin
      .from('units')
      .select('id, unit_code, status')
      .eq('id', unitId)
      .single();

    if (unitError || !unit) {
      return NextResponse.json({ error: 'Unit not found.' }, { status: 404 });
    }

    if (unit.status !== 'deposit_pending') {
      return NextResponse.json(
        { error: 'Unit must be in deposit_pending status. Offboard the tenant first.' },
        { status: 400 }
      );
    }

    // 2. Get the active/recently-vacated tenant assignment
    //    We query by unit_id without strictly requiring 'active' status
    //    so we can still settle if the assignment was recently updated.
    const { data: assignment, error: assignError } = await supabaseAdmin
      .from('unit_assignments')
      .select('id, tenant_id, overstay_penalty, prorated_refund_amount, evacuation_reason')
      .eq('unit_id', unitId)
      .order('created_at', { ascending: false })
      .limit(1)
      .maybeSingle();

    if (assignError || !assignment) {
      return NextResponse.json({ error: 'No tenant assignment found for this unit.' }, { status: 404 });
    }

    // 3. Fetch outstanding rent arrears from unpaid/partial invoices
    const { data: unpaidInvoices } = await supabaseAdmin
      .from('invoices')
      .select('id, outstanding_balance, billing_month')
      .eq('unit_id', unitId)
      .in('status', ['published', 'draft'])
      .gt('outstanding_balance', 0);

    const outstandingArrears = (unpaidInvoices || []).reduce(
      (sum, inv) => sum + Number(inv.outstanding_balance || 0),
      0
    );

    // 4. Read overstay penalty and prorated refund credit from assignment
    const overstayPenalty = Number(assignment.overstay_penalty || 0);
    const proratedRefundCredit = Number(assignment.prorated_refund_amount || 0);

    // 5. Calculate financials
    //    Net Refund = Deposit + ProratedCredit - Damages - Arrears - OverstayPenalty
    const totalDamages = damages.reduce((sum, d) => sum + Number(d.amount || 0), 0);
    const totalDeductions = totalDamages + outstandingArrears + overstayPenalty;
    const netRefund = Math.max(0, depositHeld + proratedRefundCredit - totalDeductions);

    const now = new Date().toISOString();

    // 6. Record the deposit settlement
    const { error: settlementError } = await supabaseAdmin
      .from('deposit_settlements')
      .insert({
        unit_id: unitId,
        tenant_id: assignment.tenant_id,
        settled_by: user.id,
        deposit_held: depositHeld,
        damage_deductions: totalDamages,
        arrears_deducted: outstandingArrears,
        overstay_penalty: overstayPenalty,
        prorated_refund_credit: proratedRefundCredit,
        net_refund: netRefund,
        mpesa_reference: mpesaRef,
        damages_breakdown: damages,
        evacuation_reason: assignment.evacuation_reason || null,
        settled_at: now,
      });

    if (settlementError) {
      console.warn('deposit_settlements insert failed (table may not exist yet):', settlementError.message);
    }

    // 7. Mark all outstanding invoices as 'settled_by_deposit' so they don't
    //    appear as lingering arrears in the system after the tenant is gone.
    if ((unpaidInvoices || []).length > 0) {
      const invoiceIds = (unpaidInvoices || []).map((inv) => inv.id);
      await supabaseAdmin
        .from('invoices')
        .update({ status: 'settled_by_deposit', updated_at: now })
        .in('id', invoiceIds);
    }

    // 8. Mark the unit_assignment as 'ended'
    await supabaseAdmin
      .from('unit_assignments')
      .update({ status: 'ended', move_out_date: now, updated_at: now })
      .eq('id', assignment.id);

    // 9. Mark the unit as vacant
    const { error: unitUpdateError } = await supabaseAdmin
      .from('units')
      .update({ status: 'vacant', updated_at: now })
      .eq('id', unitId);

    if (unitUpdateError) {
      return NextResponse.json({ error: 'Failed to update unit status.' }, { status: 500 });
    }

    return NextResponse.json({
      success: true,
      message: `Deposit settled for ${unit.unit_code}. Unit is now vacant.`,
      summary: {
        depositHeld,
        proratedRefundCredit,
        totalDamages,
        outstandingArrears,
        overstayPenalty,
        totalDeductions,
        netRefund,
        mpesaRef,
        settledAt: now,
      },
    });
  } catch (error: any) {
    console.error('Deposit settlement failed:', error);
    return NextResponse.json({ error: error.message || 'Failed to settle deposit.' }, { status: 500 });
  }
}
