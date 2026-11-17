import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/tenant/move-out-notice');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load your move-out notice.');
  }
}

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: 'A valid JSON request body is required.' }, { status: 400 });
  }
  try {
    return await forwardDjangoRequest(request, '/tenant/move-out-notice/create', {
      method: 'POST',
      body,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to submit your move-out notice.');
  }
}
