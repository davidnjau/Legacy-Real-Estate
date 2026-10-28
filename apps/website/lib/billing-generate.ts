import { SupabaseClient } from '@supabase/supabase-js';
import { getInvoiceReleaseAt } from './billing-schedule';

export interface GenerateResult {
  invoicesCreated: number;
  invoicesUpdated: number;
  skipped: string[];
}

export async function syncDraftInvoiceArrears(
  supabaseAdmin: SupabaseClient,
  billingMonth: string,
  unitId: string,
  tenantId: string,
  arrears: number
): Promise<void> {
  const { data: invoice, error: fetchError } = await supabaseAdmin
    .from('invoices')
    .select('id, rent_amount, garbage_amount, water_amount, recurring_charges, amount_paid, status, publish_at, published_at')
    .eq('unit_id', unitId)
    .eq('tenant_id', tenantId)
    .eq('billing_month', billingMonth)
    .maybeSingle();

  if (fetchError) throw new Error(fetchError.message);
  if (!invoice || invoice.published_at) return;

  const totalAmountDue = Number(invoice.rent_amount || 0)
    + Number(invoice.garbage_amount || 0)
    + Number(invoice.water_amount || 0)
    + ((invoice.recurring_charges as { amount?: number }[] | null) || []).reduce((sum, charge) => sum + Number(charge.amount || 0), 0)
    + arrears;
  const amountPaid = Number(invoice.amount_paid || 0);
  const { error: updateError } = await supabaseAdmin
    .from('invoices')
    .update({
      status: 'draft',
      carried_arrears_amount: arrears,
      total_amount_due: totalAmountDue,
      outstanding_balance: Math.max(0, totalAmountDue - amountPaid),
      updated_at: new Date().toISOString(),
    })
    .eq('id', invoice.id);

  if (updateError) throw new Error(updateError.message);
}

/**
 * Core billing generation logic.
 * Generates draft invoices for all occupied units for the given billingMonth.
 * Idempotent: units that already have an invoice for the month are skipped.
 */
export async function generateDraftInvoices(
  supabaseAdmin: SupabaseClient,
  billingMonth: string,
  propertyId?: string
): Promise<GenerateResult> {
  let unitsQuery = supabaseAdmin
    .from('units')
    .select('*')
    .in('status', ['occupied', 'notice_given', 'booked']);
  if (propertyId) unitsQuery = unitsQuery.eq('property_id', propertyId);
  const { data: units, error: unitsError } = await unitsQuery;

  if (unitsError) throw new Error(unitsError.message);

  const generatedInvoices: any[] = [];
  let invoicesUpdated = 0;
  const skipped: string[] = [];
  const propertyIds = [...new Set((units || []).map((unit) => unit.property_id).filter(Boolean))];
  const [{ data: configuredCharges, error: chargesError }, { data: propertySettings, error: propertySettingsError }] = propertyIds.length
    ? await Promise.all([
        supabaseAdmin.from('property_recurring_charges')
          .select('property_id, name, amount')
          .in('property_id', propertyIds)
          .eq('is_active', true)
          .order('name'),
        supabaseAdmin.from('properties').select('id, billing_mode').in('id', propertyIds),
      ])
    : [{ data: [], error: null }, { data: [], error: null }];
  if (chargesError) throw new Error(`Failed to load recurring charges: ${chargesError.message}`);
  if (propertySettingsError) throw new Error(`Failed to load property billing settings: ${propertySettingsError.message}`);
  const chargesByProperty = new Map<string, { name: string; amount: number }[]>();
  const billingModeByProperty = new Map((propertySettings || []).map((property) => [property.id, property.billing_mode]));
  for (const charge of configuredCharges || []) {
    const charges = chargesByProperty.get(charge.property_id) || [];
    charges.push({ name: charge.name, amount: Number(charge.amount || 0) });
    chargesByProperty.set(charge.property_id, charges);
  }
  const todayParts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Nairobi',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const todayInNairobi = `${todayParts.find((part) => part.type === 'year')?.value}-${todayParts.find((part) => part.type === 'month')?.value}-${todayParts.find((part) => part.type === 'day')?.value}`;

  for (const unit of units || []) {
    const { data: assignment } = await supabaseAdmin
      .from('unit_assignments')
      .select('tenant_id, move_in_date, expected_move_out_date, opening_arrears, migration_arrears_consumed_at, is_migrated')
      .eq('unit_id', unit.id)
      .eq('status', 'active')
      .limit(1)
      .maybeSingle();

    if (!assignment) {
      skipped.push(unit.unit_code);
      continue;
    }

    if (unit.status === 'booked') {
      const moveInDate = assignment.move_in_date ? String(assignment.move_in_date).slice(0, 10) : '';
      if (!moveInDate || moveInDate.slice(0, 7) > billingMonth) {
        skipped.push(`${unit.unit_code} (move-in is after ${billingMonth})`);
        continue;
      }
      if (moveInDate <= todayInNairobi) {
        const { error: occupancyError } = await supabaseAdmin.from('units')
          .update({ status: 'occupied', updated_at: new Date().toISOString() })
          .eq('id', unit.id)
          .eq('status', 'booked');
        if (occupancyError) throw new Error(`Failed to activate booked unit ${unit.unit_code}: ${occupancyError.message}`);
      }
    }

    if (assignment.move_in_date && String(assignment.move_in_date).slice(0, 7) === billingMonth) {
      skipped.push(`${unit.unit_code} (move-in charges are covered by the settlement for ${billingMonth})`);
      continue;
    }

    // Skip units moving out within the grace period (on or before 10th)
    if (unit.status === 'notice_given' && assignment.expected_move_out_date) {
      const graceCutoff = `${billingMonth}-10`;
      if (assignment.expected_move_out_date <= graceCutoff) {
        skipped.push(`${unit.unit_code} (notice given – moving out ${assignment.expected_move_out_date})`);
        continue;
      }
    }

    // Idempotent: skip if invoice already exists
    const { data: existingInvoice } = await supabaseAdmin
      .from('invoices')
      .select('id, status, amount_paid, publish_at, published_at')
      .eq('unit_id', unit.id)
      .eq('billing_month', billingMonth)
      .maybeSingle();

    if (existingInvoice) {
      const isUnreleased = existingInvoice.status === 'draft'
        || (!existingInvoice.published_at && (!existingInvoice.publish_at || new Date(existingInvoice.publish_at) > new Date()));
      if (!isUnreleased) {
        skipped.push(`${unit.unit_code} (already published)`);
        continue;
      }
    }

    // Carry forward outstanding balance from the most recent published invoice
    const { data: previousInvoice } = await supabaseAdmin
      .from('invoices')
      .select('outstanding_balance')
      .eq('unit_id', unit.id)
      .neq('billing_month', billingMonth)
      .in('status', ['published', 'partial', 'paid', 'unpaid'])
      .order('billing_month', { ascending: false })
      .limit(1);

    const carriedArrears = previousInvoice?.[0]
      ? Number(previousInvoice[0].outstanding_balance || 0)
      : assignment.is_migrated && assignment.migration_arrears_consumed_at ? 0 : Number(assignment.opening_arrears || 0);

    // Read the latest water readings (submitted or published)
    const { data: allReadings, error: readingsError } = await supabaseAdmin
      .from('water_readings')
      .select('meter_reading, reading_type, reading_date, created_at')
      .eq('unit_id', unit.id)
      .in('status', ['submitted', 'published'])
      .order('reading_date', { ascending: false })
      .order('created_at', { ascending: false })
      .limit(20);
    if (readingsError) throw new Error(`Failed to load water readings for ${unit.unit_code}: ${readingsError.message}`);

    const latestReading = allReadings?.find((r) => r.reading_type === 'monthly') ?? null;
    const previousReading = latestReading
      ? allReadings?.find(
          (r) =>
            r.reading_date < latestReading.reading_date ||
            (r.reading_date === latestReading.reading_date && r.created_at < latestReading.created_at)
        ) ?? null
      : null;

    const waterUnits =
      latestReading && previousReading
        ? Math.max(0, Number(latestReading.meter_reading) - Number(previousReading.meter_reading))
        : 0;

    const isMeteredWater = billingModeByProperty.get(unit.property_id) !== 'fixed_utilities'
      && Number(unit.monthly_water_rate || 0) > 0;
    const waterRate = isMeteredWater ? Number(unit.monthly_water_rate || 0) : 0;
    const [billingYear, billingMonthNumber] = billingMonth.split('-').map(Number);
    const expectedReadingMonth = new Date(Date.UTC(billingYear, billingMonthNumber - 2, 1)).toISOString().slice(0, 7);
    const waterReadingMissing = isMeteredWater && (
      !latestReading
      || !previousReading
      || String(latestReading.reading_date).slice(0, 7) !== expectedReadingMonth
      || Number(latestReading.meter_reading) < Number(previousReading.meter_reading)
    );
    const waterAmount = waterReadingMissing ? 0 : waterUnits * waterRate;
    if (waterReadingMissing) {
      skipped.push(`${unit.unit_code} (metered water reading missing or invalid for ${expectedReadingMonth}; invoice will remain unpublished)`);
    }
    const rentAmount = Number(unit.rent_amount || 0);
    const garbageAmount = Number(unit.garbage_amount || 0);
    const recurringCharges = chargesByProperty.get(unit.property_id) || [];
    const recurringChargesTotal = recurringCharges.reduce((sum, charge) => sum + charge.amount, 0);
    const totalAmountDue = rentAmount + garbageAmount + waterAmount + recurringChargesTotal + carriedArrears;
    const publishOn = getInvoiceReleaseAt(billingMonth);

    const amountPaid = Number(existingInvoice?.amount_paid || 0);
    const invoicePayload = {
      unit_id: unit.id,
      property_id: unit.property_id || propertyId,
      tenant_id: assignment.tenant_id,
      billing_month: billingMonth,
      month_start: `${billingMonth}-01`,
      month_end: new Date(
        new Date(`${billingMonth}-01`).getFullYear(),
        new Date(`${billingMonth}-01`).getMonth() + 1,
        0
      )
        .toISOString()
        .slice(0, 10),
      rent_amount: rentAmount,
      garbage_amount: garbageAmount,
      water_units: waterUnits,
      water_rate: waterRate,
      water_amount: waterAmount,
      water_reading_missing: waterReadingMissing,
      recurring_charges: recurringCharges,
      carried_arrears_amount: carriedArrears,
      total_amount_due: totalAmountDue,
      amount_paid: amountPaid,
      outstanding_balance: Math.max(0, totalAmountDue - amountPaid),
      status: 'draft',
      due_date: new Date(`${billingMonth}-10T00:00:00.000Z`).toISOString().slice(0, 10),
      publish_at: publishOn,
      published_at: null,
    };

    const { data: createdInvoice, error: createError } = existingInvoice
      ? await supabaseAdmin
          .from('invoices')
          .update({ ...invoicePayload, updated_at: new Date().toISOString() })
          .eq('id', existingInvoice.id)
          .select()
          .single()
      : await supabaseAdmin
          .from('invoices')
          .insert(invoicePayload)
          .select()
          .single();

    if (createError) {
      console.error(`[billing-generate] Failed for ${unit.unit_code}:`, createError);
      skipped.push(`${unit.unit_code} (error: ${createError.message})`);
      continue;
    }

    if (existingInvoice) invoicesUpdated++;
    else generatedInvoices.push(createdInvoice);
  }

  return { invoicesCreated: generatedInvoices.length, invoicesUpdated, skipped };
}

/**
 * Publishes all draft invoices for the given billing month.
 * Returns the count of invoices published.
 */
export async function publishDraftInvoices(
  supabaseAdmin: SupabaseClient,
  billingMonth: string,
  propertyId?: string
): Promise<number> {
  const publishedAt = getInvoiceReleaseAt(billingMonth);
  if (new Date(publishedAt) > new Date()) return 0;

  let invoicesQuery = supabaseAdmin
    .from('invoices')
    .select('id, unit_id, tenant_id, amount_paid, outstanding_balance')
    .eq('billing_month', billingMonth)
    .in('status', ['draft', 'partial', 'paid', 'unpaid'])
    .is('published_at', null)
    .eq('water_reading_missing', false);
  if (propertyId) invoicesQuery = invoicesQuery.eq('property_id', propertyId);
  const { data: invoices, error: fetchError } = await invoicesQuery;

  if (fetchError) throw new Error(fetchError.message);
  const invoiceUnitIds = [...new Set((invoices || []).map((invoice) => invoice.unit_id))];
  const [{ data: settings }, { data: assignments, error: assignmentError }] = await Promise.all([
    propertyId
      ? supabaseAdmin.from('property_billing_settings').select('first_online_billing_month').eq('property_id', propertyId).maybeSingle()
      : supabaseAdmin.from('property_billing_settings').select('first_online_billing_month').order('property_id').limit(1).maybeSingle(),
    invoiceUnitIds.length
      ? supabaseAdmin.from('unit_assignments').select('unit_id, tenant_id, is_migrated').in('unit_id', invoiceUnitIds).eq('status', 'active')
      : Promise.resolve({ data: [], error: null }),
  ]);
  if (assignmentError) throw new Error(assignmentError.message);
  const assignmentMap = new Map((assignments || []).map((assignment) => [`${assignment.unit_id}:${assignment.tenant_id}`, assignment]));
  let publishedCount = 0;
  for (const invoice of invoices || []) {
    const amountPaid = Number(invoice.amount_paid || 0);
    const outstandingBalance = Number(invoice.outstanding_balance || 0);
    const status = outstandingBalance <= 0 && amountPaid > 0
      ? 'paid'
      : amountPaid > 0 ? 'partial' : 'published';
    const { error } = await supabaseAdmin
      .from('invoices')
      .update({ status, published_at: publishedAt, publish_at: publishedAt })
      .eq('id', invoice.id)
      .is('published_at', null);
    if (error) throw new Error(error.message);
    publishedCount++;

    const assignment = assignmentMap.get(`${invoice.unit_id}:${invoice.tenant_id}`);
    if (settings?.first_online_billing_month === billingMonth && assignment?.is_migrated) {
        await supabaseAdmin
          .from('unit_assignments')
          .update({ migration_arrears_consumed_at: new Date().toISOString(), updated_at: new Date().toISOString() })
          .eq('unit_id', invoice.unit_id)
          .eq('tenant_id', invoice.tenant_id)
          .eq('status', 'active')
          .is('migration_arrears_consumed_at', null);
    }
  }
  return publishedCount;
}
