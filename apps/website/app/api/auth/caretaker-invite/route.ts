import { NextResponse } from 'next/server';
import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    const url = new URL(request.url);
    const token = url.searchParams.get('token');
    const propertyId = url.searchParams.get('propertyId');
    const path = token
      ? `/caretaker-invitations/verify?token=${encodeURIComponent(token)}`
      : `/caretaker-invitations${propertyId ? `?property_id=${encodeURIComponent(propertyId)}` : ''}`;
    return await forwardDjangoRequest(request, path);
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load caretaker invitation information.');
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/caretaker-invitations/create', {
      method: 'POST',
      body: {
        email: body.email,
        property_id: body.propertyId,
      },
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to create the caretaker invitation.');
  }
}

export async function PUT(request: Request) {
  try {
    const body = await request.json();
    const inviteId = String(body.inviteId || '');
    if (!/^\d+$/.test(inviteId)) {
      return NextResponse.json({ error: 'A valid invitation ID is required.' }, { status: 400 });
    }
    return await forwardDjangoRequest(request, `/caretaker-invitations/${inviteId}/resend`, {
      method: 'POST',
      body: {},
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to regenerate the caretaker invitation link.');
  }
}

export async function DELETE(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/caretaker-invitations/revoke', {
      method: 'POST',
      body: {
        property_id: body.propertyId,
        caretaker_id: body.caretakerId,
      },
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to remove caretaker property access.');
  }
}
