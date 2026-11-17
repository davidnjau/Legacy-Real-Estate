import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/landlord/billing-settings');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load migration billing settings.');
  }
}

export async function PATCH(request: Request) {
  try {
    const body = await request.json().catch(() => ({}));
    return await forwardDjangoRequest(request, '/landlord/billing-settings', {
      method: 'PATCH',
      body,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to update migration billing settings.');
  }
}
