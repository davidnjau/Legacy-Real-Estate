import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const response = await forwardDjangoRequest(request, '/auth/register-landlord', {
      method: 'POST',
      body: {
        name: body.fullName || body.name,
        email: body.email,
        phone: body.phone,
        password: body.password,
        property_name: body.propertyName || body.property_name,
      },
    });
    if (response.ok) {
      response.cookies.delete('demo_role');
      response.cookies.set('demo_role', '', { path: '/', maxAge: 0 });
    }
    return response;
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to register the landlord account.');
  }
}
