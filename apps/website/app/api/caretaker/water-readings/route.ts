import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { getInvoiceReleaseAt } from '../../../../lib/billing-schedule';
import { generateDraftInvoices } from '../../../../lib/billing-generate';
import { getActivePropertyId } from '../../../../lib/property-access';

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

/**
 * GET — fetch water reading state for all occupied units.
 * 
 * Water reading lifecycle per unit:
 *   1. BASELINE: A single reading_type='baseline' record. Set either by:
 *        a) The landlord in the migration wizard (for existing tenants), OR
 *        b) The caretaker via the dashboard modal (for new tenants).
 *      This reading is NEVER billed — it's the starting reference point.
 *   2. MONTHLY: End-of-month readings (reading_type='monthly'). The first
 *      monthly reading calculates usage as (monthly - baseline). Subsequent
 *      months calculate as (current_monthly - previous_monthly).
 * 
 * What this endpoint returns per unit:
 *   - baselineReading: the baseline meter value (null if none exists yet)
 *   - previousReading: the reference reading to subtract from the new one
 *     (= last monthly reading, or baseline if no monthly readings yet)
 *   - latestReading: the most recent monthly reading for this period (null if none yet)
 *   - unitsUsed: calculated water units (latestReading - previousReading)
 *   - hasBaseline: whether a baseline exists at all
 *   - status: reading status for the current period
 */
export async function GET() {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;

    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const { data: units, error: unitsError } = await supabaseAdmin
      .from('units')
      .select('id, unit_code')
      .eq('property_id', scope.propertyId)
      .eq('status', 'occupied')
      .order('unit_code', { ascending: true });

    if (unitsError) {
      return NextResponse.json({ error: unitsError.message }, { status: 500 });
    }

    const result = [];
    const now = new Date();
    const currentCalendarMonth = new Intl.DateTimeFormat('en-CA', { timeZone: 'Africa/Nairobi', year: 'numeric', month: '2-digit' }).format(now);
    const releaseAtStr = getInvoiceReleaseAt(currentCalendarMonth);
    const released = new Date(releaseAtStr) <= now;
    
    const nextMonth = (month: string) => {
      const [year, value] = month.split('-').map(Number);
      return new Date(Date.UTC(year, value, 1)).toISOString().slice(0, 7);
    };
    
    const preparationMonth = released ? nextMonth(currentCalendarMonth) : currentCalendarMonth;

    for (const unit of units || []) {
      // Fetch ALL readings for this unit, ordered newest first
      const { data: allReadings } = await supabaseAdmin
        .from('water_readings')
        .select('id, meter_reading, reading_date, reading_type, status')
        .eq('unit_id', unit.id)
        .order('reading_date', { ascending: false })
        .order('created_at', { ascending: false })
        .limit(50);

      const readings = allReadings || [];

      // 1. Find the baseline reading (there should be exactly 0 or 1)
      const baselineRecord = readings.find(r => r.reading_type === 'baseline') || null;
      const baselineReading = baselineRecord ? Number(baselineRecord.meter_reading) : null;

      // 2. Get all monthly readings, sorted newest first
      const monthlyReadings = readings.filter(r => r.reading_type === 'monthly');

      // 3. Determine the latest monthly reading (current period)
      const latestMonthly = monthlyReadings[0] || null;

      // 4. Determine the "previous" reading for delta calculation:
      //    - If there are 2+ monthly readings, previous = second-most-recent monthly
      //    - If there is 1 monthly reading, previous = baseline
      //    - If there are 0 monthly readings, previous = baseline (for next entry)
      let previousReading: number | null = null;
      if (monthlyReadings.length >= 2) {
        previousReading = Number(monthlyReadings[1].meter_reading);
      } else {
        previousReading = baselineReading;
      }

      // 5. Calculate units used
      let unitsUsed: number | null = null;
      if (latestMonthly && previousReading !== null) {
        unitsUsed = Math.max(0, Number(latestMonthly.meter_reading) - previousReading);
      }

      // 6. Current reading status
      const latestMonthStr = latestMonthly ? latestMonthly.reading_date.slice(0, 7) : null;
      const isFromPreviousMonth = Boolean(latestMonthStr && latestMonthStr < preparationMonth);
      const publishedForNextCycle = latestMonthly?.status === 'published' || isFromPreviousMonth;
      
      const currentStatus = isFromPreviousMonth ? 'not_submitted' : latestMonthly?.status || (baselineRecord ? 'baseline_only' : 'not_submitted');

      result.push({
        unitId: unit.id,
        unitCode: unit.unit_code,
        baselineReading,
        previousReading: publishedForNextCycle ? Number(latestMonthly.meter_reading) : previousReading,
        latestReading: publishedForNextCycle ? null : latestMonthly ? Number(latestMonthly.meter_reading) : null,
        unitsUsed: publishedForNextCycle ? null : unitsUsed,
        hasBaseline: baselineRecord !== null,
        status: currentStatus,
      });
    }

    return NextResponse.json({
      readings: result,
      billingMonth: preparationMonth,
      nextReleaseAt: getInvoiceReleaseAt(preparationMonth),
    });
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}

/**
 * POST — two modes:
 *   1. Baseline entry (from caretaker dashboard modal):
 *      body: { caretakerId, baseline: { unitId, reading } }
 *   2. Monthly readings (from water readings table):
 *      body: { caretakerId, readings: [{ unitId, reading }] }
 * 
 * The baseline path is triggered ONLY for new tenants who don't have a
 * baseline reading yet. Migration tenants already have theirs.
 */
export async function POST(request: Request) {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;

    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const body = await request.json();
    const { caretakerId } = body;

    if (!caretakerId) {
      return NextResponse.json({ error: 'Caretaker ID is required.' }, { status: 400 });
    }

    if (caretakerId !== auth.currentUser?.user.id) {
      return NextResponse.json({ error: 'The submitted user does not match the authenticated account.' }, { status: 403 });
    }

    // ─── MODE 1: Baseline entry (from dashboard modal) ───
    if (body.baseline) {
      const unitId = String(body.baseline.unitId || '').trim();
      const value = Number(body.baseline.reading);
      if (!unitId || Number.isNaN(value) || value < 0) {
        return NextResponse.json({ error: 'A valid unit and non-negative baseline reading are required.' }, { status: 400 });
      }

      const { data: scopedUnit, error: scopedUnitError } = await supabaseAdmin.from('units')
        .select('id')
        .eq('id', unitId)
        .eq('property_id', scope.propertyId)
        .maybeSingle();
      if (scopedUnitError || !scopedUnit) return NextResponse.json({ error: 'Unit not found in the active property.' }, { status: 404 });

      // Get the active tenant for this unit (any non-deleted assignment)
      const { data: assignment } = await supabaseAdmin
        .from('unit_assignments')
        .select('tenant_id')
        .eq('unit_id', unitId)
        .eq('status', 'active')
        .limit(1)
        .maybeSingle();
      if (!assignment || !assignment.tenant_id) {
        return NextResponse.json({ error: 'No tenant is assigned to this unit.' }, { status: 400 });
      }

      // Guard: prevent duplicate baselines
      const { count } = await supabaseAdmin
        .from('water_readings')
        .select('id', { count: 'exact', head: true })
        .eq('unit_id', unitId)
        .eq('reading_type', 'baseline');
      if (count) {
        return NextResponse.json({ error: 'This unit already has a baseline reading.' }, { status: 409 });
      }

      const { error } = await supabaseAdmin.from('water_readings').insert({
        unit_id: unitId,
        tenant_id: assignment.tenant_id,
        submitted_by_user_id: caretakerId,
        reading_date: new Date().toISOString().slice(0, 10),
        meter_reading: value,
        reading_type: 'baseline',
        status: 'submitted', // baselines are immediately usable
      });
      if (error) return NextResponse.json({ error: error.message }, { status: 500 });
      return NextResponse.json({ success: true, message: 'Baseline water reading saved.' });
    }

    // ─── MODE 2: Monthly readings (from the Water Readings table) ───
    const { readings } = body;
    if (!Array.isArray(readings) || readings.length === 0) {
      return NextResponse.json({ error: 'Readings array is required.' }, { status: 400 });
    }

    const today = new Date().toISOString().slice(0, 10);

    for (const entry of readings) {
      const unitId = String(entry.unitId || '').trim();
      const value = Number(entry.reading);

      if (!unitId || Number.isNaN(value) || value < 0) {
        return NextResponse.json({ error: `Invalid reading for unit ${unitId}.` }, { status: 400 });
      }

      const { data: scopedUnit, error: scopedUnitError } = await supabaseAdmin.from('units')
        .select('id')
        .eq('id', unitId)
        .eq('property_id', scope.propertyId)
        .maybeSingle();
      if (scopedUnitError || !scopedUnit) return NextResponse.json({ error: 'Unit not found in the active property.' }, { status: 404 });

      // Verify unit has a baseline (cannot accept monthly without baseline)
      const { count: baselineCount } = await supabaseAdmin
        .from('water_readings')
        .select('id', { count: 'exact', head: true })
        .eq('unit_id', unitId)
        .eq('reading_type', 'baseline');

      if (!baselineCount) {
        return NextResponse.json({
          error: `Unit ${unitId} does not have a baseline reading yet. Add one from the dashboard first.`,
        }, { status: 400 });
      }

      // Get tenant assignment (any non-deleted assignment)
      const { data: assignment } = await supabaseAdmin
        .from('unit_assignments')
        .select('tenant_id, status')
        .eq('unit_id', unitId)
        .eq('status', 'active')
        .limit(1)
        .maybeSingle();

      if (!assignment || !assignment.tenant_id) {
        return NextResponse.json({
          error: `No tenant assignment found for unit ${unitId}. The unit may not have an active tenant.`,
        }, { status: 400 });
      }

      const readingPayload = {
        unit_id: unitId,
        tenant_id: assignment.tenant_id,
        submitted_by_user_id: caretakerId,
        reading_date: today,
        meter_reading: value,
        reading_type: 'monthly',
        status: 'submitted',
      };

      const { data: existingReading, error: lookupError } = await supabaseAdmin
        .from('water_readings')
        .select('id')
        .eq('unit_id', unitId)
        .eq('reading_date', today)
        .eq('reading_type', 'monthly')
        .maybeSingle();

      if (lookupError) return NextResponse.json({ error: lookupError.message }, { status: 500 });

      const { error: writeError } = existingReading
        ? await supabaseAdmin.from('water_readings').update(readingPayload).eq('id', existingReading.id)
        : await supabaseAdmin.from('water_readings').insert(readingPayload);

      if (writeError) return NextResponse.json({ error: writeError.message }, { status: 500 });
    }

    return NextResponse.json({
      success: true,
      message: `${readings.length} water reading(s) saved. Ready for review.`,
    });
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}

// PATCH — caretaker publishes/confirms the readings for the month
export async function PATCH(request: Request) {
  try {
    const auth = await requireApiRole(['landlord', 'caretaker']);
    if (auth.response) return auth.response;

    const scope = await getActivePropertyId(supabaseAdmin, auth.currentUser!.user.id);
    if (scope.error || !scope.propertyId) return NextResponse.json({ error: scope.error || 'Select a property.' }, { status: 403 });

    const body = await request.json();
    const { caretakerId } = body;

    if (!caretakerId) {
      return NextResponse.json({ error: 'Caretaker ID is required.' }, { status: 400 });
    }

    if (caretakerId !== auth.currentUser?.user.id) {
      return NextResponse.json({ error: 'The submitted user does not match the authenticated account.' }, { status: 403 });
    }

    // 1. Mark all submitted monthly readings as published
    const { error } = await supabaseAdmin
      .from('water_readings')
      .update({ status: 'published' })
      .eq('status', 'submitted')
      .eq('reading_type', 'monthly')
      .in('unit_id', (await supabaseAdmin.from('units').select('id').eq('property_id', scope.propertyId)).data?.map((unit) => unit.id) || []);

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    // 2. Determine the preparation (billing) month
    const now = new Date();
    const currentCalendarMonth = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Africa/Nairobi',
      year: 'numeric',
      month: '2-digit',
    }).format(now);
    const releaseAtStr = getInvoiceReleaseAt(currentCalendarMonth);
    const released = new Date(releaseAtStr) <= now;
    const nextMonthStr = (month: string) => {
      const [year, value] = month.split('-').map(Number);
      return new Date(Date.UTC(year, value, 1)).toISOString().slice(0, 7);
    };
    const preparationMonth = released ? nextMonthStr(currentCalendarMonth) : currentCalendarMonth;

    const { data: billingSettings, error: billingSettingsError } = await supabaseAdmin
      .from('property_billing_settings')
      .select('first_online_billing_month')
      .eq('property_id', scope.propertyId)
      .maybeSingle();
    if (billingSettingsError) return NextResponse.json({ error: billingSettingsError.message }, { status: 500 });

    if (!billingSettings?.first_online_billing_month) {
      const { error: initializeBillingError } = await supabaseAdmin.from('property_billing_settings').upsert({
        property_id: scope.propertyId,
        first_online_billing_month: preparationMonth,
        updated_by: auth.currentUser!.user.id,
        updated_at: new Date().toISOString(),
      }, { onConflict: 'property_id' });
      if (initializeBillingError) return NextResponse.json({ error: initializeBillingError.message }, { status: 500 });
    }

    // 3. Auto-generate draft invoices for the preparation month (idempotent)
    try {
      const genResult = await generateDraftInvoices(supabaseAdmin, preparationMonth, scope.propertyId);
      console.log(`[water-readings PATCH] Generated ${genResult.invoicesCreated} and updated ${genResult.invoicesUpdated} draft(s) for ${preparationMonth}`);
      return NextResponse.json({
        success: true,
        message: `Water readings published. ${genResult.invoicesCreated} draft invoice(s) created and ${genResult.invoicesUpdated} refreshed for ${preparationMonth}.`,
        autoGeneratedInvoices: genResult.invoicesCreated,
        updatedDraftInvoices: genResult.invoicesUpdated,
        billingMonth: preparationMonth,
      });
    } catch (genErr: any) {
      console.error('[water-readings PATCH] Auto-generation failed:', genErr.message);
      return NextResponse.json({ error: `Readings were published, but draft generation failed: ${genErr.message}` }, { status: 500 });
    }
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
