import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/tenant-activation/complete', {
      method: 'POST',
      body: {
        token: body.token,
        code: body.code,
        unit_code: body.unitCode,
        full_name: body.fullName,
        email: body.email,
        phone: body.phone,
        password: body.password,
      },
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to activate the tenant account.');
  }
}
