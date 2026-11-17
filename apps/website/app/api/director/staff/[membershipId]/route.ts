import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../lib/django-proxy';

type RouteContext = {
  params: Promise<{ membershipId: string }>;
};

export async function PATCH(request: Request, context: RouteContext) {
  try {
    const { membershipId } = await context.params;
    const body = await request.json();
    return await forwardDjangoRequest(request, `/director/staff/${membershipId}`, {
      method: 'PATCH',
      body,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to update the staff account.');
  }
}

export async function DELETE(request: Request, context: RouteContext) {
  try {
    const { membershipId } = await context.params;
    return await forwardDjangoRequest(request, `/director/staff/${membershipId}`, {
      method: 'DELETE',
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to suspend the staff account.');
  }
}
