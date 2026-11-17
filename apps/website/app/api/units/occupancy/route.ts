import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/units/occupancy', {
      method: 'POST',
      body,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to update unit occupancy.');
  }
}
