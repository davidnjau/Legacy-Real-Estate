import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/tenant-activation/verify', {
      method: 'POST',
      body: { unit_code: body.unitCode, code: body.code },
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to verify this tenant activation code.');
  }
}
