'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter, useSearchParams, usePathname } from 'next/navigation';
import dynamic from 'next/dynamic';
import type { TourStep } from '../ui/TourOverlay';

// Lazy-load the heavy overlay so it doesn't affect normal page load
const TourOverlay = dynamic(() => import('../ui/TourOverlay'), { ssr: false });

const TOUR_STEPS: TourStep[] = [
  {
    targetId: 'sidebar-header',
    title: 'Welcome to your Landlord Portal',
    body: 'This is your central hub for managing your entire property. Everything you need is in the sidebar on the left. Let\'s walk through each section.',
    placement: 'right',
  },
  {
    targetId: 'nav-dashboard',
    title: '📊 Dashboard',
    body: 'Your at-a-glance overview. See the current month\'s breakdown of total expected revenue, collection rates, occupancy status, and any pending actions that need your attention.',
    placement: 'right',
  },
  {
    targetId: 'nav-migration',
    title: '📁 Migration Setup — Start Here!',
    body: 'This is where you begin. During the first month, import all your existing units and tenants here. Apart from adding the existing tenant\'s info, you also add their arrears after the tenant\'s grace period for clearing arrears passes, which are then carried over to the new month.',
    placement: 'right',
  },
  {
    targetId: 'nav-units',
    title: '🏠 Units & Tenants',
    body: 'View and manage all your units. Apart from logging move-in settlements and generating claim codes for tenants who move in, this section is also for settling deposits after a tenant moves out, handling move-out notices, and managing immediate evacuations.',
    placement: 'right',
  },
  {
    targetId: 'nav-billing',
    title: '📄 Billing Wizard',
    body: 'At the end of each month, come here to generate draft invoices for all occupied units. Once your caretaker has published the water readings, you can publish the invoices. Note that tenants don\'t see their invoices when published; they see them immediately when the new month arrives.',
    placement: 'right',
  },
  {
    targetId: 'nav-ledger',
    title: '💰 Financial Ledger',
    body: 'The complete financial picture for your property. Track every payment, outstanding balance, and arrear. Filter by date, unit, or tenant.',
    placement: 'right',
  },
  {
    targetId: 'nav-maintenance',
    title: '🔧 Maintenance',
    body: 'Tenants can log maintenance requests directly from their dashboard. Track, update, and resolve tickets here — all communication is centralised.',
    placement: 'right',
  },
  {
    targetId: 'nav-staff',
    title: '👥 Staff & Caretakers',
    body: 'Invite your caretakers to the portal. They get their own dedicated interface to record water meter readings and monitor room statuses — separate from your landlord view.',
    placement: 'right',
  },
];

export default function TourController() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const [active, setActive] = useState(false);

  useEffect(() => {
    if (searchParams.get('tour') === 'true') {
      // Small delay so the page fully renders first
      const t = setTimeout(() => setActive(true), 600);
      return () => clearTimeout(t);
    }
  }, [searchParams]);

  const finish = useCallback(async () => {
    setActive(false);
    await fetch('/api/landlord/onboarding', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tutorialCompleted: true }),
    });
    // Remove ?tour=true from URL cleanly
    router.replace('/landlord/dashboard');
  }, [router]);

  const skip = useCallback(async () => {
    setActive(false);
    await fetch('/api/landlord/onboarding', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tutorialCompleted: false }),
    });
    router.replace('/landlord/dashboard');
  }, [router]);

  if (!active) return null;

  return <TourOverlay steps={TOUR_STEPS} onComplete={finish} onSkip={skip} />;
}
