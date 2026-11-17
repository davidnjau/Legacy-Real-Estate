import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../lib/django-proxy';

type RouteContext = {
  params: Promise<{ invitationId: string }>;
};

export async function POST(request: Request, context: RouteContext) {
  try {
    const { invitationId } = await context.params;
    return await forwardDjangoRequest(request, `/caretaker-invitations/${invitationId}/resend`, {
      method: 'POST',
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to resend the caretaker invitation.');
  }
}
