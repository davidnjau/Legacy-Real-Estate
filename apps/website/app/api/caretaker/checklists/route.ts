import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/caretaker/checklists');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load move-in handovers.');
  }
}
