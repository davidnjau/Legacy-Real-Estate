import { djangoProxyFailure, forwardDjangoRequest } from '../../../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    const challengeId = new URL(request.url).searchParams.get('challenge_id') || '';
    return await forwardDjangoRequest(
      request,
      `/auth/staff/activate/context?challenge_id=${encodeURIComponent(challengeId)}`,
    );
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to verify the staff activation link.');
  }
}
