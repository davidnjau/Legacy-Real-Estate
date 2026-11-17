import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../../lib/django-proxy';

type RouteContext = {
  params: Promise<{ settlementId: string; photoIndex: string }>;
};

export async function GET(request: Request, context: RouteContext) {
  try {
    const { settlementId, photoIndex } = await context.params;
    if (!/^\d+$/.test(settlementId) || !/^\d+$/.test(photoIndex)) {
      return new Response('Inspection photo not found.', { status: 404 });
    }
    return await forwardDjangoRequest(
      request,
      `/tenant/move-out-settlements/${settlementId}/photos/${photoIndex}`,
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load the move-out inspection photo.');
  }
}
