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
    const body = await request.json();
    return await forwardDjangoRequest(request, `/caretaker/checklists/${packageId}/submit`, { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to submit the move-in handover.');
  }
}
