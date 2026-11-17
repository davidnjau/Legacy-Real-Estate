import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../../lib/django-proxy';

type RouteContext = {
  params: Promise<{ inspectionId: string; photoIndex: string }>;
};

export async function GET(request: Request, context: RouteContext) {
  try {
    const { inspectionId, photoIndex } = await context.params;
    if (!/^\d+$/.test(inspectionId) || !/^\d+$/.test(photoIndex)) {
      return new Response('Inspection photo not found.', { status: 404 });
    }
    return await forwardDjangoRequest(
      request,
      `/landlord/inspections/${inspectionId}/photos/${photoIndex}`,
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load the inspection photo.');
  }
}
