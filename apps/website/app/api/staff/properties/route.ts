import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/properties');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load accessible properties.');
  }
}
