import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const formData = await request.formData();
    return await forwardDjangoRequest(request, '/tenant/maintenance-tickets/create', {
      formData,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to submit your maintenance request.');
  }
}
