import { djangoProxyFailure, forwardDjangoRequest } from '../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/caretaker/meter-readings');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load water readings.');
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/caretaker/meter-readings', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to save water readings.');
  }
}
