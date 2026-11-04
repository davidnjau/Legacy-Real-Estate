import { CaretakerSidebar } from '../../components/caretaker/Sidebar';
import { getDjangoUser, hasDjangoRole } from '../../lib/django-auth';
import { redirect } from 'next/navigation';
import { Suspense } from 'react';
import CaretakerTourController from '../../components/caretaker/TourController';

export default async function CaretakerLayout({ children }: { children: React.ReactNode }) {
  const user = await getDjangoUser();
  if (!user) {
    redirect('/login');
  }

  if (!hasDjangoRole(user, 'Caretaker')) {
    // Redirect non-caretakers to their appropriate interface
    redirect(hasDjangoRole(user, 'Landlord (portal)') ? '/landlord/dashboard' : '/tenant/dashboard');
  }

  return (
    <div className="flex h-screen bg-slate-50">
      <CaretakerSidebar />
      <main className="flex-1 overflow-y-auto">
        <div className="p-8 max-w-7xl mx-auto">
          {children}
        </div>
      </main>

      <Suspense fallback={null}>
        <CaretakerTourController />
      </Suspense>
    </div>
  );
}
