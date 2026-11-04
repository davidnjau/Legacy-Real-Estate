import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/auth/register-landlord', {
      method: 'POST',
      body: {
        name: body.fullName,
        email: body.email,
        phone: body.phone,
        password: body.password,
        property_name: body.propertyName,
      },
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to register the landlord account.');
  }
}
