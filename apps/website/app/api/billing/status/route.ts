// app/api/billing/status/route.ts
import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
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
 * GET /api/billing/status?month=YYYY-MM
 *
 * Returns whether any published invoices exist for the given billing month.
 * Used by the billing wizard to show context-aware guidance.
 */
export async function GET(request: Request) {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;
    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const { searchParams } = new URL(request.url);
    const month = searchParams.get('month');

    if (!month || !/^\d{4}-\d{2}$/.test(month)) {
      return NextResponse.json({ error: 'Valid month (YYYY-MM) is required.' }, { status: 400 });
    }

    const releaseAt = getInvoiceReleaseAt(month);
    const releasedAt = new Date(releaseAt) <= new Date();

    const { data: published, error } = await supabaseAdmin
      .from('invoices')
      .select('id')
      .eq('billing_month', month)
      .eq('property_id', scope.propertyId)
      .in('status', ['published', 'paid', 'partial'])
      .not('published_at', 'is', null)
      .lte('publish_at', new Date().toISOString())
      .limit(1);

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    const { data: drafts } = await supabaseAdmin
      .from('invoices')
      .select('id')
      .eq('billing_month', month)
      .eq('property_id', scope.propertyId)
      .in('status', ['draft', 'partial', 'paid', 'unpaid'])
      .is('published_at', null)
      .limit(1);

    const hasDrafts = (drafts || []).length > 0;
    const hasPublished = (published || []).length > 0;
    let hasPublishedFinal = hasPublished;

    // Publish due drafts even if some invoices were already published earlier.
    if (releasedAt && hasDrafts) {
      try {
        const publishedCount = await publishDraftInvoices(supabaseAdmin, month, scope.propertyId);
        hasPublishedFinal = hasPublished || publishedCount > 0;
        console.log(`[billing/status] Auto-published drafts for ${month}`);
      } catch (err: any) {
        console.error(`[billing/status] Auto-publish failed for ${month}:`, err.message);
      }
    }

    return NextResponse.json({
      month,
      hasPublished: hasPublishedFinal,
      hasDrafts,
      releaseAt,
      releasedAt,
    });
  } catch (error: any) {
    console.error('Billing status check failed:', error);
    return NextResponse.json({ error: error.message || 'Failed to check billing status.' }, { status: 500 });
  }
}
