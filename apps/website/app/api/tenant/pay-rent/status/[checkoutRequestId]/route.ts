import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../lib/django-proxy';

export async function GET(
  request: Request,
  props: { params: Promise<{ checkoutRequestId: string }> }
) {
  try {
    const params = await props.params;
    return await forwardDjangoRequest(
      request,
      `/payments/rent/stk-status/${encodeURIComponent(params.checkoutRequestId)}`
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to check M-Pesa payment status.');
  }
}
