import { redirect } from 'next/navigation';
import { getAuthenticatedDjangoUser, hasDjangoRole } from '../../lib/django-auth';
import { DirectorPortalShell } from '../../components/admin/DirectorPortalShell';

export default async function DirectorLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const user = await getAuthenticatedDjangoUser();
  if (!user || !hasDjangoRole(user, 'Super Admin (Director)')) {
    redirect('/login');
  }

  return <DirectorPortalShell>{children}</DirectorPortalShell>;
}
