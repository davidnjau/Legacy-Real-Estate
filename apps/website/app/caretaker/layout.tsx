import { redirect } from 'next/navigation';
import { getAuthenticatedDjangoUser, hasDjangoRole } from '../../lib/django-auth';
import { CaretakerSidebar } from '../../components/caretaker/Sidebar';
import { PrototypeHeader } from '../../components/prototype/PrototypeHeader';

export default async function CaretakerLayout({ children }: { children: React.ReactNode }) {
  const user = await getAuthenticatedDjangoUser();
  if (!user || !hasDjangoRole(user, 'Caretaker')) {
    redirect('/login');
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-slate-50">
      <PrototypeHeader activeRole="caretaker" user={user} />
      <div className="flex flex-1 overflow-hidden">
        <CaretakerSidebar />
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-7xl px-4 pb-24 pt-4 sm:px-6 sm:pt-6 md:p-8">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
