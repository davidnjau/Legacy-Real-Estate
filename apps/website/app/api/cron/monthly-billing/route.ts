import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { generateDraftInvoices, publishDraftInvoices } from '../../../../lib/billing-generate';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

export async function GET(request: Request) {
  const secret = process.env.CRON_SECRET;
  if (!secret) return NextResponse.json({ error: 'Monthly billing cron is not configured.' }, { status: 503 });
  if (request.headers.get('authorization') !== `Bearer ${secret}`) {
    return NextResponse.json({ error: 'Unauthorized.' }, { status: 401 });
  }

  const dateParts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Nairobi',
    year: 'numeric',
    month: '2-digit',
  }).formatToParts(new Date());
  const billingMonth = `${dateParts.find((part) => part.type === 'year')?.value}-${dateParts.find((part) => part.type === 'month')?.value}`;
  const { data: properties, error: propertiesError } = await supabaseAdmin.from('properties').select('id');
  if (propertiesError) {
    console.error('Monthly billing could not load properties:', propertiesError);
    return NextResponse.json({ error: 'Unable to load properties for monthly billing.' }, { status: 500 });
  }

  const results: { propertyId: string; invoicesCreated: number; invoicesUpdated: number; invoicesPublished: number; warnings: string[] }[] = [];
  const failures: { propertyId: string; error: string }[] = [];
  for (const property of properties || []) {
    try {
      const generation = await generateDraftInvoices(supabaseAdmin, billingMonth, property.id);
      const invoicesPublished = await publishDraftInvoices(supabaseAdmin, billingMonth, property.id);
      results.push({
        propertyId: property.id,
        invoicesCreated: generation.invoicesCreated,
        invoicesUpdated: generation.invoicesUpdated,
        invoicesPublished,
        warnings: generation.skipped.filter((item) => item.includes('metered water reading')),
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Billing operation failed.';
      console.error(`Monthly billing failed for property ${property.id}:`, error);
      failures.push({ propertyId: property.id, error: message });
    }
  }

  return NextResponse.json({ billingMonth, results, failures }, { status: failures.length ? 500 : 200 });
}
