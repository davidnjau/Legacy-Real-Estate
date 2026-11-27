import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/tenant-activation/complete', {
      method: 'POST',
      body: {
        code: body.code,
        unit_code: body.unitCode,
        phone: body.phone,
        national_id_or_passport: body.nationalIdOrPassport,
      },
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to activate the tenant account.');
  }
}
