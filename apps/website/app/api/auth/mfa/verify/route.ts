import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const response = await forwardDjangoRequest(request, '/auth/mfa/verify', { body });
    if (response.ok) {
      response.cookies.delete('demo_role');
      response.cookies.set('demo_role', '', { path: '/', maxAge: 0 });
    }
    return response;
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to verify the sign-in code.');
  }
}
