import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../lib/django-proxy';

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ leadId: string }> },
) {
  try {
    const { leadId } = await params;
    if (!/^\d+$/.test(leadId)) {
      return Response.json({ error: 'A valid lead ID is required.' }, { status: 400 });
    }
    const body = await request.json();
    return await forwardDjangoRequest(request, `/property-officer/leads/${leadId}/status`, {
      method: 'POST',
      body,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to update the lead status.');
  }
}
