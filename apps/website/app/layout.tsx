// app/layout.tsx
import type { Metadata } from 'next';
import './globals.css';

import { createClient } from '@supabase/supabase-js';

export async function generateMetadata(): Promise<Metadata> {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!supabaseUrl || !serviceRoleKey) {
    if (process.env.NEXT_PUBLIC_API_MODE === 'local') {
      return {
        title: 'Rental Management | Property Management',
        description: 'Property management for landlords, caretakers, and tenants.',
      };
    }

    throw new Error(
      'NEXT_PUBLIC_SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required to generate metadata.'
    );
  }

  const supabaseAdmin = createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  });

  const { data: landlord } = await supabaseAdmin
    .from('users')
    .select('property_name')
    .eq('role', 'landlord')
    .limit(1)
    .maybeSingle();

  const propertyName = landlord?.property_name || 'Rental Management System';

  return {
    title: `${propertyName} | Property Management`,
    description: `Modern property management portal for ${propertyName}. Pay rent via M-Pesa, track maintenance, and manage units.`,
    openGraph: {
      title: `${propertyName} | Property Management`,
      description: `Modern property management portal for ${propertyName}.`,
      type: 'website',
    },
  };
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="font-sans bg-slate-50 text-slate-900 antialiased">
        {children}
      </body>
    </html>
  );
}