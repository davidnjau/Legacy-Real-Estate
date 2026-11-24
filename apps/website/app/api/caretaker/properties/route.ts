import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/caretaker/properties');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load your assigned properties.');
  }
}

export async function PATCH(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/caretaker/properties', {
      method: 'PATCH',
      body,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to select the active property.');
  }
}
