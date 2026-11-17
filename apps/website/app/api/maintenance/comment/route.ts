import { NextResponse } from 'next/server';
import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    const ticketId = new URL(request.url).searchParams.get('ticketId');
    if (!ticketId || !/^\d+$/.test(ticketId)) {
      return NextResponse.json({ error: 'A valid ticket ID is required.' }, { status: 400 });
    }
    return await forwardDjangoRequest(request, `/maintenance-tickets/${ticketId}/comments`);
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load the request conversation.');
  }
}

export async function POST(request: Request) {
  try {
    const payload = await request.json() as { ticketId?: unknown; message?: unknown };
    const ticketId = String(payload.ticketId ?? '');
    if (
      !/^\d+$/.test(ticketId)
      || typeof payload.message !== 'string'
    ) {
      return NextResponse.json({ error: 'A ticket ID and message are required.' }, { status: 400 });
    }
    return await forwardDjangoRequest(
      request,
      `/maintenance-tickets/${ticketId}/comments`,
      { method: 'POST', body: { body: payload.message } },
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to send your message.');
  }
}
