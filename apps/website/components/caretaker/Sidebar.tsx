// components/caretaker/Sidebar.tsx
'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { ClipboardCheck, Droplets, LayoutDashboard, Wrench } from 'lucide-react';
import { ResponsivePortalSidebar } from '../ui/ResponsivePortalSidebar';

const navItems = [
  { name: 'Site Hub', href: '/caretaker/dashboard', icon: LayoutDashboard },
  { name: 'Checklists', href: '/caretaker/checklists', icon: ClipboardCheck },
  { name: 'On-Site Tickets', href: '/caretaker/tickets', icon: Wrench },
];

export function CaretakerSidebar() {
  const pathname = usePathname();
  const [hasPaidWater, setHasPaidWater] = useState(false);

  useEffect(() => {
    let active = true;
    void fetch('/api/caretaker/dashboard')
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || data.message || 'Unable to load caretaker properties.');
        }
        return data;
      })
      .then((data) => {
        if (active) {
          setHasPaidWater(
            Array.isArray(data.properties) &&
              data.properties.some((property: { has_paid_water?: boolean }) => property.has_paid_water)
          );
        }
      })
      .catch((error: unknown) => {
        console.error('Unable to determine caretaker water-reading access:', error);
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <ResponsivePortalSidebar
      label="Caretaker navigation"
      className="border-emerald-950/60 bg-[#102820] text-white md:h-full md:border-r"
    >
      <div className="border-b border-emerald-900/40 p-5">
        <div className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-xl bg-emerald-400 text-sm font-black text-[#102820]">
            L
          </span>
          <h1 className="text-lg font-bold tracking-tight text-white">Legacy Caretaker</h1>
        </div>
        <p className="mt-1 text-xs font-medium text-emerald-300">Field Operations Workspace</p>
      </div>

      <nav
        aria-label="Caretaker navigation"
        className="flex flex-col gap-1.5 px-3 py-4"
      >
        {[...navItems.slice(0, 1), ...(hasPaidWater ? [{ name: 'Water Meter', href: '/caretaker/meters', icon: Droplets }] : []), ...navItems.slice(1)].map((item) => {
          const isActive = pathname === item.href;
          const Icon = item.icon;
          return (
            <Link
              key={item.name}
              href={item.href}
              aria-current={isActive ? 'page' : undefined}
              data-tour-id={`caretaker-nav-${item.name.toLowerCase().split(' ')[0]}`}
              className={`flex min-w-0 items-center gap-2.5 rounded-xl px-3 py-2.5 text-xs font-semibold transition-colors ${
                isActive ? 'bg-emerald-600 text-white shadow-xs' : 'text-slate-300 hover:bg-white/10'
              }`}
            >
              <Icon size={18} className={isActive ? 'text-white' : 'text-emerald-300/80'} aria-hidden="true" />
              <span>{item.name}</span>
            </Link>
          );
        })}
      </nav>
    </ResponsivePortalSidebar>
  );
}
