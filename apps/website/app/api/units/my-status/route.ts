import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/tenant/unit-status');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load your unit status.');
  }
}
