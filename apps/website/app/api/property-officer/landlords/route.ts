import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    const agencyId = new URL(request.url).searchParams.get('agency_id');
    const path = agencyId
      ? `/property-officer/landlords?agency_id=${encodeURIComponent(agencyId)}`
      : '/property-officer/landlords';
    return await forwardDjangoRequest(request, path);
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load landlord onboarding records.');
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    return await forwardDjangoRequest(request, '/property-officer/landlords', { body });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to create the landlord portal account.');
  }
}
