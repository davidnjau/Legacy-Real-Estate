import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../lib/django-proxy';

export async function GET(
  request: Request,
  context: { params: Promise<{ ticketId: string; index: string }> },
) {
  try {
    const { ticketId, index } = await context.params;
    if (!/^\d+$/.test(ticketId) || !/^\d+$/.test(index)) {
      return new Response('Evidence not found.', { status: 404 });
    }
    return await forwardDjangoRequest(
      request,
      `/maintenance-tickets/${ticketId}/evidence/${index}`,
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load maintenance evidence.');
  }
}
