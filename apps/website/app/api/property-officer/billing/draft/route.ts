import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/property-officer/billing/draft', {
      method: 'POST',
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to prepare invoice drafts.');
  }
}
