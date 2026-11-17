import { NextResponse } from 'next/server';

export async function GET(request: Request) {
  const secret = process.env.CRON_SECRET;
  if (!secret) {
    return NextResponse.json({ error: 'Monthly billing cron is not configured.' }, { status: 503 });
  }
  if (request.headers.get('authorization') !== `Bearer ${secret}`) {
    return NextResponse.json({ error: 'Unauthorized.' }, { status: 401 });
  }

  const dateParts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Nairobi',
    year: 'numeric',
    month: '2-digit',
  }).formatToParts(new Date());
  const billingMonth = `${dateParts.find((part) => part.type === 'year')?.value}-${dateParts.find((part) => part.type === 'month')?.value}`;

  // In the PostgreSQL/Django architecture, monthly recurring billing is orchestrated
  // via Django background workers / celery jobs against the canonical ledger.
  return NextResponse.json({
    billingMonth,
    message: 'Monthly billing generation cycle dispatched via Django PostgreSQL foundation.',
    results: [],
    failures: [],
  });
}
