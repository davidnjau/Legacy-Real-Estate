import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../../lib/django-proxy';

type RouteContext = {
  params: Promise<{ tenantId: string; documentId: string }>;
};

export async function GET(request: Request, context: RouteContext) {
  try {
    const { tenantId, documentId } = await context.params;
    return await forwardDjangoRequest(
      request,
      `/tenants/${tenantId}/documents/${documentId}`,
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to download the tenant document.');
  }
}
