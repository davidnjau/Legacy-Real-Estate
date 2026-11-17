import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    const query = new URL(request.url).searchParams.toString();
    return await forwardDjangoRequest(request, `/properties${query ? `?${query}` : ''}`);
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load properties.');
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/properties/create', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to create the property.');
  }
}
