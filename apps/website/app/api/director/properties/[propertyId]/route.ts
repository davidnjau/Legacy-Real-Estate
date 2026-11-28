import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

export async function GET(
  request: Request,
  context: { params: Promise<{ propertyId: string }> },
) {
  try {
    const { propertyId } = await context.params;
    const query = new URL(request.url).searchParams.toString();
    return await forwardDjangoRequest(
      request,
      `/director/properties/${encodeURIComponent(propertyId)}${query ? `?${query}` : ''}`,
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load the director property overview.');
  }
}
