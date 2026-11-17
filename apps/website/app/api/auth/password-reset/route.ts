import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/auth/password-reset', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to request a password reset.');
  }
}
