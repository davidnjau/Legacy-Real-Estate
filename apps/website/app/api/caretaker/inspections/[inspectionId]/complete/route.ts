import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../lib/django-proxy';
import { NextResponse } from 'next/server';

type RouteContext = {
  params: Promise<{ inspectionId: string }>;
};

export async function POST(request: Request, context: RouteContext) {
  const { inspectionId } = await context.params;
  if (!/^\d+$/.test(inspectionId)) {
    return NextResponse.json({ error: 'Inspection not found.' }, { status: 404 });
  }
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'A valid JSON request body is required.' }, { status: 400 });
  }
  try {
    return await forwardDjangoRequest(
      request,
      `/inspections/${inspectionId}/complete`,
      { method: 'POST', body },
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to submit the exit inspection.');
  }
}
