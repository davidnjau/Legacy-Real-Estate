import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    const query = new URL(request.url).searchParams.toString();
    return await forwardDjangoRequest(request, `/director/settings${query ? `?${query}` : ''}`);
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load agency settings.');
  }
}

export async function PATCH(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/director/settings', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to save agency settings.');
  }
}
