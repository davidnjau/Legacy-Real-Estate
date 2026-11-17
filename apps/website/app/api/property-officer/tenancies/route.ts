import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/tenancies');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load tenancy records.');
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/tenancies/create', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to create the tenancy.');
  }
}
