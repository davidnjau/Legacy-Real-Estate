import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../../lib/django-proxy';

type RouteContext = {
  params: Promise<{ invoiceId: string }>;
};

export async function POST(request: Request, context: RouteContext) {
  try {
    const { invoiceId } = await context.params;
    return await forwardDjangoRequest(
      request,
      `/property-officer/billing/invoices/${invoiceId}/waiver`,
      { method: 'POST' },
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to request an invoice waiver.');
  }
}
