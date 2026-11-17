import { NextResponse } from 'next/server';
import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json().catch(() => ({}));
    return await forwardDjangoRequest(request, '/landlord/billing-settings', {
      method: 'POST',
      body,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to generate monthly invoices.');
  }
}
