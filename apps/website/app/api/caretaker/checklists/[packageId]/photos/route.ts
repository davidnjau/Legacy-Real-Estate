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
    const formData = await request.formData();
    return await forwardDjangoRequest(request, `/caretaker/checklists/${packageId}/photos`, { formData });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to upload move-in handover photos.');
  }
}
