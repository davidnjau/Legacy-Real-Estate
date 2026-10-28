import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';

const admin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

function parseProperty(body: Record<string, unknown>) {
  const name = String(body.name || '').trim();
  const currencyCode = String(body.currencyCode || 'KES').trim().toUpperCase();
  const timezone = String(body.timezone || 'Africa/Nairobi').trim();
  const billingMode = body.billingMode === 'fixed_utilities' ? 'fixed_utilities' : 'metered_water';
  const rentDueDay = Number(body.rentDueDay ?? 10);
  const amounts = {
    default_rent: Number(body.defaultRent ?? 0),
    security_deposit: Number(body.securityDeposit ?? 0),
    water_deposit: Number(body.waterDeposit ?? 0),
    default_garbage_charge: Number(body.garbageCharge ?? 0),
    default_water_rate: Number(body.waterRate ?? 0),
  };

  if (!name || name.length > 120) throw new Error('Property name is required and must be 120 characters or fewer.');
  if (!/^[A-Z]{3}$/.test(currencyCode)) throw new Error('Currency must be a three-letter ISO code.');
  if (!Number.isInteger(rentDueDay) || rentDueDay < 1 || rentDueDay > 28) throw new Error('Rent due day must be between 1 and 28.');
  if (Object.values(amounts).some((amount) => !Number.isFinite(amount) || amount < 0)) throw new Error('Property charges must be non-negative numbers.');

  return {
    name,
    address: String(body.address || '').trim() || null,
    currency_code: currencyCode,
    timezone,
    rent_due_day: rentDueDay,
    billing_mode: billingMode,
    ...amounts,
    updated_at: new Date().toISOString(),
  };
}

function parseRecurringCharges(value: unknown) {
  if (!Array.isArray(value)) return [];
  if (value.length > 20) throw new Error('A property can have at most 20 recurring utility charges.');
  const charges = value.map((item: any) => ({
    name: String(item?.name || '').trim(),
    amount: Number(item?.amount),
    is_active: item?.isActive !== false,
  }));
  if (charges.some((charge) => !charge.name || charge.name.length > 80 || !Number.isFinite(charge.amount) || charge.amount < 0)) {
    throw new Error('Every recurring charge needs a name of 80 characters or fewer and a non-negative amount.');
  }
  if (new Set(charges.map((charge) => charge.name.toLowerCase())).size !== charges.length) {
    throw new Error('Recurring charge names must be unique within the property.');
  }
  return charges;
}

export async function GET() {
  const auth = await requireApiRole(['landlord']);
  if (auth.response) return auth.response;

  const [{ data: properties, error: propertiesError }, { data: profile, error: profileError }] = await Promise.all([
    admin.from('properties').select('*').eq('owner_id', auth.currentUser!.user.id).order('created_at', { ascending: true }),
    admin.from('users').select('active_property_id').eq('id', auth.currentUser!.user.id).maybeSingle(),
  ]);
  if (propertiesError || profileError) return NextResponse.json({ error: propertiesError?.message || profileError?.message }, { status: 500 });
  const propertyIds = (properties || []).map((property) => property.id);
  const { data: charges, error: chargesError } = propertyIds.length
    ? await admin.from('property_recurring_charges').select('id, property_id, name, amount, is_active').in('property_id', propertyIds).order('name')
    : { data: [], error: null };
  if (chargesError) return NextResponse.json({ error: chargesError.message }, { status: 500 });
  const chargesByProperty = new Map<string, any[]>();
  for (const charge of charges || []) {
    const items = chargesByProperty.get(charge.property_id) || [];
    items.push(charge);
    chargesByProperty.set(charge.property_id, items);
  }
  return NextResponse.json({
    properties: (properties || []).map((property) => ({ ...property, recurringCharges: chargesByProperty.get(property.id) || [] })),
    activePropertyId: profile?.active_property_id || properties?.[0]?.id || null,
  });
}

export async function POST(request: Request) {
  const auth = await requireApiRole(['landlord']);
  if (auth.response) return auth.response;

  try {
    const body = await request.json();
    const propertyInput = parseProperty(body);
    const recurringCharges = parseRecurringCharges(body.recurringCharges);
    const landlordId = auth.currentUser!.user.id;
    const { data: property, error: propertyError } = await admin.from('properties')
      .insert({ owner_id: landlordId, ...propertyInput })
      .select()
      .single();
    if (propertyError || !property) return NextResponse.json({ error: propertyError?.message || 'Unable to create property.' }, { status: 500 });

    const { error: membershipError } = await admin.from('property_members').insert({
      property_id: property.id,
      user_id: landlordId,
      member_role: 'owner',
    });
    if (membershipError) {
      await admin.from('properties').delete().eq('id', property.id).eq('owner_id', landlordId);
      return NextResponse.json({ error: membershipError.message }, { status: 500 });
    }

    if (recurringCharges.length) {
      const { error: chargeError } = await admin.from('property_recurring_charges').insert(
        recurringCharges.map((charge) => ({ property_id: property.id, ...charge }))
      );
      if (chargeError) {
        await admin.from('properties').delete().eq('id', property.id).eq('owner_id', landlordId);
        return NextResponse.json({ error: chargeError.message }, { status: 500 });
      }
    }

    const { data: currentProfile } = await admin.from('users').select('active_property_id').eq('id', landlordId).maybeSingle();
    if (!currentProfile?.active_property_id) {
      await admin.from('users').update({ active_property_id: property.id, property_name: property.name, updated_at: new Date().toISOString() }).eq('id', landlordId);
    }

    return NextResponse.json({ property, activePropertyId: currentProfile?.active_property_id || property.id }, { status: 201 });
  } catch (error: any) {
    return NextResponse.json({ error: error.message || 'Invalid property details.' }, { status: 400 });
  }
}

export async function PATCH(request: Request) {
  const auth = await requireApiRole(['landlord']);
  if (auth.response) return auth.response;

  try {
    const body = await request.json();
    const landlordId = auth.currentUser!.user.id;

    if (body.activePropertyId) {
      const { data: property, error: propertyError } = await admin.from('properties')
        .select('id, name')
        .eq('id', String(body.activePropertyId))
        .eq('owner_id', landlordId)
        .maybeSingle();
      if (propertyError || !property) return NextResponse.json({ error: 'Property not found for this landlord.' }, { status: 404 });
      const { error } = await admin.from('users').update({ active_property_id: property.id, property_name: property.name, updated_at: new Date().toISOString() }).eq('id', landlordId);
      if (error) return NextResponse.json({ error: error.message }, { status: 500 });
      return NextResponse.json({ activePropertyId: property.id });
    }

    const propertyId = String(body.propertyId || '');
    const { data: ownedProperty, error: ownershipError } = await admin.from('properties')
      .select('id')
      .eq('id', propertyId)
      .eq('owner_id', landlordId)
      .maybeSingle();
    if (ownershipError) return NextResponse.json({ error: ownershipError.message }, { status: 500 });
    if (!ownedProperty) return NextResponse.json({ error: 'Property not found for this landlord.' }, { status: 404 });

    const propertyInput = parseProperty(body);
    const hasRecurringCharges = Array.isArray(body.recurringCharges);
    const recurringCharges = hasRecurringCharges ? parseRecurringCharges(body.recurringCharges) : null;
    let previousCharges: any[] = [];
    if (hasRecurringCharges) {
      const { data, error: previousError } = await admin.from('property_recurring_charges')
        .select('name, amount, is_active')
        .eq('property_id', propertyId);
      if (previousError) return NextResponse.json({ error: previousError.message }, { status: 500 });
      previousCharges = data || [];
      const { error: deleteError } = await admin.from('property_recurring_charges').delete().eq('property_id', propertyId);
      if (deleteError) return NextResponse.json({ error: deleteError.message }, { status: 500 });
      if (recurringCharges!.length) {
        const { error: insertError } = await admin.from('property_recurring_charges').insert(
          recurringCharges!.map((charge) => ({ property_id: propertyId, ...charge }))
        );
        if (insertError) {
          if (previousCharges.length) await admin.from('property_recurring_charges').insert(previousCharges.map((charge) => ({ property_id: propertyId, ...charge })));
          return NextResponse.json({ error: insertError.message }, { status: 500 });
        }
      }
    }
    const { data: property, error } = await admin.from('properties')
      .update(propertyInput)
      .eq('id', propertyId)
      .eq('owner_id', landlordId)
      .select()
      .maybeSingle();
    if (error || !property) {
      if (hasRecurringCharges && previousCharges.length) await admin.from('property_recurring_charges').insert(previousCharges.map((charge) => ({ property_id: propertyId, ...charge })));
      return NextResponse.json({ error: error?.message || 'Property not found.' }, { status: 404 });
    }
    const { data: savedCharges } = await admin.from('property_recurring_charges').select('id, name, amount, is_active').eq('property_id', propertyId).order('name');
    return NextResponse.json({ property: { ...property, recurringCharges: savedCharges || [] } });
  } catch (error: any) {
    return NextResponse.json({ error: error.message || 'Invalid property details.' }, { status: 400 });
  }
}