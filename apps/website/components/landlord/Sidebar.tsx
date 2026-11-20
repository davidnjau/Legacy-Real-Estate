// components/landlord/Sidebar.tsx
'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Building2, ClipboardCheck, FileText, LayoutDashboard } from 'lucide-react';
import SignOutButton from '../auth/SignOutButton';
import { ResponsivePortalSidebar } from '../ui/ResponsivePortalSidebar';

const landlordItems = [
  { name: 'Overview', href: '/landlord/dashboard', icon: LayoutDashboard, tourId: 'nav-dashboard' },
  { name: 'My Properties', href: '/landlord/properties', icon: Building2, tourId: 'nav-properties' },
  { name: 'Statements & Remittances', href: '/landlord/statements', icon: FileText, tourId: 'nav-statements' },
  { name: 'Expense Approvals', href: '/landlord/approvals', icon: ClipboardCheck, tourId: 'nav-approvals' },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <ResponsivePortalSidebar
      label="Landlord navigation"
      className="bg-[#102820] text-white md:h-full md:border-r md:border-emerald-950/60"
    >
      <div className="p-5 border-b border-emerald-900/40 shrink-0" data-tour-id="sidebar-header">
        <div className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-xl bg-emerald-400 text-[#102820] font-black text-sm">
            L
          </span>
          <h1 className="text-lg font-bold tracking-tight text-white">Legacy Portal</h1>
        </div>
        <p className="text-xs text-emerald-200/80 mt-1 font-medium">Property Owner Workspace</p>
      </div>

      <nav className="flex-1 px-3 space-y-1.5 mt-4 overflow-y-auto">
        <p className="px-3 pb-2 text-[10px] font-bold uppercase tracking-[0.16em] text-emerald-200/60">My portfolio</p>
        {landlordItems.map(({ name, href, icon: Icon, tourId }) => {
          const isActive = pathname === href;
          return (
            <Link key={name} href={href} data-tour-id={tourId}
              aria-current={isActive ? 'page' : undefined}
              className={`flex items-center gap-3 rounded-xl px-3 py-2 text-xs font-semibold transition-colors ${
                isActive ? 'bg-emerald-600 text-white font-bold shadow-xs' : 'text-slate-300 hover:bg-white/10'
              }`}>
              <Icon size={16} className={isActive ? 'text-white' : 'text-emerald-300/80'} />
              <span>{name}</span>
            </Link>
          );
        })}
      </nav>

      <div className="p-4 border-t border-emerald-900/40 shrink-0">
        <SignOutButton />
      </div>
    </ResponsivePortalSidebar>
  );
}