import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const contentType = request.headers.get('content-type') || '';
    if (contentType.includes('multipart/form-data') || contentType.includes('application/x-www-form-urlencoded')) {
      const formData = await request.formData();
      return await forwardDjangoRequest(request, '/property-officer/tenant-onboarding', { formData: formData as FormData });
    }
    const body = await request.json();
    return await forwardDjangoRequest(request, '/property-officer/tenant-onboarding', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to complete tenant onboarding.');
  }
}
