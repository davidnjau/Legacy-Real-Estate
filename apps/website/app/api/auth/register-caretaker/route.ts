import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/caretaker-invitations/complete', {
      method: 'POST',
      body: {
        token: body.token,
        code: body.code,
        name: body.fullName,
        phone: body.phone,
        password: body.password,
      },
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Caretaker registration failed.');
  }
}
