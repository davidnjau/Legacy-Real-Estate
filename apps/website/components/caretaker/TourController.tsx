'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import dynamic from 'next/dynamic';
import type { TourStep } from '../ui/TourOverlay';

// Lazy-load the heavy overlay so it doesn't affect normal page load
const TourOverlay = dynamic(() => import('../ui/TourOverlay'), { ssr: false });

const TOUR_STEPS: TourStep[] = [
  {
    targetId: 'caretaker-nav-dashboard',
    title: 'Dashboard',
    body: 'Your dashboard shows pending actions and an overview of your tasks for the current month.',
    placement: 'right',
  },
  {
    targetId: 'caretaker-nav-water',
    title: 'Water Readings',
    body: 'Click here at the end of each month to enter the latest meter readings for all units. This updates the landlord\'s billing instantly.',
    placement: 'right',
  }
];

export default function CaretakerTourController() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [active, setActive] = useState(false);
  const [steps, setSteps] = useState<TourStep[]>([]);

  // Only run on mount or when ?tour changes
  useEffect(() => {
    if (searchParams.get('tour') === 'true') {
      setSteps(TOUR_STEPS);
      setActive(true);
    }
  }, [searchParams]);

  const finish = useCallback(async () => {
    setActive(false);
    // Mark as completed in DB
    await fetch('/api/caretaker/onboarding', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tutorialCompleted: true }),
    });

    // Remove ?tour=true from URL
    const url = new URL(window.location.href);
    url.searchParams.delete('tour');
    router.replace(url.pathname + url.search);
  }, [router]);

  if (!active || steps.length === 0) return null;

  return <TourOverlay steps={steps} onComplete={finish} onSkip={finish} />;
}
