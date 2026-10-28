// components/caretaker/Sidebar.tsx
'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { LayoutDashboard, Droplets, LogOut, UserCircle, FolderUp, Megaphone, Wallet, Wrench, FileText } from 'lucide-react';
import { createClient } from '../../lib/supabase/client';

const navItems = [
  { name: 'Room Status', href: '/caretaker/dashboard', icon: LayoutDashboard },
  { name: 'Tenant Migration', href: '/caretaker/migration', icon: FolderUp },
  { name: 'Billing', href: '/caretaker/billing', icon: FileText },
  { name: 'Rent Ledger', href: '/caretaker/ledger', icon: Wallet },
  { name: 'Maintenance', href: '/caretaker/maintenance', icon: Wrench },
  { name: 'Announcements', href: '/caretaker/announcements', icon: Megaphone },
  { name: 'Water Readings', href: '/caretaker/water-readings', icon: Droplets },
  { name: 'Profile', href: '/caretaker/profile', icon: UserCircle },
];

export function CaretakerSidebar() {
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
      <div className="p-6">
        <h1 className="text-xl font-bold tracking-tight">Sunshine Apts</h1>
        <p className="text-sm text-emerald-400 mt-1 font-medium">Caretaker Portal</p>
      </div>

      <nav className="flex-1 px-4 space-y-2 mt-4">
        {navItems.map((item) => {
          const isActive = pathname === item.href;
          const Icon = item.icon;
          return (
            <Link
              key={item.name}
              href={item.href}
              data-tour-id={`caretaker-nav-${item.name.toLowerCase().split(' ')[0]}`}
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
