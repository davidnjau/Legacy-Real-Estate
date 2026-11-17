import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

const endpoint = '/landlord/move-in-settlements';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, endpoint);
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load move-in settlements.');
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, endpoint, { method: 'POST', body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to create the move-in reservation.');
  }
}

export async function PATCH(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, endpoint, { method: 'PATCH', body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to update the move-in settlement.');
  }
}

export async function DELETE(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, endpoint, { method: 'DELETE', body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to cancel the move-in settlement.');
  }
}
