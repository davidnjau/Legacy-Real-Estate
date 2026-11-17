import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../lib/django-proxy';
import { NextResponse } from 'next/server';

type RouteContext = {
  params: Promise<{ noticeId: string }>;
};

export async function POST(request: Request, context: RouteContext) {
  const { noticeId } = await context.params;
  if (!/^\d+$/.test(noticeId)) {
    return NextResponse.json({ error: 'Move-out notice not found.' }, { status: 404 });
  }
  try {
    return await forwardDjangoRequest(
      request,
      `/tenant/move-out-notices/${noticeId}/withdraw`,
      { method: 'POST', body: {} },
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to withdraw your move-out notice.');
  }
}
