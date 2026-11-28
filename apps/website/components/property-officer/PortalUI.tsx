'use client';

import { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, LoaderCircle } from 'lucide-react';

export function PortalSectionBanner({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <section
      aria-label={`${title} section overview`}
      className="relative isolate mx-auto mt-5 max-w-[1440px] overflow-hidden rounded-[26px] bg-[#173b2e] px-6 py-7 text-white shadow-[0_16px_40px_rgba(23,59,46,0.16)] sm:px-8 sm:py-8"
    >
      <div aria-hidden="true" className="pointer-events-none absolute -right-20 -top-32 -z-10 h-80 w-80 rounded-full border-[42px] border-white/[0.035]" />
      <div aria-hidden="true" className="pointer-events-none absolute -bottom-36 right-28 -z-10 h-64 w-64 rounded-full border-[32px] border-[#c5f06e]/[0.07]" />
      <div className="relative max-w-3xl">
        <p className="text-sm font-semibold text-[#c5e1b4]">{eyebrow}</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-[38px]">{title}</h1>
        <p className="mt-3 max-w-xl text-sm leading-relaxed text-white/65">{description}</p>
      </div>
    </section>
  );
}

export function PortalLoading({ label, rows = 4 }: { label: string; rows?: number }) {
  return (
    <div role="status" aria-label={label} className="portal-loading rounded-2xl border border-[#e7ebe5] bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-[#526458]">
        <LoaderCircle size={16} className="animate-spin text-[#668a4c]" />
        {label}
      </div>
      <div aria-hidden="true" className="space-y-3">
        {Array.from({ length: rows }, (_, index) => (
          <div key={index} className="flex items-center gap-3">
            <span className="portal-skeleton h-9 w-9 shrink-0 rounded-xl" />
            <span className="min-w-0 flex-1 space-y-2">
              <span className="portal-skeleton block h-3 w-2/5 rounded-full" />
              <span className="portal-skeleton block h-2.5 w-3/4 rounded-full" />
            </span>
            <span className="portal-skeleton hidden h-7 w-20 rounded-lg sm:block" />
          </div>
        ))}
      </div>
      <span className="sr-only">Please wait while this section loads.</span>
    </div>
  );
}

export function usePortalPagination<T>(records: T[], initialPageSize = 10) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(initialPageSize);
  const pageCount = Math.max(1, Math.ceil(records.length / pageSize));
  const currentPage = Math.min(page, pageCount);

  useEffect(() => {
    setPage(1);
  }, [records]);

  const pageRecords = useMemo(
    () => records.slice((currentPage - 1) * pageSize, currentPage * pageSize),
    [currentPage, pageSize, records],
  );

  function changePageSize(value: number) {
    setPageSize(value);
    setPage(1);
  }

  return {
    page: currentPage,
    pageCount,
    pageSize,
    pageRecords,
    setPage,
    setPageSize: changePageSize,
  };
}

export function PortalPagination({
  page,
  pageCount,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
}: {
  page: number;
  pageCount: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}) {
  if (total === 0) return null;

  const firstRecord = (page - 1) * pageSize + 1;
  const lastRecord = Math.min(page * pageSize, total);

  return (
    <nav aria-label="Record pages" className="flex flex-wrap items-center justify-between gap-3 border-t border-[#edf0eb] px-4 py-3 text-xs text-[#68776d] sm:px-5">
      <p aria-live="polite">
        Showing <span className="font-semibold text-[#344b3b]">{firstRecord}–{lastRecord}</span> of{' '}
        <span className="font-semibold text-[#344b3b]">{total}</span> records
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2">
          Rows
          <select
            aria-label="Rows per page"
            value={pageSize}
            onChange={(event) => onPageSizeChange(Number(event.target.value))}
            className="rounded-lg border border-[#dfe5df] bg-white px-2 py-1.5 text-xs font-semibold text-[#385343]"
          >
            {[10, 20, 50].map((size) => <option key={size} value={size}>{size}</option>)}
          </select>
        </label>
        <button
          type="button"
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          className="rounded-lg border border-[#dfe5df] bg-white p-1.5 text-[#385343] transition hover:border-[#bdd2b0] hover:bg-[#f5f8f3] disabled:cursor-not-allowed disabled:opacity-40"
        >
          <ChevronLeft size={15} />
        </button>
        <span aria-current="page" className="min-w-16 text-center font-semibold text-[#344b3b]">
          Page {page} of {pageCount}
        </span>
        <button
          type="button"
          aria-label="Next page"
          disabled={page >= pageCount}
          onClick={() => onPageChange(page + 1)}
          className="rounded-lg border border-[#dfe5df] bg-white p-1.5 text-[#385343] transition hover:border-[#bdd2b0] hover:bg-[#f5f8f3] disabled:cursor-not-allowed disabled:opacity-40"
        >
          <ChevronRight size={15} />
        </button>
      </div>
    </nav>
  );
}
