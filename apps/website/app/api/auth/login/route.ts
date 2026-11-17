import { NextResponse } from 'next/server';

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000/api/v1';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      return NextResponse.json({ error: 'A JSON object is required.' }, { status: 400 });
    }
    const identifier = typeof body.identifier === 'string'
      ? body.identifier.trim()
      : typeof body.email === 'string'
        ? body.email.trim()
        : '';
    const password = typeof body.password === 'string' ? body.password : '';

    if (!identifier || !password) {
      return NextResponse.json(
        { error: 'Email or phone number and password are required.' },
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
      body: JSON.stringify({ identifier, password }),
      cache: 'no-store',
    });

    const result = new NextResponse(await response.text(), {
      status: response.status,
      headers: { 'Content-Type': response.headers.get('content-type') || 'application/json' },
    });
    if (response.ok) {
      result.cookies.set('demo_role', '', { path: '/', maxAge: 0 });
    }
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
