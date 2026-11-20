import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/payments/rent/stk-push', {
      method: 'POST',
      body,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to initiate rent M-Pesa STK push.');
  }
}
