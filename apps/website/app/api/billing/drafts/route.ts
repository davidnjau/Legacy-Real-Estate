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

export async function GET(request: Request) {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;
    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const month = new URL(request.url).searchParams.get('month')?.trim();
    if (!month || !/^\d{4}-\d{2}$/.test(month)) {
      return NextResponse.json({ error: 'A valid month in YYYY-MM format is required.' }, { status: 400 });
    }

    const { data: invoices, error: invoiceError } = await supabaseAdmin
      .from('invoices')
      .select('*')
      .eq('property_id', scope.propertyId)
      .eq('billing_month', month)
      .in('status', ['draft', 'partial', 'paid', 'unpaid'])
      .is('published_at', null)
      .order('unit_id', { ascending: true });

    if (invoiceError) {
      return NextResponse.json({ error: invoiceError.message }, { status: 500 });
    }

    const unitIds = [...new Set((invoices || []).map((invoice) => invoice.unit_id).filter(Boolean))];
    const tenantIds = [...new Set((invoices || []).map((invoice) => invoice.tenant_id).filter(Boolean))];

    const [{ data: units }, { data: tenants }] = await Promise.all([
      unitIds.length
        ? supabaseAdmin.from('units').select('id, unit_code').in('id', unitIds)
        : Promise.resolve({ data: [] as { id: string; unit_code: string }[] }),
      tenantIds.length
        ? supabaseAdmin.from('users').select('id, full_name').in('id', tenantIds)
        : Promise.resolve({ data: [] as { id: string; full_name: string }[] }),
    ]);

    const unitMap = new Map((units || []).map((unit) => [unit.id, unit.unit_code]));
    const tenantMap = new Map((tenants || []).map((tenant) => [tenant.id, tenant.full_name]));

    return NextResponse.json({
      billingMonth: month,
      drafts: (invoices || []).map((invoice) => ({
        id: invoice.id,
        billing_month: invoice.billing_month,
        unit_id: invoice.unit_id,
        unit_code: unitMap.get(invoice.unit_id) || '-',
        tenant_name: tenantMap.get(invoice.tenant_id) || 'Unassigned',
        rent_amount: Number(invoice.rent_amount || 0),
        garbage_amount: Number(invoice.garbage_amount || 0),
        water_units: Number(invoice.water_units || 0),
        water_amount: Number(invoice.water_amount || 0),
        water_reading_missing: Boolean(invoice.water_reading_missing),
        carried_arrears_amount: Number(invoice.carried_arrears_amount || 0),
        total_amount_due: Number(invoice.total_amount_due || 0),
        amount_paid: Number(invoice.amount_paid || 0),
        outstanding_balance: Number(invoice.outstanding_balance || 0),
        status: invoice.status,
      })),
    });
  } catch (error: any) {
    console.error('Draft invoice fetch failed:', error);
    return NextResponse.json({ error: 'Failed to fetch draft invoices.' }, { status: 500 });
  }
}
