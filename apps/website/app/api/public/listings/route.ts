import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    const query = new URL(request.url).search;
    return await forwardDjangoRequest(request, `/public/vacancies${query}`);
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load current listings.');
  }
}
