import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';
import { NextResponse } from 'next/server';

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

function djangoPath(segments: string[], method: string) {
  if (method === 'GET' && segments.length === 1 && segments[0] === 'workflows') {
    return '/move-out-workflows';
  }
  if (method === 'GET' && segments.length === 1 && segments[0] === 'tenancies') {
    return '/tenancies';
  }
  if (method === 'GET' && segments.length === 1 && segments[0] === 'inspections') {
    return '/inspections';
  }
  if (method === 'POST' && segments.length === 1 && segments[0] === 'notices') {
    return '/move-out-notices/create';
  }
  if (
    method === 'POST' &&
    segments.length === 3 &&
    segments[0] === 'notices' &&
    /^\d+$/.test(segments[1]) &&
    segments[2] === 'decision'
  ) {
    return `/move-out-notices/${segments[1]}/decision`;
  }
  if (method === 'POST' && segments.length === 1 && segments[0] === 'settlements') {
    return '/move-out-settlements/create';
  }
  if (
    method === 'POST' &&
    segments.length === 3 &&
    segments[0] === 'settlements' &&
    /^\d+$/.test(segments[1]) &&
    ['decision', 'payout'].includes(segments[2])
  ) {
    return `/move-out-settlements/${segments[1]}/${segments[2]}`;
  }
  return null;
}

async function proxy(request: Request, context: RouteContext) {
  const { path } = await context.params;
  const target = djangoPath(path, request.method);
  if (!target) {
    return NextResponse.json({ error: 'Move-out endpoint not found.' }, { status: 404 });
  }
  const query = request.method === 'GET' ? new URL(request.url).search : '';
  let body: unknown;
  if (!['GET', 'HEAD'].includes(request.method)) {
    try {
      body = await request.json();
    } catch {
      return NextResponse.json({ error: 'A valid JSON request body is required.' }, { status: 400 });
    }
  }
  try {
    return await forwardDjangoRequest(request, `${target}${query}`, {
      method: request.method,
      body,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to process the move-out request.');
  }
}

export async function GET(request: Request, context: RouteContext) {
  return proxy(request, context);
}

export async function POST(request: Request, context: RouteContext) {
  return proxy(request, context);
}
