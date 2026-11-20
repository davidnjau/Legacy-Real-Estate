'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Home, Wrench, UserCircle, DoorOpen } from 'lucide-react';
import SignOutButton from '../auth/SignOutButton';
import { ResponsivePortalSidebar } from '../ui/ResponsivePortalSidebar';

export function Sidebar() {
  const pathname = usePathname();
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

  return (
    <ResponsivePortalSidebar
      label="Tenant navigation"
      desktopWidthClass="md:w-[260px]"
      surface="light"
      className="border-r border-[#e6e9e5] bg-white px-4 py-6 text-[#172820]"
    >
      <div className="mb-6 shrink-0 px-3">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#91a095]">Workspace</p>
        <p className="mt-1 truncate text-xs text-[#8a978e]">{propertyName === '...' ? 'Resident hub' : propertyName}</p>
        <p className="mt-1 truncate text-[11px] font-medium text-[#68776d]">
          Unit {identity.unitCode || 'NAI-SV-A3'} · {identity.name || 'Resident'}
        </p>
      </div>

      <nav className="flex-1 space-y-5 overflow-y-auto">
        <div>
          <p className="px-3 pb-2 text-[9px] font-bold uppercase tracking-[0.18em] text-[#a0aaa2]">Home</p>
          <Link
            href="/tenant/dashboard"
            data-tour-id="tenant-nav-dashboard"
            aria-current={pathname === '/tenant/dashboard' ? 'page' : undefined}
            className={`group flex items-center gap-3 rounded-xl border px-3 py-2.5 text-[12px] transition ${
              pathname === '/tenant/dashboard'
                ? 'border-[#e1ebd8] bg-[#f1f6ed] font-bold text-[#234630] shadow-[0_2px_8px_rgba(30,60,40,0.04)]'
                : 'border-transparent font-medium text-[#68776d] hover:bg-[#f7f9f6] hover:text-[#234630]'
            }`}
          >
            <Home size={16} strokeWidth={pathname === '/tenant/dashboard' ? 2.2 : 1.8} className={pathname === '/tenant/dashboard' ? 'text-[#668a4c]' : 'text-[#91a095] transition group-hover:text-[#668a4c]'} />
            <span className="flex-1">Dashboard & Billing</span>
            {pathname === '/tenant/dashboard' && <span className="h-1.5 w-1.5 rounded-full bg-[#83a95d]" />}
          </Link>
        </div>

        <div>
          <p className="px-3 pb-2 text-[9px] font-bold uppercase tracking-[0.18em] text-[#a0aaa2]">Tenancy</p>
          <div className="space-y-1">
            <Link
              href="/tenant/dashboard#move-out-notice"
              data-tour-id="tenant-nav-move-out"
              className="group flex items-center gap-3 rounded-xl border border-transparent px-3 py-2.5 text-[12px] font-medium text-[#68776d] transition hover:bg-[#f7f9f6] hover:text-[#234630]"
            >
              <DoorOpen size={16} className="text-[#91a095] transition group-hover:text-[#668a4c]" />
              <span>Move-Out Notice</span>
            </Link>
            <Link
              href="/tenant/maintenance"
              data-tour-id="tenant-nav-maintenance"
              aria-current={pathname === '/tenant/maintenance' ? 'page' : undefined}
              className={`group flex items-center gap-3 rounded-xl border px-3 py-2.5 text-[12px] transition ${
                isOffboarding
                  ? 'pointer-events-none border-transparent font-medium text-[#a0aaa2] opacity-50'
                  : pathname === '/tenant/maintenance'
                    ? 'border-[#e1ebd8] bg-[#f1f6ed] font-bold text-[#234630] shadow-[0_2px_8px_rgba(30,60,40,0.04)]'
                    : 'border-transparent font-medium text-[#68776d] hover:bg-[#f7f9f6] hover:text-[#234630]'
              }`}
            >
              <Wrench size={16} strokeWidth={pathname === '/tenant/maintenance' ? 2.2 : 1.8} className={pathname === '/tenant/maintenance' ? 'text-[#668a4c]' : 'text-[#91a095] transition group-hover:text-[#668a4c]'} />
              <span className="flex-1">Maintenance</span>
              {pathname === '/tenant/maintenance' && <span className="h-1.5 w-1.5 rounded-full bg-[#83a95d]" />}
            </Link>
          </div>
        </div>

        <div>
          <p className="px-3 pb-2 text-[9px] font-bold uppercase tracking-[0.18em] text-[#a0aaa2]">Account</p>
          <Link
            href="/tenant/profile"
            data-tour-id="tenant-nav-profile"
            aria-current={pathname === '/tenant/profile' ? 'page' : undefined}
            className={`group flex items-center gap-3 rounded-xl border px-3 py-2.5 text-[12px] transition ${
              pathname === '/tenant/profile'
                ? 'border-[#e1ebd8] bg-[#f1f6ed] font-bold text-[#234630] shadow-[0_2px_8px_rgba(30,60,40,0.04)]'
                : 'border-transparent font-medium text-[#68776d] hover:bg-[#f7f9f6] hover:text-[#234630]'
            }`}
          >
            <UserCircle size={16} strokeWidth={pathname === '/tenant/profile' ? 2.2 : 1.8} className={pathname === '/tenant/profile' ? 'text-[#668a4c]' : 'text-[#91a095] transition group-hover:text-[#668a4c]'} />
            <span className="flex-1">Profile</span>
            {pathname === '/tenant/profile' && <span className="h-1.5 w-1.5 rounded-full bg-[#83a95d]" />}
          </Link>
        </div>
      </nav>

      <div className="mt-auto shrink-0 border-t border-[#e7ece5] pt-4">
        <SignOutButton />
      </div>
    </ResponsivePortalSidebar>
  );
}
