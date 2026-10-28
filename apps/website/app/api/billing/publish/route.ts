// app/api/billing/publish/route.ts
import { NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';
import { requireApiRole } from '../../../../lib/api-auth';
import { getInvoiceReleaseAt } from '../../../../lib/billing-schedule';
import { publishDraftInvoices } from '../../../../lib/billing-generate';
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

/**
 * POST /api/billing/publish
 *
 * Flips all DRAFT invoices for the given billing month to 'published'.
 * The 'published_at' timestamp is set to midnight of the 1st of that month,
 * meaning tenants will only see the invoice once the month begins.
 *
 * Body: { billingMonth: "YYYY-MM" }
 */
export async function POST(request: Request) {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;
    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const body = await request.json();
    const billingMonth = String(body.billingMonth || '').trim();

    if (!billingMonth || !/^\d{4}-\d{2}$/.test(billingMonth)) {
      return NextResponse.json(
        { error: 'A valid billingMonth in the format YYYY-MM is required.' },
        { status: 400 }
      );
    }

    const publishedAt = getInvoiceReleaseAt(billingMonth);
    if (new Date(publishedAt) > new Date()) {
      return NextResponse.json({ error: `Invoices for ${billingMonth} cannot be published before ${publishedAt}.` }, { status: 409 });
    }

    const invoicesPublished = await publishDraftInvoices(supabaseAdmin, billingMonth, scope.propertyId);
    if (!invoicesPublished) return NextResponse.json({ error: `No unpublished invoices found for ${billingMonth}.` }, { status: 404 });

    return NextResponse.json({
      success: true,
      message: `Published ${invoicesPublished} invoice(s) for ${billingMonth}. Tenants will see them from ${publishedAt}.`,
      billingMonth,
      invoicesPublished,
    });
  } catch (error: any) {
    console.error('Invoice Publishing Error:', error);
    return NextResponse.json(
      { error: error.message || 'Failed to publish invoices' },
      { status: 500 }
    );
  }
}