import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/property-officer/move-in-handovers');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load move-in handovers awaiting review.');
  }
}
