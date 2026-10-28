import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { generateDraftInvoices } from '../../../../lib/billing-generate';
import { getActivePropertyId } from '../../../../lib/property-access';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

export async function POST(request: Request) {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;
    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const body = await request.json();
    let billingMonth: string;
    if (body.billingMonth) {
      billingMonth = String(body.billingMonth).trim();
    } else {
      const now = new Date();
      const nextMonth = new Date(now.getFullYear(), now.getMonth() + 1, 1);
      billingMonth = nextMonth.toISOString().slice(0, 7);
    }

    const result = await generateDraftInvoices(supabaseAdmin, billingMonth, scope.propertyId);

    return NextResponse.json({
      success: true,
      message: `Generated ${result.invoicesCreated} draft invoice(s) for ${billingMonth}.`,
      billingMonth,
      invoicesCreated: result.invoicesCreated,
      skipped: result.skipped,
    });
  } catch (error: any) {
    console.error('Invoice generation failed:', error);
    return NextResponse.json({ error: error.message || 'Failed to generate invoices.' }, { status: 500 });
  }
}
