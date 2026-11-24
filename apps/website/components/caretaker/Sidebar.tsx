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
  const [properties, setProperties] = useState<{ id: number; name: string; branch_name: string }[]>([]);
  const [activePropertyId, setActivePropertyId] = useState('');
  const [propertyError, setPropertyError] = useState('');
  const [savingProperty, setSavingProperty] = useState(false);

  useEffect(() => {
    let active = true;
    void fetch('/api/caretaker/properties', { cache: 'no-store' })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || data.message || 'Unable to load assigned properties.');
        }
        return data;
      })
      .then((data) => {
        if (!active) return;
        const assignedProperties = Array.isArray(data.properties) ? data.properties : [];
        setProperties(assignedProperties);
        setActivePropertyId(
          String(data.activePropertyId ?? assignedProperties[0]?.id ?? ''),
        );
        return fetch('/api/caretaker/dashboard', { cache: 'no-store' })
          .then(async (response) => {
            const dashboard = await response.json();
            if (!response.ok) {
              throw new Error(dashboard.detail || dashboard.message || 'Unable to load the selected property.');
            }
            return dashboard;
          })
          .then((dashboard) => {
            if (active) {
              setHasPaidWater(
                Array.isArray(dashboard.properties) &&
                  dashboard.properties.some((property: { has_paid_water?: boolean }) => property.has_paid_water)
              );
            }
          });
      })
      .catch((error: unknown) => {
        if (active) {
          setPropertyError(error instanceof Error ? error.message : 'Unable to load caretaker properties.');
        }
      });
    return () => {
      active = false;
    };
  }, []);

  async function selectProperty(propertyId: string) {
    const parsedId = Number(propertyId);
    if (!Number.isInteger(parsedId) || parsedId <= 0 || savingProperty) return;
    setSavingProperty(true);
    setPropertyError('');
    try {
      const response = await fetch('/api/caretaker/properties', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ activePropertyId: parsedId }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || payload.message || 'Unable to select this property.');
      }
      setActivePropertyId(String(payload.activePropertyId));
      window.location.reload();
    } catch (error) {
      setPropertyError(error instanceof Error ? error.message : 'Unable to select this property.');
    } finally {
      setSavingProperty(false);
    }
  }

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
      <div className="mb-5 shrink-0 px-3">
        <label htmlFor="caretaker-property" className="block text-[10px] font-bold uppercase tracking-[0.16em] text-[#91a095]">
          Active property
        </label>
        <select
          id="caretaker-property"
          value={activePropertyId}
          disabled={savingProperty || properties.length === 0}
          onChange={(event) => void selectProperty(event.target.value)}
          className="mt-2 w-full rounded-xl border border-[#dfe5df] bg-white px-3 py-2.5 text-xs font-semibold text-[#294534] disabled:opacity-60"
        >
          {properties.length === 0 && <option value="">No assigned properties</option>}
          {properties.map((property) => (
            <option key={property.id} value={property.id}>
              {property.name}{property.branch_name ? ` · ${property.branch_name}` : ''}
            </option>
          ))}
        </select>
        {propertyError && <p role="alert" className="mt-2 text-xs text-red-700">{propertyError}</p>}
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
