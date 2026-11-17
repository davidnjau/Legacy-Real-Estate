import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    const agencyId = new URL(request.url).searchParams.get('agency_id');
    const path = agencyId
      ? `/director/staff?agency_id=${encodeURIComponent(agencyId)}`
      : '/director/staff';
    return await forwardDjangoRequest(request, path);
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load agency staff.');
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/director/staff', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to create the staff account.');
  }
}
