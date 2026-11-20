'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { Home, Wrench, LogOut, UserCircle, DoorOpen } from 'lucide-react';
import { ResponsivePortalSidebar } from '../ui/ResponsivePortalSidebar';

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const isOffboarding = false; // In a real app, this status would be fetched from the database
  const [identity, setIdentity] = useState({ unitCode: '', name: '' });
  const [propertyName, setPropertyName] = useState('...');

  useEffect(() => {
    const loadTenant = async () => {
      try {
        const response = await fetch('/api/tenant/invoices', { cache: 'no-store' });
        const data = await response.json();
        if (response.ok && data.tenant?.name) {
          setIdentity({
            unitCode: data.tenant?.unit_code || 'NAI-SV-A3',
            name: data.tenant?.name || 'Resident',
          });
          setPropertyName(data.tenant?.property_name || 'Sunset Villas');
          return;
        }
      } catch {
        // continue to auth/me fallback
      }

      try {
        const authRes = await fetch('/api/auth/me', { cache: 'no-store' });
        if (authRes.ok) {
          const authData = await authRes.json();
          if (authData.user) {
            setIdentity({
              unitCode: 'NAI-SV-A3',
              name: authData.user.name || 'Resident',
            });
            setPropertyName('Sunset Villas (Resident Hub)');
            return;
          }
        }
      } catch {
        // fallback to default
      }

      setIdentity({ unitCode: 'NAI-SV-A3', name: 'Brian Omondi' });
      setPropertyName('Sunset Villas (Resident Hub)');
    };
    void loadTenant();
  }, []);

  const handleLogout = async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch {
      // ignore
    } finally {
      window.location.href = '/api/auth/logout';
    }
  };

  return (
    <ResponsivePortalSidebar
      label="Tenant navigation"
      className="bg-[#102820] text-white md:h-full md:border-r md:border-emerald-950/60"
    >
      <div className="p-5 border-b border-emerald-900/40 shrink-0">
        <div className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-xl bg-emerald-400 text-[#102820] font-black text-sm">
            L
          </span>
          <h1 className="text-lg font-bold tracking-tight">{propertyName === '...' ? 'Resident Hub' : propertyName}</h1>
        </div>
        <p className="text-xs text-emerald-300 mt-1 font-semibold">Unit {identity.unitCode || 'NAI-SV-A3'} • {identity.name || 'Resident'}</p>
      </div>

      <nav className="flex-1 px-3 space-y-1.5 mt-4 overflow-y-auto">
        <Link href="/tenant/dashboard" data-tour-id="tenant-nav-dashboard" className={`flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-colors ${pathname === '/tenant/dashboard' ? 'bg-emerald-600 text-white font-bold shadow-xs' : 'hover:bg-white/10 text-slate-300'}`}>
          <Home size={16} className={pathname === '/tenant/dashboard' ? 'text-white' : 'text-emerald-300/80'} />
          <span>Dashboard & Billing</span>
        </Link>
        <Link href="/tenant/dashboard#move-out-notice" data-tour-id="tenant-nav-move-out" className="flex items-center gap-2.5 rounded-xl px-3 py-2 text-xs font-semibold text-slate-300 transition-colors hover:bg-white/10 hover:text-white">
          <DoorOpen size={16} className="text-emerald-300/80" />
          <span>Move-Out Notice</span>
        </Link>
        <Link href="/tenant/maintenance" data-tour-id="tenant-nav-maintenance" className={`flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-colors ${isOffboarding ? 'opacity-50 cursor-not-allowed pointer-events-none' : (pathname === '/tenant/maintenance' ? 'bg-emerald-600 text-white font-bold shadow-xs' : 'hover:bg-white/10 text-slate-300')}`}>
          <Wrench size={16} className={pathname === '/tenant/maintenance' ? 'text-white' : 'text-emerald-300/80'} />
          <span>Maintenance</span>
        </Link>
        <Link href="/tenant/profile" data-tour-id="tenant-nav-profile" className={`flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-colors ${pathname === '/tenant/profile' ? 'bg-emerald-600 text-white font-bold shadow-xs' : 'hover:bg-white/10 text-slate-300'}`}>
          <UserCircle size={16} className={pathname === '/tenant/profile' ? 'text-white' : 'text-emerald-300/80'} />
          <span>Profile</span>
        </Link>
      </nav>

      <div className="p-4 border-t border-emerald-900/40 shrink-0">
        <button 
          onClick={handleLogout}
          className="flex items-center gap-2.5 px-3 py-2 w-full text-left rounded-xl hover:bg-red-500/15 transition-colors text-red-300 text-xs font-semibold"
        >
          <LogOut size={16} />
          <span>Sign Out</span>
        </button>
      </div>
    </ResponsivePortalSidebar>
  );
}
