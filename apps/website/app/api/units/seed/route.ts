import { NextResponse } from 'next/server';

export async function POST() {
  return NextResponse.json({
    success: true,
    message: 'Units and properties are managed via canonical Django migrations and management commands.',
  });
}
