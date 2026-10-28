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
    const { units } = body; 
    // Array of { unitCode, floorNumber, rentAmount, garbageAmount, waterRate, isOccupied, fullName, email, phone, openingArrears, waterReading }

    if (!Array.isArray(units) || units.length === 0) {
      return NextResponse.json({ error: 'No units provided for import.' }, { status: 400 });
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

    const today = new Date().toISOString().slice(0, 10);
    const results = { success: 0, failed: 0, emailFailures: [] as string[], errors: [] as string[] };

    for (const row of units) {
      try {
        if (!row.unitCode || row.rentAmount === undefined || row.garbageAmount === undefined) {
          throw new Error(`Missing required unit fields for ${row.unitCode || 'Unknown'}`);
        }

        // 1. Create Unit
        const { data: newUnit, error: unitError } = await supabaseAdmin
          .from('units')
          .insert({
            property_id: scope.propertyId,
            unit_code: row.unitCode.toUpperCase(),
            floor_number: row.floorNumber || null,
            rent_amount: Number(row.rentAmount),
            garbage_amount: Number(row.garbageAmount),
            monthly_water_rate: Number(row.waterRate) || 150,
            deposit_amount: row.isOccupied ? secDeposit : 0,
            water_deposit_amount: row.isOccupied ? waterDeposit : 0,
            status: row.isOccupied ? 'occupied' : 'vacant',
            is_migrated: true,
          })
          .select()
          .single();

        if (unitError) throw new Error(`Unit ${row.unitCode} creation failed: ${unitError.message}`);

        if (!row.isOccupied) {
          results.success++;
          continue;
        }

        // 2. Auth User
        const normalizedPhone = normalizeKenyanPhoneForStorage(row.phone);
        const normalizedEmail = String(row.email || '').trim().toLowerCase();
        if (!row.fullName || !normalizedEmail || !normalizedPhone) {
          throw new Error(`Missing tenant fields for occupied unit ${row.unitCode}`);
        }

        const { data: authUser, error: authError } = await supabaseAdmin.auth.admin.createUser({
          email: normalizedEmail,
          email_confirm: true,
          user_metadata: { full_name: row.fullName, phone: normalizedPhone },
        });

        if (authError || !authUser?.user) {
          throw new Error(`Auth failed for ${normalizedEmail}: ${authError?.message}`);
        }

        const tenantId = authUser.user.id;

        // 3. Profile
        const { error: profileError } = await supabaseAdmin.from('users').insert({
          id: tenantId,
          full_name: row.fullName,
          email: normalizedEmail,
          phone: normalizedPhone,
          role: 'tenant',
          status: 'dormant',
        });
        if (profileError) throw new Error(`Profile creation failed for ${normalizedEmail}: ${profileError.message}`);

        // 4. Assignment
        const { error: assignmentError } = await supabaseAdmin.from('unit_assignments').insert({
          unit_id: newUnit.id,
          tenant_id: tenantId,
          move_in_date: today,
          status: 'active',
          is_migrated: true,
          opening_arrears: Number(row.openingArrears) || 0,
          deposit_paid: 0,
        });
        if (assignmentError) throw new Error(`Assignment failed for ${row.email}`);

        // 5. Water Reading. Missing baseline is handled by the caretaker dashboard.
        if (row.waterReading !== undefined && row.waterReading !== null && row.waterReading !== '') {
          await supabaseAdmin.from('water_readings').insert({
            unit_id: newUnit.id,
            tenant_id: tenantId,
            submitted_by_user_id: auth.currentUser?.user.id,
            meter_reading: Number(row.waterReading),
            reading_date: today,
            reading_type: 'baseline',
            status: 'submitted',
          });
        }

        // 6. Create the office activation code and send the email activation link.
        const activation = await createTenantActivation(supabaseAdmin, {
          unitId: newUnit.id,
          unitCode: newUnit.unit_code,
          tenantId,
          fullName: row.fullName,
          email: normalizedEmail,
          origin: new URL(request.url).origin,
          createClaimCode: false,
        });
        if (!activation.emailSent) {
          results.emailFailures.push(`${normalizedEmail}: ${activation.emailError || 'email provider error'}`);
        }

        results.success++;
      } catch (err: any) {
        results.failed++;
        results.errors.push(err.message);
      }
    }

    return NextResponse.json({
      message: `Successfully imported ${results.success} records. ${results.failed} failed. ${results.emailFailures.length} activation email(s) need retry.`,
      ...results
    });

  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
