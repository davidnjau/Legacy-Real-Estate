'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import {
  LayoutDashboard,
  Building2,
  Receipt,
  Wrench,
  ShieldCheck,
  MessageSquare,
  Landmark,
  Map,
  Scale,
  LogOut,
} from 'lucide-react';
import { ResponsivePortalSidebar } from '../ui/ResponsivePortalSidebar';

interface StaffSidebarProps {
  userRole?: string;
}

const navItems = [
  { name: 'Command Center', href: '/staff/dashboard', icon: LayoutDashboard, active: true },
  { name: 'Property Register', href: '/staff/dashboard#properties', icon: Building2, active: true },
  { name: 'Rent & Arrears (W2)', href: '/staff/dashboard#invoicing', icon: Receipt, active: true },
  { name: 'Maintenance Dispatch (W3)', href: '/staff/dashboard#maintenance', icon: Wrench, active: true },
  { name: 'Approvals Queue (W4)', href: '/staff/dashboard#approvals', icon: ShieldCheck, active: true },
  { name: 'SMS Delivery Hub (W5)', href: '/staff/dashboard#comms', icon: MessageSquare, active: true },
  { name: 'GL & KRA Tax (W6)', href: '/staff/dashboard#kra-tax', icon: Landmark, active: false, badge: 'W6' },
  { name: 'Land & Plots (W7)', href: '/staff/dashboard#land-sales', icon: Map, active: false, badge: 'W7' },
  { name: 'Legal Arrears (W8)', href: '/staff/dashboard#legal', icon: Scale, active: false, badge: 'W8' },
];

export function StaffSidebar({ userRole = 'Operations Staff' }: StaffSidebarProps) {
  const pathname = usePathname();
  const router = useRouter();

  const handleLogout = async () => {
    try {
      const csrf = await fetch('/api/auth/csrf', { cache: 'no-store' }).then((r) => r.json());
      await fetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'X-CSRFToken': csrf.csrfToken || '' },
      });
    } catch {
      // ignore
    } finally {
      router.push('/login');
      router.refresh();
    }
  };

  return (
    <ResponsivePortalSidebar label="Staff navigation" className="bg-[#102820] text-white">
      <div className="flex items-center justify-between border-b border-emerald-900/50 p-5">
        <div>
          <div className="flex items-center gap-2">
            <span className="grid h-8 w-8 place-items-center rounded-xl bg-emerald-400 text-[#102820] font-black text-sm">
              L
            </span>
            <span className="text-lg font-bold tracking-tight text-white">Legacy Agency</span>
          </div>
          <p className="mt-1 text-xs text-emerald-200/80 font-medium truncate max-w-[200px]">
            {userRole}
          </p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-4">
        <p className="px-3 pb-2 text-[10px] font-bold uppercase tracking-wider text-emerald-300/60">
          Agency Workspaces
        </p>
        {navItems.map((item) => {
          const isActive = pathname === item.href || (item.href === '/staff/dashboard' && pathname.startsWith('/staff'));
          const Icon = item.icon;

          return (
            <Link
              key={item.name}
              href={item.href}
              className={`flex items-center justify-between rounded-xl px-3 py-2.5 text-xs font-semibold transition ${
                isActive && !item.href.includes('#')
                  ? 'bg-emerald-600 text-white font-bold shadow-sm'
                  : 'text-slate-300 hover:bg-white/10 hover:text-white'
              }`}
            >
              <div className="flex items-center gap-2.5">
                <Icon size={16} className={isActive ? 'text-white' : 'text-emerald-300/80'} />
                <span>{item.name}</span>
              </div>
              {item.badge && (
                <span className="rounded bg-white/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-emerald-200">
                  {item.badge}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-emerald-900/50 p-4">
        <button
          type="button"
          onClick={handleLogout}
          className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-xs font-semibold text-red-300 transition hover:bg-red-500/15 hover:text-red-200"
        >
          <LogOut size={16} />
          <span>Sign Out</span>
        </button>
      </div>
    </ResponsivePortalSidebar>
  );
}
