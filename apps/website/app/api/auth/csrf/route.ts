import { NextResponse } from 'next/server';

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000/api/v1';

export async function GET(request: Request) {
  const response = await fetch(`${apiBaseUrl}/auth/csrf`, {
    headers: { Cookie: request.headers.get('cookie') || '' },
    cache: 'no-store',
  });
  const payload = await response.text();
  const result = new NextResponse(payload, {
    status: response.status,
    headers: { 'Content-Type': response.headers.get('content-type') || 'application/json' },
  });

  for (const cookie of response.headers.getSetCookie()) {
    result.headers.append('Set-Cookie', cookie);
  }
  return result;
}
