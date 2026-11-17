import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../lib/django-proxy';

type RouteContext = {
  params: Promise<{ tenancyId: string }>;
};

export async function GET(request: Request, context: RouteContext) {
  try {
    const { tenancyId } = await context.params;
    return await forwardDjangoRequest(request, `/tenancies/${tenancyId}/move-in-agreement.pdf`);
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to download the move-in agreement.');
  }
}
