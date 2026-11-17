import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/tenant/profile');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load your tenant profile.');
  }
}

export async function PATCH(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/tenant/profile', {
      method: 'PATCH',
      body,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to update your tenant profile.');
  }
}
