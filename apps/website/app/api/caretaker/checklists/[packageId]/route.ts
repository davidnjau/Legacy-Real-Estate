import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

type RouteContext = { params: Promise<{ packageId: string }> };

export async function PATCH(request: Request, { params }: RouteContext) {
  try {
    const { packageId } = await params;
    if (!/^\d+$/.test(packageId)) {
      return Response.json({ error: 'A valid checklist item ID is required.' }, { status: 400 });
    }
    const body = await request.json();
    return await forwardDjangoRequest(request, `/caretaker/checklists/${packageId}`, { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to update the caretaker checklist item.');
  }
}
