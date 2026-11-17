import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/tenant/dashboard');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load your tenant dashboard.');
  }
}
