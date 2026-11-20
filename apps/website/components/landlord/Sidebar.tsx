// components/landlord/Sidebar.tsx
'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Building2, ClipboardCheck, FileText, LayoutDashboard } from 'lucide-react';
import SignOutButton from '../auth/SignOutButton';
import { ResponsivePortalSidebar } from '../ui/ResponsivePortalSidebar';

const landlordGroups = [
  {
    label: 'Portfolio',
    items: [
      { name: 'Overview', href: '/landlord/dashboard', icon: LayoutDashboard, tourId: 'nav-dashboard' },
      { name: 'My Properties', href: '/landlord/properties', icon: Building2, tourId: 'nav-properties' },
    ],
  },
  {
    label: 'Finance',
    items: [
      { name: 'Statements & Remittances', href: '/landlord/statements', icon: FileText, tourId: 'nav-statements' },
      { name: 'Expense Approvals', href: '/landlord/approvals', icon: ClipboardCheck, tourId: 'nav-approvals' },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <ResponsivePortalSidebar
      label="Landlord navigation"
      desktopWidthClass="md:w-[260px]"
      surface="light"
      className="border-r border-[#e6e9e5] bg-white px-4 py-6"
    >
      <div className="mb-6 shrink-0 px-3" data-tour-id="sidebar-header">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#91a095]">Workspace</p>
        <p className="mt-1 text-xs text-[#8a978e]">Property owner</p>
      </div>

      <nav className="flex-1 space-y-5 overflow-y-auto">
        {landlordGroups.map(({ label, items }) => (
          <div key={label}>
            <p className="px-3 pb-2 text-[9px] font-bold uppercase tracking-[0.18em] text-[#a0aaa2]">{label}</p>
            <div className="space-y-1">
              {items.map(({ name, href, icon: Icon, tourId }) => {
                const isActive = pathname === href;
                return (
                  <Link
                    key={name}
                    href={href}
                    data-tour-id={tourId}
                    aria-current={isActive ? 'page' : undefined}
                    className={`group flex items-center gap-3 rounded-xl border px-3 py-2.5 text-[12px] transition ${
                      isActive
                        ? 'border-[#e1ebd8] bg-[#f1f6ed] font-bold text-[#234630] shadow-[0_2px_8px_rgba(30,60,40,0.04)]'
                        : 'border-transparent font-medium text-[#68776d] hover:bg-[#f7f9f6] hover:text-[#234630]'
                    }`}
                  >
                    <Icon
                      size={16}
                      strokeWidth={isActive ? 2.2 : 1.8}
                      className={isActive ? 'text-[#668a4c]' : 'text-[#91a095] transition group-hover:text-[#668a4c]'}
                    />
                    <span className="flex-1">{name}</span>
                    {isActive && <span className="h-1.5 w-1.5 rounded-full bg-[#83a95d]" />}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="mt-auto shrink-0 border-t border-[#e7ece5] pt-4">
        <SignOutButton />
      </div>
    </ResponsivePortalSidebar>
  );
}