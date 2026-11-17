import { NextResponse } from 'next/server';
import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

async function forwardJsonRequest(request: Request, method: 'POST' | 'PATCH') {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'Expected a JSON request body.' }, { status: 400 });
  }
  if (typeof body !== 'object' || body === null || Array.isArray(body)) {
    return NextResponse.json({ error: 'Expected a JSON object.' }, { status: 400 });
  }

  try {
    return await forwardDjangoRequest(request, '/landlord/properties', { method, body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to save property details.');
  }
}

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/landlord/properties');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load your properties.');
  }
}

export async function POST(request: Request) {
  return forwardJsonRequest(request, 'POST');
}

export async function PATCH(request: Request) {
  return forwardJsonRequest(request, 'PATCH');
}
