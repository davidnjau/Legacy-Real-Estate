// components/landlord/Sidebar.tsx
'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { LayoutDashboard, Home, FileText, Wallet, Wrench, LogOut, Users, FolderUp } from 'lucide-react';
import { createClient } from '../../lib/supabase/client';

const navItems = [
  { name: 'Dashboard',        href: '/landlord/dashboard',  icon: LayoutDashboard, tourId: 'nav-dashboard' },
  { name: 'Units & Tenants',  href: '/landlord/units',      icon: Home,            tourId: 'nav-units' },
  { name: 'Billing Wizard',   href: '/landlord/billing',    icon: FileText,        tourId: 'nav-billing' },
  { name: 'Financial Ledger', href: '/landlord/ledger',     icon: Wallet,          tourId: 'nav-ledger' },
  { name: 'Maintenance',      href: '/landlord/maintenance', icon: Wrench,         tourId: 'nav-maintenance' },
  { name: 'Staff & Caretakers', href: '/landlord/staff',    icon: Users,           tourId: 'nav-staff' },
  { name: 'Migration Setup',  href: '/landlord/migration',  icon: FolderUp,        tourId: 'nav-migration' },
];

export function Sidebar({ userRole = 'landlord' }: { userRole?: string }) {
  const pathname = usePathname();
  const router = useRouter();

  const handleLogout = async () => {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push('/login');
    router.refresh();
  };

  return (
    <aside className="w-64 bg-slate-900 text-white flex flex-col h-full">
      <div className="p-6" data-tour-id="sidebar-header">
        <h1 className="text-xl font-bold tracking-tight">Sunshine Apts</h1>
        <p className="text-sm text-slate-400 mt-1">Landlord Portal</p>
      </div>

      <nav className="flex-1 px-4 space-y-2 mt-4">
        {navItems.map((item) => {
          const isActive = pathname === item.href;
          const Icon = item.icon;
          
          return (
            <Link 
              key={item.name} 
              href={item.href}
              data-tour-id={item.tourId}
              className={`flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${
                isActive ? 'bg-emerald-600 text-white font-bold' : 'hover:bg-slate-800 text-slate-300 font-medium'
              }`}
            >
              <Icon size={20} className={isActive ? 'text-white' : 'text-slate-400'} />
              <span>{item.name}</span>
            </Link>
          );
        })}
      </nav>

      <div className="p-4 border-t border-slate-800">
        <button 
          onClick={handleLogout}
          className="flex items-center gap-3 px-3 py-2 w-full text-left rounded-lg hover:bg-slate-800 transition-colors text-red-400"
        >
          <LogOut size={20} />
          <span className="font-medium">Log Out</span>
        </button>
      </div>
    </aside>
  );
}