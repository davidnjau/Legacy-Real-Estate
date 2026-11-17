import { NextResponse } from 'next/server';
import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

async function proxy(request: Request, context: RouteContext) {
  const { path } = await context.params;
  if (
    path.length !== 2 ||
    !/^\d+$/.test(path[0]) ||
    !['decision', 'payout'].includes(path[1]) ||
    request.method !== 'POST'
  ) {
    return NextResponse.json({ error: 'Landlord remittance endpoint not found.' }, { status: 404 });
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
      `/landlord/remittances/${path[0]}/${path[1]}`,
      { method: 'POST', body },
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to update the landlord remittance.');
  }
}

export async function POST(request: Request, context: RouteContext) {
  return proxy(request, context);
}
