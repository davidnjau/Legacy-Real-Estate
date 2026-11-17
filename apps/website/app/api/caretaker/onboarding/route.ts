import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/caretaker/onboarding');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load caretaker onboarding.');
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json().catch(() => ({}));
    return await forwardDjangoRequest(request, '/caretaker/onboarding', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to update caretaker onboarding.');
  }
}
