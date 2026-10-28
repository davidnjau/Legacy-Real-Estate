import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { getActivePropertyId } from '../../../../lib/property-access';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  }
);

export async function POST(request: Request) {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;
    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const body = await request.json();
    const { invoiceId, amount, method, reference } = body;

    if (!invoiceId || typeof amount !== 'number' || amount <= 0) {
      return NextResponse.json({ error: 'Invalid payment parameters' }, { status: 400 });
    }

    // Handle Move-In Settlement payments
    if (String(invoiceId).startsWith('move-in-')) {
      const settlementId = String(invoiceId).replace('move-in-', '');
      const { data: settlement, error: settlementError } = await supabaseAdmin.from('move_in_settlements').select('unit_id').eq('id', settlementId).maybeSingle();
      if (settlementError || !settlement) return NextResponse.json({ error: 'Settlement not found.' }, { status: 404 });
      const { data: scopedUnit } = await supabaseAdmin.from('units').select('id').eq('id', settlement.unit_id).eq('property_id', scope.propertyId).maybeSingle();
      if (!scopedUnit) return NextResponse.json({ error: 'Settlement not found in the active property.' }, { status: 404 });
      const { data: result, error } = await supabaseAdmin.rpc('record_move_in_payment', {
        p_settlement_id: settlementId,
        p_amount: amount,
        p_method: 'manual',
        p_reference: String(reference || '').trim(),
        p_recorded_by: auth.currentUser!.user.id,
        p_checkout_request_id: null,
        p_is_callback: false,
      });
      if (error) throw error;
      if (!result?.applied) return NextResponse.json({ error: result?.reason || 'Payment could not be applied.' }, { status: 409 });
      return NextResponse.json({ success: true, message: 'Payment recorded successfully.' });
    }

    // Handle regular Invoice payments
    const allowedMethods = ['cash', 'bank_transfer', 'mpesa_manual'];
    if (!allowedMethods.includes(String(method))) return NextResponse.json({ error: 'Unsupported payment method.' }, { status: 400 });
    const { data: scopedInvoice } = await supabaseAdmin.from('invoices').select('id').eq('id', invoiceId).eq('property_id', scope.propertyId).maybeSingle();
    if (!scopedInvoice) return NextResponse.json({ error: 'Invoice not found in the active property.' }, { status: 404 });
    const { data: result, error: updateError } = await supabaseAdmin.rpc('record_invoice_payment', {
      p_invoice_id: invoiceId,
      p_amount: amount,
      p_method: method,
      p_reference: String(reference || '').trim(),
      p_recorded_by: auth.currentUser!.user.id,
    });
    if (updateError) throw updateError;
    if (!result?.applied) return NextResponse.json({ error: result?.reason || 'Payment could not be applied.' }, { status: 409 });

    return NextResponse.json({ success: true, message: 'Payment recorded successfully' });

  } catch (error: any) {
    console.error('Manual payment record error:', error);
    return NextResponse.json({ error: error.message || 'Failed to record payment' }, { status: 500 });
  }
}
