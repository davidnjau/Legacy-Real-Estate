import { NextResponse } from 'next/server';
import { getDjangoHomePath } from '../../../../lib/django-routing';
import { DEMO_USERS } from '../../../../lib/django-auth';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const role = searchParams.get('role') || 'director';

  const demoUser = DEMO_USERS[role] || DEMO_USERS['director'];
  const destination = getDjangoHomePath(demoUser.roles);

  const response = NextResponse.redirect(new URL(destination, request.url));
  response.cookies.set('demo_role', role, {
    path: '/',
    maxAge: 60 * 60 * 8, // 8 hours
    sameSite: 'lax',
  });
  return response;
}
