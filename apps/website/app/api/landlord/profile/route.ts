import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/landlord/profile');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load your landlord profile.');
  }
}

export async function PATCH(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/landlord/profile', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to update your landlord profile.');
  }
}
