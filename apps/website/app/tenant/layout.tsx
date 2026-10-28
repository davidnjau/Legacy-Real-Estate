// app/tenant/layout.tsx
import { createClient } from '../../lib/supabase/server';
import { createClient as createAdmin } from '@supabase/supabase-js';
import { Sidebar } from '../../components/tenant/Sidebar';
import { OffboardingScreen } from '../../components/tenant/OffboardingScreen';
import { Suspense } from 'react';
import TenantTourController from '../../components/tenant/TourController';

const supabaseAdmin = createAdmin(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
);

export default async function TenantLayout({ children }: { children: React.ReactNode }) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();

  let unitStatus = 'unknown';
  let unitCode: string | null = null;

  if (user) {
    const { data: assignment } = await supabaseAdmin
      .from('unit_assignments')
      .select('unit_id, status')
      .eq('tenant_id', user.id)
      .order('created_at', { ascending: false })
      .limit(1)
      .maybeSingle();

    if (assignment?.unit_id) {
      const { data: unit } = await supabaseAdmin
        .from('units')
        .select('unit_code, status')
        .eq('id', assignment.unit_id)
        .single();

      unitStatus = unit?.status || 'unknown';
      unitCode = unit?.unit_code || null;
    }
  }

  const isOffboarding = unitStatus === 'offboarding' || unitStatus === 'deposit_pending';

  return (
    <div className="flex h-screen bg-slate-50">
      {/* Sidebar Navigation */}
      {!isOffboarding && <Sidebar />}

      {/* Main Content Area */}
      <main className="flex-1 overflow-y-auto">
        {isOffboarding ? (
          <OffboardingScreen unitCode={unitCode} unitStatus={unitStatus} />
        ) : (
          <div className="p-8 max-w-5xl mx-auto">
            {children}
          </div>
        )}
      </main>
      
      <Suspense fallback={null}>
        <TenantTourController />
      </Suspense>
    </div>
  );
}