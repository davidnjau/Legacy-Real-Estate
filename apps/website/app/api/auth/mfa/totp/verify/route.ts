import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/auth/mfa/totp/verify', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to verify the authenticator code.');
  }
}
