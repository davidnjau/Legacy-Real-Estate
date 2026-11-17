import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../lib/django-proxy';

type RouteContext = {
  params: Promise<{ tenantId: string }>;
};

export async function POST(request: Request, context: RouteContext) {
  try {
    const { tenantId } = await context.params;
    return await forwardDjangoRequest(request, `/tenants/${tenantId}/documents`, {
      method: 'POST',
      formData: await request.formData(),
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to upload the tenant document.');
  }
}
