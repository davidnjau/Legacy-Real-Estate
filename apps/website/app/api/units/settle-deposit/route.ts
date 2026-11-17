import { NextResponse } from 'next/server';

export async function POST() {
  return NextResponse.json(
    { error: 'Deposit settlement now uses the audited Django move-out workflow.' },
    { status: 410 },
  );
}
