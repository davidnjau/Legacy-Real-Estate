import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { getActivePropertyId } from '../../../../lib/property-access';

const admin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

export async function GET() {
  const auth = await requireApiRole(['landlord']);
  if (auth.response) return auth.response;
  const scope = await getActivePropertyId(admin, auth.currentUser!.user.id);
  if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

  const { data, error } = await admin
    .from('property_billing_settings')
    .select('paybill_number, account_number, universal_security_deposit, universal_water_deposit, first_online_billing_month')
    .eq('property_id', scope.propertyId)
    .maybeSingle();
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json({ settings: data || { paybill_number: '', account_number: '' } });
}

export async function PATCH(request: Request) {
  const auth = await requireApiRole(['landlord']);
  if (auth.response) return auth.response;
  const scope = await getActivePropertyId(admin, auth.currentUser!.user.id);
  if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

  const body = await request.json();
  const updates = {
    paybill_number: String(body.paybillNumber || '').trim() || null,
    account_number: String(body.accountNumber || '').trim() || null,
    updated_by: auth.currentUser!.user.id,
    updated_at: new Date().toISOString(),
  };

  const { data: existing, error: lookupError } = await admin
    .from('property_billing_settings')
    .select('property_id')
    .eq('property_id', scope.propertyId)
    .maybeSingle();
  if (lookupError) return NextResponse.json({ error: lookupError.message }, { status: 500 });

  const query = existing
    ? admin.from('property_billing_settings').update(updates).eq('property_id', scope.propertyId)
    : admin.from('property_billing_settings').insert({
        property_id: scope.propertyId,
        universal_security_deposit: 8000,
        universal_water_deposit: 500,
        first_online_billing_month: null,
        ...updates,
      });
  const { data, error } = await query.select('paybill_number, account_number, universal_security_deposit, universal_water_deposit, first_online_billing_month').single();
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json({ settings: data });
}