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
  let formData: FormData;
  try {
    formData = await request.formData();
  } catch {
    return NextResponse.json({ error: 'Inspection photos are required.' }, { status: 400 });
  }
  try {
    return await forwardDjangoRequest(
      request,
      `/inspections/${inspectionId}/photos`,
      { method: 'POST', formData },
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to upload inspection photos.');
  }
}
