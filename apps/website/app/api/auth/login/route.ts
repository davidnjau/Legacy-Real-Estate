import { NextResponse } from 'next/server';

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000/api/v1';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const email = String(body.email || '').trim().toLowerCase();
    const password = String(body.password || '');

    if (!email || !password) {
      return NextResponse.json(
        { error: 'Email and password are required.' },
        { status: 400 }
      );
    }

    const response = await fetch(`${apiBaseUrl}/auth/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Cookie: request.headers.get('cookie') || '',
        'X-CSRFToken': request.headers.get('x-csrftoken') || '',
      },
      body: JSON.stringify({ identifier: email, password }),
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
  } catch (error) {
    console.error('Login failed:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'An unexpected error occurred during login.' },
      { status: 500 }
    );
  }
}
