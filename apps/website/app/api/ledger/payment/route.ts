import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/payments/create', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to record payment.');
  }
}
