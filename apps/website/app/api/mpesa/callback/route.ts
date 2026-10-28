// app/api/mpesa/callback/route.ts
import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';

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

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const stkCallback = body.Body?.stkCallback;

    if (!stkCallback) {
      return NextResponse.json({ error: 'Invalid callback payload' }, { status: 400 });
    }

    const resultCode = stkCallback.ResultCode;
    const merchantRequestID = stkCallback.MerchantRequestID;
    const checkoutRequestID = stkCallback.CheckoutRequestID;

    if (resultCode === 0) {
      // Payment Successful
      const callbackMetadata = stkCallback.CallbackMetadata?.Item;
      let amountPaid = 0;
      let mpesaReceiptNumber = '';
      let transactionDate = '';
      let phoneNumber = '';

      callbackMetadata?.forEach((item: any) => {
        if (item.Name === 'Amount') amountPaid = item.Value;
        if (item.Name === 'MpesaReceiptNumber') mpesaReceiptNumber = item.Value;
        if (item.Name === 'TransactionDate') transactionDate = String(item.Value);
        if (item.Name === 'PhoneNumber') phoneNumber = String(item.Value);
      });

      const settlementIdFromReference = typeof stkCallback.AccountReference === 'string' && stkCallback.AccountReference.startsWith('MOVEIN-')
        ? stkCallback.AccountReference.slice('MOVEIN-'.length)
        : null;
      const { data: pendingMoveInPayment } = await supabaseAdmin
        .from('move_in_payments')
        .select('id, settlement_id, recorded_by, status')
        .eq('checkout_request_id', checkoutRequestID)
        .limit(1)
        .maybeSingle();
      const settlementId = settlementIdFromReference || pendingMoveInPayment?.settlement_id || null;
      const { data: transitionAttempt } = checkoutRequestID
        ? await supabaseAdmin.from('migration_arrears_payment_attempts')
            .select('assignment_id, tenant_id')
            .eq('checkout_request_id', checkoutRequestID)
            .maybeSingle()
        : { data: null };

      if (settlementId) {
        if (!pendingMoveInPayment) {
          console.error('Move-in M-Pesa callback has no matching pending payment record:', checkoutRequestID);
        } else {
          const { data: result, error: applyError } = await supabaseAdmin.rpc('record_move_in_payment', {
            p_settlement_id: settlementId,
            p_amount: amountPaid,
            p_method: 'stk_push',
            p_reference: mpesaReceiptNumber,
            p_recorded_by: pendingMoveInPayment.recorded_by,
            p_checkout_request_id: checkoutRequestID,
            p_is_callback: true,
          });
          if (applyError) throw applyError;
          if (!result?.applied && !result?.duplicate) {
            console.error('Move-in payment requires reconciliation:', result?.reason || 'unknown');
          }
        }
      }

      // 1. Record payment in database
      const { error: paymentError } = await supabaseAdmin.from('payments').insert({
        checkout_request_id: checkoutRequestID,
        merchant_request_id: merchantRequestID,
        mpesa_receipt: mpesaReceiptNumber,
        amount: amountPaid,
        phone_number: phoneNumber,
        transaction_date: transactionDate,
        status: 'SUCCESS',
      });

      if (paymentError) {
        console.error('Failed to log payment in database:', paymentError);
      }

      // Apply recurring invoice payments by their STK request, not by phone recency.
      if (!settlementId && transitionAttempt) {
        const { data: result, error: applyError } = await supabaseAdmin.rpc('record_migration_arrears_payment', {
          p_assignment_id: transitionAttempt.assignment_id,
          p_amount: amountPaid,
          p_reference: mpesaReceiptNumber,
          p_payment_date: new Date().toISOString().slice(0, 10),
          p_recorded_by: transitionAttempt.tenant_id,
          p_method: 'stk_push',
          p_checkout_request_id: checkoutRequestID,
          p_mpesa_receipt: mpesaReceiptNumber,
          p_phone_number: phoneNumber,
          p_transaction_date: transactionDate,
        });
        if (applyError) throw applyError;
        if (!result?.applied && !result?.duplicate) {
          console.error('Transition payment requires reconciliation:', result?.reason || 'unknown');
        }
      } else if (!settlementId) {
        const { data: result, error: applyError } = await supabaseAdmin.rpc('apply_mpesa_invoice_payment', {
          p_checkout_request_id: checkoutRequestID,
          p_mpesa_receipt: mpesaReceiptNumber,
          p_amount: amountPaid,
          p_phone_number: phoneNumber,
          p_transaction_date: transactionDate,
        });
        if (applyError) throw applyError;
        if (result?.applied) {
          console.log(`Invoice ${result.invoice_id} updated from M-Pesa callback.`);
        } else {
          console.warn('M-Pesa callback was not applied to an invoice:', result?.reason || 'duplicate');
        }
      }
    } else {
      // Payment Failed or Cancelled by User
      if (checkoutRequestID) {
        await supabaseAdmin.from('move_in_payments')
          .update({ status: 'failed', result_description: stkCallback.ResultDesc, processed_at: new Date().toISOString() })
          .eq('checkout_request_id', checkoutRequestID)
          .eq('status', 'pending');
      }
      if (checkoutRequestID) {
        await supabaseAdmin.from('migration_arrears_payment_attempts')
          .update({ status: 'failed', result_description: stkCallback.ResultDesc, processed_at: new Date().toISOString() })
          .eq('checkout_request_id', checkoutRequestID)
          .eq('status', 'pending');
      }
      if (checkoutRequestID) {
        await supabaseAdmin.from('mpesa_invoice_attempts')
          .update({ status: 'failed', result_description: stkCallback.ResultDesc, processed_at: new Date().toISOString() })
          .eq('checkout_request_id', checkoutRequestID)
          .eq('status', 'pending');
      }
      await supabaseAdmin.from('payments').insert({
        checkout_request_id: checkoutRequestID,
        merchant_request_id: merchantRequestID,
        status: 'FAILED',
        result_desc: stkCallback.ResultDesc,
      });
    }

    return NextResponse.json({ ResultCode: 0, ResultDesc: 'Accepted' });
  } catch (error) {
    console.error('M-Pesa Callback Error:', error);
    return NextResponse.json({ error: 'Internal Server Error' }, { status: 500 });
  }
}