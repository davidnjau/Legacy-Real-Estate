import { CaretakerSidebar } from '../../components/caretaker/Sidebar';
import { createClient } from '../../lib/supabase/server';
import { redirect } from 'next/navigation';
import { Suspense } from 'react';
import CaretakerTourController from '../../components/caretaker/TourController';

export default async function CaretakerLayout({ children }: { children: React.ReactNode }) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();

  if (!user) {
    redirect('/login');
  }

  const { data: profile } = await supabase.from('users').select('role').eq('id', user.id).maybeSingle();
  
  if (profile?.role !== 'caretaker') {
    // Redirect non-caretakers to their appropriate interface
    redirect(profile?.role === 'landlord' ? '/landlord/dashboard' : '/tenant/dashboard');
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
