import { NextResponse } from 'next/server';

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000/api/v1';

export async function POST(request: Request) {
  const response = await fetch(`${apiBaseUrl}/auth/logout`, {
    method: 'POST',
    headers: {
      Cookie: request.headers.get('cookie') || '',
      'X-CSRFToken': request.headers.get('x-csrftoken') || '',
    },
    cache: 'no-store',
  });
  const result = new NextResponse(await response.text(), {
    status: response.status,
    headers: { 'Content-Type': response.headers.get('content-type') || 'application/json' },
  });
  for (const cookie of response.headers.getSetCookie()) {
    result.headers.append('Set-Cookie', cookie);
  }
  return result;
}
