'use client';

import { usePathname } from 'next/navigation';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Menu, X } from 'lucide-react';

type ResponsivePortalSidebarProps = {
  children: ReactNode;
  label: string;
  className?: string;
  desktopWidthClass?: string;
  fillDesktopHeight?: boolean;
};

export function ResponsivePortalSidebar({
  children,
  label,
  className = '',
  desktopWidthClass = 'md:w-64',
  fillDesktopHeight = false,
}: ResponsivePortalSidebarProps) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const panelId = `portal-nav-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
  // On desktop the sidebar fills the full height of the portal layout row,
  // stays fixed in place without shrinking, and scrolls internally if links exceed the viewport.
  void fillDesktopHeight;
  const desktopHeightClass = 'md:h-full md:min-h-full';

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!open) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    panelRef.current
      ?.querySelector<HTMLElement>('button:not([disabled]), a[href], input:not([disabled])')
      ?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
        return;
      }

      if (event.key !== 'Tab' || !panelRef.current) return;
      const focusable = Array.from(
        panelRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    const desktopQuery = window.matchMedia('(min-width: 768px)');
    const closeOnDesktop = (event: MediaQueryListEvent) => {
      if (event.matches) setOpen(false);
    };
    document.addEventListener('keydown', handleKeyDown);
    desktopQuery.addEventListener('change', closeOnDesktop);

    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', handleKeyDown);
      desktopQuery.removeEventListener('change', closeOnDesktop);
    };
  }, [open]);

  return (
    <div className="shrink-0 md:contents">
      <div className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-2.5 md:hidden">
        <button
          ref={triggerRef}
          type="button"
          aria-controls={panelId}
          aria-expanded={open}
          aria-label={`Open ${label}`}
          onClick={() => setOpen(true)}
          className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-800 shadow-sm transition hover:bg-slate-50 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500"
        >
          <Menu size={18} aria-hidden="true" />
          <span>Menu</span>
        </button>
        <span className="truncate pl-3 text-xs font-semibold text-slate-500">{label}</span>
      </div>

      {open && (
        <button
          type="button"
          aria-label={`Close ${label}`}
          onClick={() => setOpen(false)}
          className="fixed inset-0 z-40 bg-slate-950/50 backdrop-blur-[2px] transition-opacity duration-200 md:hidden"
        />
      )}

      <aside
        ref={panelRef}
        id={panelId}
        aria-label={label}
        role={open ? 'dialog' : undefined}
        aria-modal={open ? true : undefined}
        className={`fixed inset-y-0 left-0 z-50 flex h-dvh w-[min(20rem,88vw)] flex-col overflow-y-auto overscroll-contain shadow-2xl transition-transform duration-300 ease-out motion-reduce:transition-none md:static md:z-auto md:flex md:shrink-0 md:translate-x-0 md:flex-col md:overflow-y-auto md:overscroll-contain md:shadow-none ${desktopHeightClass} ${desktopWidthClass} ${open ? 'translate-x-0' : '-translate-x-full'} ${className}`}
      >
        <div className="flex min-h-14 items-center justify-between border-b border-white/10 px-4 md:hidden">
          <span className="text-sm font-semibold">Navigation</span>
          <button
            type="button"
            aria-label={`Close ${label}`}
            onClick={() => {
              setOpen(false);
              triggerRef.current?.focus();
            }}
            className="grid h-11 w-11 place-items-center rounded-xl transition hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            <X size={20} aria-hidden="true" />
          </button>
        </div>
        {children}
      </aside>
    </div>
  );
}
