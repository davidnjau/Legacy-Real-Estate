import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/maintenance-ledger/months');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load available maintenance ledger months.');
  }
}
