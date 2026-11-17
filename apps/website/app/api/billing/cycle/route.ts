import { NextResponse } from 'next/server';
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
  const now = new Date();
  const currentMonth = monthFromDate(now);
  const releaseAt = getInvoiceReleaseAt(currentMonth);

  return NextResponse.json({
    currentMonth,
    previousMonth: previousMonth(currentMonth),
    nextMonth: nextMonth(currentMonth),
    releaseAt,
    isReleased: new Date(releaseAt) <= now,
  });
}
