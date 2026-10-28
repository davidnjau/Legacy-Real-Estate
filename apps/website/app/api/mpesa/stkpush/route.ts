// app/api/mpesa/stkpush/route.ts
import { NextResponse } from 'next/server';
import axios from 'axios';
import { createClient } from '@supabase/supabase-js';
import { requireApiRole } from '../../../../lib/api-auth';
import { getActivePropertyId } from '../../../../lib/property-access';

const supabaseAdmin = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.SUPABASE_SERVICE_ROLE_KEY!, { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } });

function normalizeKenyanPhone(value: string): string | null {
  const digits = value.replace(/\D/g, '');
  let normalized = digits;

  if (normalized.startsWith('0')) normalized = `254${normalized.slice(1)}`;
  else if (normalized.startsWith('7') || normalized.startsWith('1')) normalized = `254${normalized}`;

  return /^254[17]\d{8}$/.test(normalized) ? normalized : null;
}

async function getDarajaAccessToken() {
  const consumerKey = process.env.MPESA_CONSUMER_KEY;
  const consumerSecret = process.env.MPESA_CONSUMER_SECRET;
  const credentials = Buffer.from(`${consumerKey}:${consumerSecret}`).toString('base64');

  try {
    const res = await axios.get(
      'https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials',
      {
        headers: {
          Authorization: `Basic ${credentials}`,
        },
      }
    );
    console.log('[Daraja] ✅ Access token generated successfully');
    return res.data.access_token;
  } catch (error: any) {
    console.error('[Daraja] ❌ Token generation failed:', error.response?.status, error.response?.data || error.message);
    throw new Error('Failed to authenticate with M-Pesa');
  }
}

export async function POST(request: Request) {
  try {
    const auth = await requireApiRole(['tenant', 'landlord']);
    if (auth.response) return auth.response;

    const { phone, amount: amountValue, invoiceId, transitionAssignmentId, settlementId } = await request.json();
    const amount = Math.round(Number(amountValue));

    if (!Number.isFinite(amount) || amount <= 0) {
      return NextResponse.json({ error: 'A positive payment amount is required.' }, { status: 400 });
    }

    let formattedPhone: string | null = null;
    let accountReference: string;
    let tenantId: string | null = null;
    let transitionAssignment: { id: string; tenant_id: string; opening_arrears: number } | null = null;
    let invoice: { id: string; tenant_id: string; outstanding_balance: number; status: string; publish_at: string | null } | null = null;

    if (settlementId) {
      if (auth.currentUser?.role === 'landlord') {
        const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser.user.id);
        if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });
        const { data: settlementUnit } = await supabaseAdmin.from('move_in_settlements').select('unit_id').eq('id', settlementId).maybeSingle();
        const { data: scopedUnit } = settlementUnit
          ? await supabaseAdmin.from('units').select('id').eq('id', settlementUnit.unit_id).eq('property_id', scope.propertyId).maybeSingle()
          : { data: null };
        if (!scopedUnit) return NextResponse.json({ error: 'Settlement not found in the active property.' }, { status: 404 });
      }
      const { data: settlement } = await supabaseAdmin
        .from('move_in_settlements')
        .select('id, tenant_id, tenant_phone, status, outstanding_balance')
        .eq('id', settlementId)
        .neq('status', 'cancelled')
        .maybeSingle();
      if (!settlement) return NextResponse.json({ error: 'Move-in settlement not found.' }, { status: 404 });
      if (auth.currentUser?.role === 'tenant' && settlement.tenant_id !== auth.currentUser.user.id) {
        return NextResponse.json({ error: 'This settlement does not belong to your account.' }, { status: 403 });
      }
      if (auth.currentUser?.role !== 'tenant' && auth.currentUser?.role !== 'landlord') {
        return NextResponse.json({ error: 'Only the landlord or assigned tenant can pay this settlement.' }, { status: 403 });
      }
      if (amount > Number(settlement.outstanding_balance || 0)) return NextResponse.json({ error: 'Payment exceeds the move-in balance.' }, { status: 400 });
      formattedPhone = normalizeKenyanPhone(String(phone || settlement.tenant_phone || ''));
      accountReference = `MOVEIN-${settlement.id}`;
    } else if (transitionAssignmentId) {
      if (auth.currentUser?.role !== 'tenant') return NextResponse.json({ error: 'Only the tenant can pay transition arrears.' }, { status: 403 });
      const { data: assignment } = await supabaseAdmin
        .from('unit_assignments')
        .select('id, tenant_id, opening_arrears, migration_arrears_consumed_at, is_migrated')
        .eq('id', transitionAssignmentId)
        .eq('tenant_id', auth.currentUser.user.id)
        .eq('status', 'active')
        .maybeSingle();
      if (!assignment || !assignment.is_migrated || assignment.migration_arrears_consumed_at) {
        return NextResponse.json({ error: 'Transition arrears are no longer payable here.' }, { status: 409 });
      }
      if (amount > Number(assignment.opening_arrears || 0)) return NextResponse.json({ error: 'Payment exceeds the transition arrears balance.' }, { status: 400 });

      const { data: tenant } = await supabaseAdmin.from('users').select('phone').eq('id', auth.currentUser.user.id).maybeSingle();
      formattedPhone = normalizeKenyanPhone(String(tenant?.phone || phone || ''));
      tenantId = auth.currentUser.user.id;
      transitionAssignment = assignment;
      accountReference = `ARR-${assignment.id.slice(0, 12)}`;
    } else {
      if (auth.currentUser?.role !== 'tenant' || !invoiceId) {
        return NextResponse.json({ error: 'A tenant invoice or transition balance is required.' }, { status: 400 });
      }
      const { data: invoiceData } = await supabaseAdmin
        .from('invoices')
        .select('id, tenant_id, outstanding_balance, status, publish_at')
        .eq('id', invoiceId)
        .eq('tenant_id', auth.currentUser.user.id)
        .maybeSingle();
      if (!invoiceData) return NextResponse.json({ error: 'Invoice not found for this tenant.' }, { status: 404 });
      if (invoiceData.status === 'draft' || !invoiceData.publish_at || new Date(invoiceData.publish_at) > new Date()) {
        return NextResponse.json({ error: 'This invoice is not available for payment yet.' }, { status: 409 });
      }
      if (amount > Number(invoiceData.outstanding_balance || 0)) return NextResponse.json({ error: 'Payment exceeds the invoice balance.' }, { status: 400 });

      const { data: tenant } = await supabaseAdmin.from('users').select('phone').eq('id', auth.currentUser.user.id).maybeSingle();
      formattedPhone = normalizeKenyanPhone(String(tenant?.phone || ''));
      tenantId = auth.currentUser.user.id;
      invoice = invoiceData;
      accountReference = `INV-${invoiceData.id}`;
    }

    if (!formattedPhone) {
      return NextResponse.json({ error: 'A valid Kenyan M-Pesa phone number is required.' }, { status: 400 });
    }

    const accessToken = await getDarajaAccessToken();
    const timestamp = new Date().toISOString().replace(/[^0-9]/g, '').slice(0, 14);
    const shortcode = process.env.MPESA_SHORTCODE || '174379';
    const passkey = process.env.MPESA_PASSKEY || '';
    const password = Buffer.from(`${shortcode}${passkey}${timestamp}`).toString('base64');

    const payload = {
      BusinessShortCode: shortcode,
      Password: password,
      Timestamp: timestamp,
      TransactionType: 'CustomerPayBillOnline',
      Amount: amount,
      PartyA: formattedPhone,
      PartyB: shortcode,
      PhoneNumber: formattedPhone,
      CallBackURL: process.env.MPESA_CALLBACK_URL,
      AccountReference: accountReference,
      TransactionDesc: 'Monthly Rent & Utility Payment',
    };

    const response = await axios.post(
      'https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest',
      payload,
      {
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
      }
    );

    if (invoice && tenantId && response.data?.CheckoutRequestID) {
      const { error: attemptError } = await supabaseAdmin.from('mpesa_invoice_attempts').insert({
        checkout_request_id: response.data.CheckoutRequestID,
        merchant_request_id: response.data.MerchantRequestID || null,
        invoice_id: invoice.id,
        tenant_id: tenantId,
        requested_amount: amount,
        phone_number: formattedPhone,
      });
      if (attemptError) {
        console.error('[STK Push] Could not persist invoice payment attempt:', attemptError.message);
        return NextResponse.json({ success: false, error: 'Payment was initiated but could not be linked to the invoice. Contact the landlord before retrying.' }, { status: 500 });
      }
    }

    if (transitionAssignment && tenantId && response.data?.CheckoutRequestID) {
      const { error: attemptError } = await supabaseAdmin.from('migration_arrears_payment_attempts').insert({
        checkout_request_id: response.data.CheckoutRequestID,
        merchant_request_id: response.data.MerchantRequestID || null,
        assignment_id: transitionAssignment.id,
        tenant_id: tenantId,
        requested_amount: amount,
        phone_number: formattedPhone,
      });
      if (attemptError) {
        console.error('[STK Push] Could not persist transition payment attempt:', attemptError.message);
        return NextResponse.json({ success: false, error: 'Payment was initiated but could not be linked to the arrears balance. Contact the landlord before retrying.' }, { status: 500 });
      }
    }

    if (settlementId && response.data?.CheckoutRequestID) {
      const { error: attemptError } = await supabaseAdmin.from('move_in_payments').insert({
        settlement_id: settlementId,
        amount,
        method: 'stk_push',
        checkout_request_id: response.data.CheckoutRequestID,
        recorded_by: auth.currentUser!.user.id,
        status: 'pending',
      });
      if (attemptError) {
        console.error('[STK Push] Could not persist move-in payment attempt:', attemptError.message);
        return NextResponse.json({ success: false, error: 'Payment was initiated but could not be linked to the move-in settlement. Contact the landlord before retrying.' }, { status: 500 });
      }
    }

    return NextResponse.json({ success: true, data: response.data });
  } catch (error: any) {
    console.error('[STK Push] ❌ Full error response:', JSON.stringify(error.response?.data, null, 2) || error.message);
    console.error('[STK Push] Error status:', error.response?.status);
    console.error('[STK Push] Error headers:', error.response?.headers);
    return NextResponse.json(
      { success: false, error: error.response?.data || 'Failed to initiate M-Pesa payment' },
      { status: 500 }
    );
  }
}