import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';
import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';
import { DEMO_USERS } from '../../../../lib/django-auth';

export async function GET(request: Request) {
  const cookieStore = await cookies();
  const demoRole = cookieStore.get('demo_role')?.value;

  try {
    const response = await forwardDjangoRequest(request, '/auth/me');
    if (response.ok) return response;
    if (demoRole && DEMO_USERS[demoRole]) {
      return NextResponse.json({ user: DEMO_USERS[demoRole] });
    }
    return response;
  } catch (error) {
    if (demoRole && DEMO_USERS[demoRole]) {
      return NextResponse.json({ user: DEMO_USERS[demoRole] });
    }
    return djangoProxyFailure(error, 'Unable to verify the current user.');
  }
}
