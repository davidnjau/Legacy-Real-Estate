import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/property-officer/viewings');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load property viewings.');
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/property-officer/viewings', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to schedule the property viewing.');
  }
}
