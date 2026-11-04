import { NextResponse } from 'next/server';
import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/tenant-activation');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load tenant activation codes.');
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const unitId = String(body.unitId || '');
    if (!/^\d+$/.test(unitId)) {
      return NextResponse.json({ error: 'A valid unit ID is required.' }, { status: 400 });
    }
    const response = await forwardDjangoRequest(
      request,
      `/units/${unitId}/tenant-activation`,
      { method: 'POST', body: {} },
    );
    if (!response.ok) return response;
    const data = await response.json();
    const result = NextResponse.json({
      ...data,
      success: true,
      claimCode: data.activation_code,
      activationToken: data.activation_token,
      expiresAt: data.expires_at,
    }, { status: response.status });
    for (const cookie of response.headers.getSetCookie()) {
      result.headers.append('Set-Cookie', cookie);
    }
    return result;
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to create a tenant activation code.');
  }
}
