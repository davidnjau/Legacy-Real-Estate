import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ viewingId: string }> },
) {
  try {
    const { viewingId } = await params;
    if (!/^\d+$/.test(viewingId)) {
      return Response.json({ error: 'A valid viewing ID is required.' }, { status: 400 });
    }
    const body = await request.json();
    return await forwardDjangoRequest(request, `/property-officer/viewings/${viewingId}`, {
      method: 'PATCH',
      body,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to update the property viewing.');
  }
}
