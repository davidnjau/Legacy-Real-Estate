import { Sidebar } from '../../components/landlord/Sidebar';
import { createClient } from '../../lib/supabase/server';
import { Suspense } from 'react';
import TourController from '../../components/landlord/TourController';

export default async function LandlordLayout({ children }: { children: React.ReactNode }) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  
  let role = 'landlord';
  if (user) {
    const { data } = await supabase.from('users').select('role').eq('id', user.id).maybeSingle();
    if (data?.role) {
      role = data.role;
    }
  }

  return (
    <div className="flex h-screen bg-slate-50">
      {/* Sidebar Navigation */}
      <Sidebar userRole={role} />

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