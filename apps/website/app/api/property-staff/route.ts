import { djangoProxyFailure, forwardDjangoRequest } from '../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/properties');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load property staff records.');
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/caretakers/create', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to assign property staff.');
  }
}
