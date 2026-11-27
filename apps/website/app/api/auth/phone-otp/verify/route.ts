import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/auth/phone-otp/verify', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to verify the sign-in code.');
  }
}
