import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/landlord/billing-settings');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load billing settings.');
  }
}

export async function PATCH(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/landlord/billing-settings', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to save billing settings.');
  }
}
