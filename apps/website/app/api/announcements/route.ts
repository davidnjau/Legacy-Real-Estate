import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000/api/v1';

async function backendRequest(path: string, init: RequestInit = {}) {
  const cookieStore = await cookies();
  return fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      Cookie: cookieStore.toString(),
      ...(init.headers || {}),
    },
    cache: 'no-store',
  });
}

async function jsonResponse(response: Response) {
  return new NextResponse(await response.text(), {
    status: response.status,
    headers: { 'Content-Type': response.headers.get('content-type') || 'application/json' },
  });
}

export async function GET() {
  return jsonResponse(await backendRequest('/announcements'));
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const propertiesResponse = await backendRequest('/properties');
    if (!propertiesResponse.ok) return jsonResponse(propertiesResponse);

    const propertiesPayload = await propertiesResponse.json();
    const property = propertiesPayload.properties?.[0];
    if (!property) {
      return NextResponse.json({ error: 'No accessible property is available.' }, { status: 403 });
    }

    const createResponse = await backendRequest('/announcements/create', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': request.headers.get('x-csrftoken') || '',
      },
      body: JSON.stringify({
        branch_id: property.branch_id,
        property_id: property.id,
        title: body.title,
        body: body.message,
        publish_at: new Date().toISOString(),
        expires_at: body.expiresAt,
      }),
    });
    if (!createResponse.ok) return jsonResponse(createResponse);

    const createdPayload = await createResponse.json();
    const publishResponse = await backendRequest(
      `/announcements/${createdPayload.announcement.id}/publish`,
      {
        method: 'POST',
        headers: { 'X-CSRFToken': request.headers.get('x-csrftoken') || '' },
      },
    );
    return jsonResponse(publishResponse);
  } catch (error) {
    console.error('Failed to proxy announcement request:', error);
    return NextResponse.json({ error: 'Unable to process the announcement request.' }, { status: 500 });
  }
}
