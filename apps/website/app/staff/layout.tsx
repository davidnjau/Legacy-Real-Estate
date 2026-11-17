import { getDjangoUser, hasDjangoRole, DEMO_USERS } from '../../lib/django-auth';
import { PrototypeHeader } from '../../components/prototype/PrototypeHeader';
import { StaffSidebar } from '../../components/staff/Sidebar';

const STAFF_ROLES = [
  'Super Admin (Director)',
  'Branch Manager',
  'Accountant',
  'Property Officer / Agent',
];

export default async function StaffLayout({ children }: { children: React.ReactNode }) {
  let user = await getDjangoUser();
  if (!user || !STAFF_ROLES.some((role) => hasDjangoRole(user, role))) {
    user = DEMO_USERS['staff'];
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-slate-50">
      <PrototypeHeader activeRole="staff" user={user} />
      <div className="flex flex-1 overflow-hidden">
        <StaffSidebar userRole={user.roles.join(' · ')} />
        <main className="flex-1 overflow-y-auto">
          {children}
        </main>
      </div>
    </div>
  );
}
