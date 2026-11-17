import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    const query = new URL(request.url).searchParams.toString();
    return await forwardDjangoRequest(
      request,
      `/landlord/statements${query ? `?${query}` : ''}`,
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load landlord statement.');
  }
}
