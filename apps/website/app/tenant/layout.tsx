import { getDjangoUser, hasDjangoRole, DEMO_USERS } from '../../lib/django-auth';
import { Sidebar } from '../../components/tenant/Sidebar';
import { PrototypeHeader } from '../../components/prototype/PrototypeHeader';

export default async function TenantLayout({ children }: { children: React.ReactNode }) {
  let user = await getDjangoUser();
  if (!user || (!hasDjangoRole(user, 'Tenant (portal)') && !hasDjangoRole(user, 'Tenant'))) {
    user = DEMO_USERS['tenant'];
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-slate-50">
      <PrototypeHeader activeRole="tenant" user={user} />
      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar Navigation */}
        <Sidebar />

        {/* Main Content Area */}
        <main className="flex-1 overflow-y-auto">
          <div className="p-8 max-w-5xl mx-auto">{children}</div>
        </main>
      </div>
    </div>
  );
}
