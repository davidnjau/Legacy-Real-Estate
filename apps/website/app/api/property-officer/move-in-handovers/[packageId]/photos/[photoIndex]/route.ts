import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../../lib/django-proxy';

type RouteContext = {
  params: Promise<{ packageId: string; photoIndex: string }>;
};

export async function GET(request: Request, context: RouteContext) {
  try {
    const { packageId, photoIndex } = await context.params;
    if (!/^\d+$/.test(packageId) || !/^\d+$/.test(photoIndex)) {
      return Response.json({ error: 'Valid package and photo IDs are required.' }, { status: 400 });
    }
    return await forwardDjangoRequest(
      request,
      `/property-officer/move-in-handovers/${packageId}/photos/${photoIndex}`,
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load the move-in handover photo.');
  }
}
