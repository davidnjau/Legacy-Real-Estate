import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    const query = new URL(request.url).searchParams.toString();
    return await forwardDjangoRequest(request, `/property-officer/move-in-quote${query ? `?${query}` : ''}`);
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to calculate the move-in amount.');
  }
}
