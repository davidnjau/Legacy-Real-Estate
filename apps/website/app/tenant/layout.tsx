// app/tenant/layout.tsx
import { getDjangoUser, hasDjangoRole } from '../../lib/django-auth';
import { Sidebar } from '../../components/tenant/Sidebar';
import { redirect } from 'next/navigation';
import { Suspense } from 'react';
import TenantTourController from '../../components/tenant/TourController';

export default async function TenantLayout({ children }: { children: React.ReactNode }) {
  const user = await getDjangoUser();
  if (!user) redirect('/login');
  if (!hasDjangoRole(user, 'Tenant (portal)') && !hasDjangoRole(user, 'Tenant')) {
    redirect(hasDjangoRole(user, 'Caretaker') ? '/caretaker/dashboard' : '/landlord/dashboard');
  }

  return (
    <div className="flex h-screen bg-slate-50">
      {/* Sidebar Navigation */}
      <Sidebar />

      {/* Main Content Area */}
      <main className="flex-1 overflow-y-auto">
        <div className="p-8 max-w-5xl mx-auto">{children}</div>
      </main>
      
      <Suspense fallback={null}>
        <TenantTourController />
      </Suspense>
    </div>
  );
}