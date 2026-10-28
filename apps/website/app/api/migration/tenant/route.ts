import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { createTenantActivation } from '../../../../lib/tenant-activation';
import { normalizeKenyanPhoneForStorage } from '../../../../lib/phone';
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

    const body = await request.json();
    const { 
      unitCode, floorNumber, rentAmount, garbageAmount, waterRate,
      isOccupied, fullName, email, phone, openingArrears, waterReading 
    } = body;
    const normalizedPhone = normalizeKenyanPhoneForStorage(phone);

    if (!unitCode || rentAmount === undefined || garbageAmount === undefined) {
      return NextResponse.json({ error: 'Unit Code, Rent, and Garbage amounts are required.' }, { status: 400 });
    }

    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const { data: settings } = await supabaseAdmin
      .from('property_billing_settings')
      .select('universal_security_deposit, universal_water_deposit')
      .eq('property_id', scope.propertyId)
      .maybeSingle();

    const secDeposit = settings?.universal_security_deposit ?? 8000;
    const waterDeposit = settings?.universal_water_deposit ?? 500;

    // 1. Create the Unit
    const { data: newUnit, error: unitError } = await supabaseAdmin
      .from('units')
      .insert({
        property_id: scope.propertyId,
        unit_code: unitCode.toUpperCase(),
        floor_number: floorNumber || null,
        rent_amount: Number(rentAmount),
        garbage_amount: Number(garbageAmount),
        monthly_water_rate: Number(waterRate) || 150,
        deposit_amount: isOccupied ? secDeposit : 0,
        water_deposit_amount: isOccupied ? waterDeposit : 0,
        status: isOccupied ? 'occupied' : 'vacant',
        is_migrated: true, // Flag it as part of the initial migration
      })
      .select()
      .single();

    if (unitError) {
      return NextResponse.json({ error: `Failed to create unit: ${unitError.message}` }, { status: 400 });
    }

    // If vacant, we are done.
    if (!isOccupied) {
      return NextResponse.json({ success: true, message: `Vacant unit ${unitCode} added successfully.` });
    }

    // 2. If Occupied, pre-register the Tenant
    if (!fullName || !email || !normalizedPhone) {
      // Rollback unit creation if tenant details are missing
      await supabaseAdmin.from('units').delete().eq('id', newUnit.id);
      return NextResponse.json({ error: 'Tenant Name, Email, and Phone are required for occupied units.' }, { status: 400 });
    }

    const { data: existingProfile, error: existingProfileError } = await supabaseAdmin
      .from('users')
      .select('id, status')
      .eq('email', email.trim())
      .maybeSingle();

    if (existingProfileError) {
      await supabaseAdmin.from('units').delete().eq('id', newUnit.id);
      return NextResponse.json(
        { error: `Unable to check for an existing tenant profile: ${existingProfileError.message}` },
        { status: 500 }
      );
    }

    if (existingProfile) {
      await supabaseAdmin.from('units').delete().eq('id', newUnit.id);
      return NextResponse.json(
        { error: `A tenant profile already exists for ${email.trim()}. Use a different email or remove the existing profile first.` },
        { status: 409 }
      );
    }

    // Create Auth User
    const { data: authUser, error: authError } = await supabaseAdmin.auth.admin.createUser({
      email: email.trim(),
      email_confirm: true,
      user_metadata: { full_name: fullName, phone: normalizedPhone },
    });

    if (authError || !authUser?.user) {
      await supabaseAdmin.from('units').delete().eq('id', newUnit.id);
      return NextResponse.json({ error: authError?.message || 'Failed to pre-register tenant email.' }, { status: 400 });
    }

    const tenantId = authUser.user.id;

    // Create User Profile (status = 'dormant')
    const { error: profileError } = await supabaseAdmin.from('users').insert({
      id: tenantId,
      full_name: fullName,
      email: email.trim(),
      phone: normalizedPhone,
      role: 'tenant',
      status: 'dormant', 
    });

    if (profileError) {
      await supabaseAdmin.auth.admin.deleteUser(tenantId);
      await supabaseAdmin.from('units').delete().eq('id', newUnit.id);
      return NextResponse.json(
        { error: `Failed to create tenant profile: ${profileError.message}` },
        { status: 500 }
      );
    }

    const today = new Date().toISOString().slice(0, 10);

    // Create Unit Assignment with opening arrears
    const { error: assignmentError } = await supabaseAdmin.from('unit_assignments').insert({
      unit_id: newUnit.id,
      tenant_id: tenantId,
      move_in_date: today,
      status: 'active',
      is_migrated: true,
      opening_arrears: Number(openingArrears) || 0,
      deposit_paid: 0,
    });

if (assignmentError) {
       await supabaseAdmin.auth.admin.deleteUser(tenantId);
       await supabaseAdmin.from('users').delete().eq('id', tenantId);
       await supabaseAdmin.from('units').delete().eq('id', newUnit.id);
       return NextResponse.json({ error: 'Failed to link tenant to unit.' }, { status: 500 });
     }


// Store initial water reading
    if (waterReading !== undefined && waterReading !== null && waterReading !== '') {
      await supabaseAdmin.from('water_readings').insert({
        unit_id: newUnit.id,
        tenant_id: tenantId,
        submitted_by_user_id: auth.currentUser?.user.id,
        meter_reading: Number(waterReading),
        reading_date: today,
        reading_type: 'baseline',
        status: 'submitted', // Pre-submitted so billing can use it immediately
      });
    }

    const activation = await createTenantActivation(supabaseAdmin, {
      unitId: newUnit.id,
      unitCode: newUnit.unit_code,
      tenantId,
      fullName,
      email: email.trim(),
      origin: new URL(request.url).origin,
      createClaimCode: false,
    });

    return NextResponse.json({
      success: true,
      message: activation.emailSent
        ? `Unit ${unitCode} and Tenant ${fullName} successfully added. An activation email was sent.`
        : `Unit ${unitCode} and Tenant ${fullName} were added, but the activation email could not be sent: ${activation.emailError}`,
      emailSent: activation.emailSent,
      emailError: activation.emailError,
    });

  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
