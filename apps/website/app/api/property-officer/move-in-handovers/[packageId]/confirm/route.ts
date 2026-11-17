import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../lib/django-proxy';

type RouteContext = {
  params: Promise<{ packageId: string }>;
};

export async function POST(request: Request, context: RouteContext) {
  try {
    const { packageId } = await context.params;
    if (!/^\d+$/.test(packageId)) {
      return Response.json({ error: 'A valid move-in package ID is required.' }, { status: 400 });
    }
    return await forwardDjangoRequest(request, `/property-officer/move-in-handovers/${packageId}/confirm`, {
      method: 'POST',
      body: {},
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to confirm this move-in handover.');
  }
}
