'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import dynamic from 'next/dynamic';
import type { TourStep } from '../ui/TourOverlay';

const TourOverlay = dynamic(() => import('../ui/TourOverlay'), { ssr: false });

const TOUR_STEPS: TourStep[] = [
  {
    targetId: 'tenant-nav-dashboard',
    title: 'Dashboard & Billing',
    body: 'View your monthly rent, water, and garbage bills here. You can also track your payments and outstanding balances.',
    placement: 'right',
  },
  {
    targetId: 'tenant-nav-maintenance',
    title: 'Maintenance Requests',
    body: 'Log any issues with your unit easily and track their resolution progress directly with the landlord.',
    placement: 'right',
  },
  {
    targetId: 'tenant-nav-profile',
    title: 'Your Profile',
    body: 'View and edit your personal details, and check your current unit and deposit information.',
    placement: 'right',
  }
];

export default function TenantTourController() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [active, setActive] = useState(false);
  const [steps, setSteps] = useState<TourStep[]>([]);

  useEffect(() => {
    if (searchParams.get('tour') === 'true') {
      setSteps(TOUR_STEPS);
      setActive(true);
    }
  }, [searchParams]);

  const finish = useCallback(async () => {
    setActive(false);
    await fetch('/api/tenant/onboarding', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tutorialCompleted: true }),
    });

    const url = new URL(window.location.href);
    url.searchParams.delete('tour');
    router.replace(url.pathname + url.search);
  }, [router]);

  if (!active || steps.length === 0) return null;

  return <TourOverlay steps={steps} onComplete={finish} onSkip={finish} />;
}
