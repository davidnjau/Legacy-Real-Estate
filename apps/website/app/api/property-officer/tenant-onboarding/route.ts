import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/property-officer/tenant-onboarding', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to complete tenant onboarding.');
  }
}
