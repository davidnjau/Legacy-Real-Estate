import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

type RouteContext = { params: Promise<{ ticketId: string }> };

export async function POST(request: Request, { params }: RouteContext) {
  try {
    const { ticketId } = await params;
    if (!/^\d+$/.test(ticketId)) {
      return Response.json({ error: 'A valid ticket ID is required.' }, { status: 400 });
    }
    const formData = await request.formData();
    return await forwardDjangoRequest(
      request,
      `/maintenance-tickets/${ticketId}/completion-photos`,
      { formData },
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to upload completion photos.');
  }
}
