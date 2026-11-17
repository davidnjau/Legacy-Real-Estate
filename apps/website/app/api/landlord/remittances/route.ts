import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    return await forwardDjangoRequest(request, '/landlord/remittances');
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load landlord remittances.');
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
    return await forwardDjangoRequest(request, '/landlord/remittances/create', {
      method: 'POST',
      body,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to prepare the landlord remittance.');
  }
}
