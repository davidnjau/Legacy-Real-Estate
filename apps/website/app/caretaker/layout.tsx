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
    <div className="flex h-dvh flex-col overflow-hidden bg-[#f5f6f4] text-[#172820]">
      <PrototypeHeader activeRole="caretaker" user={user} />
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden md:flex-row">
        <CaretakerSidebar />
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-7xl px-4 py-4 sm:px-6 sm:py-6 lg:p-8">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
