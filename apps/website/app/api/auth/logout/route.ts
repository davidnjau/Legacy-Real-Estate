import { NextResponse } from 'next/server';
import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const response = await forwardDjangoRequest(request, '/auth/logout', { body: {} });
    response.cookies.delete('demo_role');
    return response;
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to sign out.');
  }
}

export async function GET(request: Request) {
  try {
    const logoutResponse = await forwardDjangoRequest(request, '/auth/logout', {
      method: 'POST',
      body: {},
    });
    if (!logoutResponse.ok && logoutResponse.status !== 401) {
      return logoutResponse;
    }
    const response = new NextResponse(null, {
      status: 303,
      headers: { Location: '/login' },
    });
    for (const cookie of logoutResponse.headers.getSetCookie()) {
      response.headers.append('Set-Cookie', cookie);
    }
    response.cookies.delete('demo_role');
    response.cookies.delete('sessionid');
    return response;
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to sign out.');
  }
}
