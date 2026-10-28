import { NextResponse } from 'next/server';
import { requireApiRole } from '../../../../lib/api-auth';
import { getInvoiceReleaseAt } from '../../../../lib/billing-schedule';

function monthFromDate(date: Date) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Africa/Nairobi', year: 'numeric', month: '2-digit' }).format(date);
}

function nextMonth(month: string) {
  const [year, value] = month.split('-').map(Number);
  return new Date(Date.UTC(year, value, 1)).toISOString().slice(0, 7);
}

function previousMonth(month: string) {
  const [year, value] = month.split('-').map(Number);
  return new Date(Date.UTC(year, value - 2, 1)).toISOString().slice(0, 7);
}

export async function GET() {
  const auth = await requireApiRole(['landlord', 'caretaker', 'tenant']);
  if (auth.response) return auth.response;

  const now = new Date();
  const currentMonth = monthFromDate(now);
  const releaseAt = getInvoiceReleaseAt(currentMonth);
  const released = new Date(releaseAt) <= now;

  return NextResponse.json({
    now: now.toISOString(),
    currentMonth,
    previousMonth: previousMonth(currentMonth),
    preparationMonth: released ? nextMonth(currentMonth) : currentMonth,
    releaseAt,
    released,
    timeZone: 'Africa/Nairobi',
  });
}
