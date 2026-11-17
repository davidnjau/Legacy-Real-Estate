import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../lib/django-proxy';

type RouteContext = {
  params: Promise<{ tenancyId: string }>;
};

export async function POST(request: Request, context: RouteContext) {
  try {
    const { tenancyId } = await context.params;
    return await forwardDjangoRequest(request, `/tenancies/${tenancyId}/tenant-activation`, {
      method: 'POST',
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to issue tenant portal activation.');
  }
}
