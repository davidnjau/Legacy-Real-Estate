import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/auth/me');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to verify the current user.');
  }
}
