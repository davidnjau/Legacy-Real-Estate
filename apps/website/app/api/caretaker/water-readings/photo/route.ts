import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const formData = await request.formData();
    return await forwardDjangoRequest(request, '/caretaker/water-readings/photo', { formData });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to upload meter evidence.');
  }
}
