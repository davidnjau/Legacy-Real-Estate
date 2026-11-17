import { djangoProxyFailure, forwardDjangoRequest } from '../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    const url = new URL(request.url);
    const month = url.searchParams.get('month');
    const path = month ? `/maintenance-ledger?month=${encodeURIComponent(month)}` : '/maintenance-ledger';
    return await forwardDjangoRequest(request, path);
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load maintenance ledger.');
  }
}
