import { redirect } from 'next/navigation';
import { getAuthenticatedDjangoUser } from '../../lib/django-auth';
import { isPropertyOfficerRole } from '../../lib/django-routing';
import { PropertyOfficerPortalShell } from '../../components/property-officer/PortalShell';

export default async function PropertyOfficerLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const user = await getAuthenticatedDjangoUser();
  const hasAgencyRole = user?.agencies?.some((agency) =>
    agency.roles.some(isPropertyOfficerRole),
  );
  const hasPortalRole = user?.roles.some(isPropertyOfficerRole);

  if (!user || (!hasAgencyRole && !hasPortalRole)) {
    redirect('/login');
  }

  return <PropertyOfficerPortalShell>{children}</PropertyOfficerPortalShell>;
}
