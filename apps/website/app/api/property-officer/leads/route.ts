import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/property-officer/leads');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load property leads.');
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/property-officer/leads', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to create the property lead.');
  }
}
