import { Sidebar } from '../../components/landlord/Sidebar';
import { getDjangoUser, hasDjangoRole } from '../../lib/django-auth';
import { redirect } from 'next/navigation';
import { Suspense } from 'react';
import TourController from '../../components/landlord/TourController';

export default async function LandlordLayout({ children }: { children: React.ReactNode }) {
  const user = await getDjangoUser();
  if (!user) redirect('/login');
  if (!hasDjangoRole(user, 'Landlord (portal)') && !hasDjangoRole(user, 'Landlord')) {
    redirect(hasDjangoRole(user, 'Caretaker') ? '/caretaker/dashboard' : '/tenant/dashboard');
  }

  return (
    <div className="flex h-screen bg-slate-50">
      {/* Sidebar Navigation */}
      <Sidebar userRole={user.roles.join(', ')} />

      {/* Main Content Area */}
      <main className="flex-1 overflow-y-auto">
        <div className="p-8 max-w-7xl mx-auto">
          {children}
        </div>
      </main>

      {/* Spotlight Tour — reads ?tour=true from URL, must be inside Suspense for useSearchParams */}
      <Suspense fallback={null}>
        <TourController />
      </Suspense>
    </div>
  );
}