import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const formData = await request.formData();
    return await forwardDjangoRequest(request, '/property-officer/imports', {
      method: 'POST',
      formData,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to process the agency import.');
  }
}
