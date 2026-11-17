import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/caretaker-invitations/complete', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to activate the caretaker account.');
  }
}
