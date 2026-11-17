import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../lib/django-proxy';

type RouteContext = {
  params: Promise<{ waiverId: string }>;
};

export async function POST(request: Request, context: RouteContext) {
  try {
    const { waiverId } = await context.params;
    const body = await request.json();
    return await forwardDjangoRequest(
      request,
      `/property-officer/billing/waivers/${encodeURIComponent(waiverId)}`,
      { method: 'POST', body },
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to decide the invoice waiver.');
  }
}
