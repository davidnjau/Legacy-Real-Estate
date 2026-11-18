import { getDjangoUser, hasDjangoRole, DEMO_USERS } from '../../lib/django-auth';
import { Sidebar } from '../../components/tenant/Sidebar';
import { PrototypeHeader } from '../../components/prototype/PrototypeHeader';

export default async function TenantLayout({ children }: { children: React.ReactNode }) {
  let user = await getDjangoUser();
  if (!user || (!hasDjangoRole(user, 'Tenant (portal)') && !hasDjangoRole(user, 'Tenant'))) {
    user = DEMO_USERS['tenant'];
  }

  return (
    <div className="flex min-h-dvh flex-col overflow-hidden bg-slate-50">
      <PrototypeHeader activeRole="tenant" user={user} />
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden md:flex-row">
        {/* Sidebar Navigation */}
        <Sidebar />

        {/* Main Content Area */}
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-5xl p-4 sm:p-6 lg:p-8">{children}</div>
        </main>
      </div>
    </div>
  );
}
