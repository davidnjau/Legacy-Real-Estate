import { NextResponse } from 'next/server';
import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../../lib/django-proxy';

type RouteContext = {
  params: Promise<{ quotationId: string }>;
};

export async function POST(request: Request, context: RouteContext) {
  const { quotationId } = await context.params;
  if (!/^\d+$/.test(quotationId)) {
    return NextResponse.json({ error: 'Landlord quotation was not found.' }, { status: 404 });
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
      `/landlord/maintenance/quotations/${quotationId}/decision`,
      { method: 'POST', body },
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to submit the landlord quotation decision.');
  }
}
