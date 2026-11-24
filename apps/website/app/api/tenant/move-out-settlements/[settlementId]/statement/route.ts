import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../lib/django-proxy';

type RouteContext = {
  params: Promise<{ settlementId: string }>;
};

export async function GET(request: Request, context: RouteContext) {
  try {
    const { settlementId } = await context.params;
    if (!/^\d+$/.test(settlementId)) {
      return new Response('Settlement statement not found.', { status: 404 });
    }
    return await forwardDjangoRequest(
      request,
      `/tenant/move-out-settlements/${settlementId}/statement`,
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load the move-out settlement statement.');
  }
}
