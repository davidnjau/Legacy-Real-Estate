import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/caretaker/water-readings');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load water readings state.');
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/caretaker/water-readings/drafts', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to submit water meter readings.');
  }
}

export async function PATCH(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/caretaker/water-readings/publish', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to publish water meter readings.');
  }
}
