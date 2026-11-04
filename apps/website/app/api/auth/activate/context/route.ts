import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    const token = new URL(request.url).searchParams.get('token') || '';
    return await forwardDjangoRequest(
      request,
      `/tenant-activation/context?token=${encodeURIComponent(token)}`,
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to verify the tenant activation link.');
  }
}
