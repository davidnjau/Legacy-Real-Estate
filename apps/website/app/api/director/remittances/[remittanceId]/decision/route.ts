import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../lib/django-proxy';

export async function POST(
  request: Request,
  context: { params: Promise<{ remittanceId: string }> },
) {
  try {
    const { remittanceId } = await context.params;
    const body = await request.json();
    return await forwardDjangoRequest(request, `/director/remittances/${encodeURIComponent(remittanceId)}/decision`, { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to review the landlord remittance.');
  }
}
