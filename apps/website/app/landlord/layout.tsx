import { redirect } from 'next/navigation';
import { Sidebar } from '../../components/landlord/Sidebar';
import { getAuthenticatedDjangoUser, hasDjangoRole } from '../../lib/django-auth';
import { PrototypeHeader } from '../../components/prototype/PrototypeHeader';

export default async function LandlordLayout({ children }: { children: React.ReactNode }) {
  const user = await getAuthenticatedDjangoUser();
  const canManageLandlordWorkflows = user && [
    'Super Admin (Director)',
    'Branch Manager',
    'Accountant',
    'Property Officer / Agent',
  ].some((role) => hasDjangoRole(user, role));

  if (
    !user ||
    (!hasDjangoRole(user, 'Landlord (portal)') &&
      !hasDjangoRole(user, 'Landlord') &&
      !canManageLandlordWorkflows)
  ) {
    redirect('/login');
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-slate-50 print:block print:h-auto print:overflow-visible">
      <div className="print:hidden">
        <PrototypeHeader activeRole="landlord" user={user} />
      </div>
      <div className="flex flex-1 overflow-hidden print:block print:overflow-visible">
        {/* Sidebar Navigation */}
        <div className="print:hidden">
          <Sidebar />
        </div>

        {/* Main Content Area */}
        <main className="flex-1 overflow-y-auto print:overflow-visible">
          <div className="mx-auto max-w-7xl p-8 print:max-w-none print:p-0">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
