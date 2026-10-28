'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { Home, Wrench, LogOut, UserCircle } from 'lucide-react';
import { createClient } from '../../lib/supabase/client';

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const isOffboarding = false; // In a real app, this status would be fetched from the database
  const [identity, setIdentity] = useState({ unitCode: '', name: '' });
  const [propertyName, setPropertyName] = useState('...');

  useEffect(() => {
    fetch('/api/tenant/profile').then((res) => res.json()).then((data) => {
      if (data.profile) setIdentity({ unitCode: data.profile.unitCode || '-', name: data.profile.fullName || 'Tenant' });
    }).catch(() => undefined);
    
    fetch('/api/system/config').then((res) => res.json()).then((data) => {
      if (data.propertyName) setPropertyName(data.propertyName);
    }).catch(() => undefined);
  }, []);

  const handleLogout = async () => {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push('/login');
    router.refresh();
  };

  return (
    <aside className="w-64 bg-slate-900 text-white flex flex-col">
      <div className="p-6">
        <h1 className="text-xl font-bold tracking-tight">{propertyName === '...' ? 'Welcome' : propertyName}</h1>
        <p className="text-sm text-emerald-400 mt-1 font-medium">Unit {identity.unitCode} • {identity.name}</p>
      </div>

      <nav className="flex-1 px-4 space-y-2 mt-4">
        <Link href="/tenant/dashboard" data-tour-id="tenant-nav-dashboard" className={`flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${pathname === '/tenant/dashboard' ? 'bg-emerald-600 text-white font-bold' : 'hover:bg-slate-800'}`}>
          <Home size={20} className={pathname === '/tenant/dashboard' ? 'text-white' : 'text-slate-400'} />
          <span className="font-medium">Dashboard & Billing</span>
        </Link>
        <Link href="/tenant/maintenance" data-tour-id="tenant-nav-maintenance" className={`flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${isOffboarding ? 'opacity-50 cursor-not-allowed pointer-events-none' : (pathname === '/tenant/maintenance' ? 'bg-emerald-600 text-white font-bold' : 'hover:bg-slate-800')}`}>
          <Wrench size={20} className={pathname === '/tenant/maintenance' ? 'text-white' : 'text-slate-400'} />
          <span className="font-medium">Maintenance</span>
        </Link>
        <Link href="/tenant/profile" data-tour-id="tenant-nav-profile" className={`flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${pathname === '/tenant/profile' ? 'bg-emerald-600 text-white font-bold' : 'hover:bg-slate-800'}`}><UserCircle size={20} /><span className="font-medium">Profile</span></Link>
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
