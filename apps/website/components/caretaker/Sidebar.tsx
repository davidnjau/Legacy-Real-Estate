// components/caretaker/Sidebar.tsx
'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { ClipboardCheck, Droplets, LayoutDashboard, Wrench } from 'lucide-react';
import SignOutButton from '../auth/SignOutButton';
import { ResponsivePortalSidebar } from '../ui/ResponsivePortalSidebar';

const navGroups = [
  {
    label: 'Overview',
    items: [{ name: 'Site Hub', href: '/caretaker/dashboard', icon: LayoutDashboard }],
  },
  {
    label: 'Field operations',
    items: [
      { name: 'Checklists', href: '/caretaker/checklists', icon: ClipboardCheck },
      { name: 'On-Site Tickets', href: '/caretaker/tickets', icon: Wrench },
    ],
  },
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
      desktopWidthClass="md:w-[260px]"
      surface="light"
      className="border-r border-[#e6e9e5] bg-white px-4 py-6"
    >
      <div className="mb-6 shrink-0 px-3">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#91a095]">Workspace</p>
        <p className="mt-1 text-xs text-[#8a978e]">Caretaker operations</p>
      </div>

      <nav
        aria-label="Caretaker navigation"
        className="flex flex-1 flex-col gap-5 overflow-y-auto"
      >
        {navGroups.map(({ label, items }) => (
          <div key={label}>
            <p className="px-3 pb-2 text-[9px] font-bold uppercase tracking-[0.18em] text-[#a0aaa2]">{label}</p>
            <div className="space-y-1">
              {items.map(({ name, href, icon: Icon }) => {
                const isActive = pathname === href;
                return (
                  <Link
                    key={name}
                    href={href}
                    aria-current={isActive ? 'page' : undefined}
                    data-tour-id={`caretaker-nav-${name.toLowerCase().split(' ')[0]}`}
                    className={`group flex min-w-0 items-center gap-3 rounded-xl border px-3 py-2.5 text-[12px] transition ${
                      isActive
                        ? 'border-[#e1ebd8] bg-[#f1f6ed] font-bold text-[#234630] shadow-[0_2px_8px_rgba(30,60,40,0.04)]'
                        : 'border-transparent font-medium text-[#68776d] hover:bg-[#f7f9f6] hover:text-[#234630]'
                    }`}
                  >
                    <Icon
                      size={16}
                      strokeWidth={isActive ? 2.2 : 1.8}
                      className={isActive ? 'text-[#668a4c]' : 'text-[#91a095] transition group-hover:text-[#668a4c]'}
                      aria-hidden="true"
                    />
                    <span className="flex-1">{name}</span>
                    {isActive && <span className="h-1.5 w-1.5 rounded-full bg-[#83a95d]" />}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
        {hasPaidWater && (
          <div>
            <p className="px-3 pb-2 text-[9px] font-bold uppercase tracking-[0.18em] text-[#a0aaa2]">Utilities</p>
            <Link
              href="/caretaker/meters"
              aria-current={pathname === '/caretaker/meters' ? 'page' : undefined}
              data-tour-id="caretaker-nav-water"
              className={`group flex items-center gap-3 rounded-xl border px-3 py-2.5 text-[12px] transition ${
                pathname === '/caretaker/meters'
                  ? 'border-[#e1ebd8] bg-[#f1f6ed] font-bold text-[#234630] shadow-[0_2px_8px_rgba(30,60,40,0.04)]'
                  : 'border-transparent font-medium text-[#68776d] hover:bg-[#f7f9f6] hover:text-[#234630]'
              }`}
            >
              <Droplets
                size={16}
                strokeWidth={pathname === '/caretaker/meters' ? 2.2 : 1.8}
                className={pathname === '/caretaker/meters' ? 'text-[#668a4c]' : 'text-[#91a095] transition group-hover:text-[#668a4c]'}
                aria-hidden="true"
              />
              <span className="flex-1">Water Meter</span>
              {pathname === '/caretaker/meters' && <span className="h-1.5 w-1.5 rounded-full bg-[#83a95d]" />}
            </Link>
          </div>
        )}
      </nav>

      <div className="mt-auto shrink-0 border-t border-[#e7ece5] pt-4">
        <SignOutButton />
      </div>
    </ResponsivePortalSidebar>
  );
}
