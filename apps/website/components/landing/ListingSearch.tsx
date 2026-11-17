'use client';

import { useEffect, useState } from 'react';
import { ArrowRight, BedDouble, MapPin, Search } from 'lucide-react';

type Listing = {
  code: string;
  unit_type: string;
  bedrooms: number;
  property_name: string;
  branch: string;
  town: string;
  location: string;
  rent: string;
  deposit: string;
  photos: string[];
};

const money = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

export default function ListingSearch() {
  const [query, setQuery] = useState('');
  const [listings, setListings] = useState<Listing[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    fetch(`/api/public/listings${query ? `?${query}` : ''}`, {
      cache: 'no-store',
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.headers.get('content-type')?.includes('application/json')) {
          throw new Error('The listings service returned an unexpected response. Please try again later.');
        }
        const result = await response.json() as { error?: string; listings?: Listing[] };
        if (!response.ok) throw new Error(result.error || 'Unable to load current listings.');
        setListings(Array.isArray(result.listings) ? result.listings : []);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : 'Unable to load current listings.');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query]);

  function search(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const params = new URLSearchParams();
    for (const key of ['branch', 'area', 'type', 'rent_min', 'rent_max']) {
      const value = String(values.get(key) || '').trim();
      if (value) params.set(key, value);
    }
    setQuery(params.toString());
  }

  return (
    <section id="homes" className="bg-[#f5f7f5] px-5 py-16 text-slate-950 sm:px-8 sm:py-20">
      <div className="mx-auto max-w-7xl">
        <div className="max-w-2xl">
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-800">Find your next home</p>
          <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">Homes currently open for you.</h2>
          <p className="mt-3 text-sm leading-6 text-slate-600">Search verified available units managed by Legacy Real Estate.</p>
        </div>

        <form onSubmit={search} className="mt-7 grid gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:grid-cols-2 lg:grid-cols-[1fr_1.2fr_1fr_0.8fr_0.8fr_auto] lg:items-end lg:p-5">
          <label className="text-xs font-semibold text-slate-600">
            Branch
            <select name="branch" defaultValue="" className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm font-normal text-slate-800">
              <option value="">All branches</option><option>Nairobi</option><option>Nakuru</option>
            </select>
          </label>
          <label className="text-xs font-semibold text-slate-600">
            Area / estate
            <input name="area" placeholder="e.g. Kahawa" className="mt-1.5 w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm font-normal text-slate-800" />
          </label>
          <label className="text-xs font-semibold text-slate-600">
            Home type
            <select name="type" defaultValue="" className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm font-normal text-slate-800">
              <option value="">Any type</option><option>Bedsitter</option><option>1BR</option><option>Own Compound</option>
            </select>
          </label>
          <label className="text-xs font-semibold text-slate-600">
            Min rent
            <input name="rent_min" type="number" min="0" placeholder="KES" className="mt-1.5 w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm font-normal text-slate-800" />
          </label>
          <label className="text-xs font-semibold text-slate-600">
            Max rent
            <input name="rent_max" type="number" min="0" placeholder="KES" className="mt-1.5 w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm font-normal text-slate-800" />
          </label>
          <button className="inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-900 px-5 py-3 text-sm font-semibold text-white hover:bg-emerald-950">
            <Search size={16} /> Search
          </button>
        </form>

        {loading ? (
          <p className="mt-8 text-sm text-slate-500">Checking available homes…</p>
        ) : error ? (
          <p role="alert" className="mt-8 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800">{error}</p>
        ) : listings.length === 0 ? (
          <div className="mt-8 rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-10 text-center">
            <p className="font-semibold text-slate-800">No listed homes match these filters yet.</p>
            <p className="mt-2 text-sm text-slate-500">Please check back soon or contact our team for availability.</p>
          </div>
        ) : (
          <div className="mt-8 grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
            {listings.map((listing) => (
              <article key={`${listing.property_name}-${listing.code}`} className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                <div
                  className="relative h-48 overflow-hidden bg-gradient-to-br from-emerald-950 via-emerald-800 to-emerald-600"
                >
                  {listing.photos[0] && (
                    <img src={listing.photos[0]} alt={listing.property_name} className="absolute inset-0 size-full object-cover" />
                  )}
                  {listing.photos[0] && <span className="absolute inset-0 bg-gradient-to-t from-emerald-950/60 to-transparent" />}
                  <span className="absolute left-4 top-4 rounded-full bg-white/95 px-3 py-1 text-xs font-semibold text-emerald-900">Available</span>
                  <span className="absolute bottom-4 left-4 text-sm font-semibold text-white">{listing.property_name}</span>
                </div>
                <div className="p-5">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h3 className="font-semibold text-slate-900">{listing.unit_type} · {listing.code}</h3>
                      <p className="mt-1 flex items-center gap-1 text-sm text-slate-500"><MapPin size={14} />{listing.location}, {listing.town}</p>
                    </div>
                    <span className="flex shrink-0 items-center gap-1 text-xs text-slate-500"><BedDouble size={14} />{listing.bedrooms ? `${listing.bedrooms} bedroom${listing.bedrooms === 1 ? '' : 's'}` : 'Studio'}</span>
                  </div>
                  <p className="mt-5 text-xl font-bold text-emerald-950">{money.format(Number(listing.rent))}<span className="text-xs font-normal text-slate-500"> / month</span></p>
                  <p className="mt-1 text-xs text-slate-500">
                    Deposit: {Number(listing.deposit) > 0 ? money.format(Number(listing.deposit)) : 'Not configured'}
                  </p>
                  <div className="mt-5 grid grid-cols-2 gap-2 border-t border-slate-100 pt-4">
                    <button type="button" disabled title="Viewing requests are not enabled yet" className="inline-flex items-center justify-center gap-1 rounded-lg bg-slate-100 px-2 py-2 text-xs font-semibold text-slate-500">
                      <ArrowRight size={14} />Book viewing
                    </button>
                    <button type="button" disabled title="Agency WhatsApp contact has not been configured" className="rounded-lg border border-slate-200 px-2 py-2 text-xs font-semibold text-slate-500">
                      WhatsApp us
                    </button>
                  </div>
                  <p className="mt-2 text-[11px] text-slate-400">Enquiry contacts will be enabled once agency details are configured.</p>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
