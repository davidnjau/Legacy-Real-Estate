import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/landlord/onboarding');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load landlord onboarding.');
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json().catch(() => ({}));
    return await forwardDjangoRequest(request, '/landlord/onboarding', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to update landlord onboarding.');
  }
}
