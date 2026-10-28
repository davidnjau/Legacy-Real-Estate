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

export async function GET() {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;
    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    // Fetch all billing_month values to extract distinct months
    // We order descending so the newest months are first
    const { data, error } = await supabaseAdmin
      .from('invoices')
      .select('billing_month')
      .eq('property_id', scope.propertyId)
      .order('billing_month', { ascending: false });

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    // Extract unique months using a Set
    const uniqueMonths = [...new Set((data || []).map(row => row.billing_month))];

    // If there are no invoices at all, at least return the current month
    if (uniqueMonths.length === 0) {
      uniqueMonths.push(new Date().toISOString().slice(0, 7));
    }

    return NextResponse.json({ months: uniqueMonths });
  } catch (error: any) {
    console.error('Failed to fetch ledger months:', error);
    return NextResponse.json({ error: 'Failed to fetch available months.' }, { status: 500 });
  }
}
