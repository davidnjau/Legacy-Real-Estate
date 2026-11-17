'use client';

import { useEffect, useState, type FormEvent } from 'react';
import { Download, FileText, Printer, RefreshCw } from 'lucide-react';

type StatementSummary = {
  billed_rent: string;
  collected_invoice_allocations: string;
  arrears: string;
  commission: string | null;
  expenses: string;
  maintenance_costs: string;
  tax: string | null;
  net_payout: string | null;
};

type PropertyStatement = {
  property_id: number;
  property: string;
  billed_rent: string;
  collected_invoice_allocations: string;
  arrears: string;
  expenses: string;
  maintenance_costs: string;
};

type MaintenanceCostAllocation = {
  id: number;
  quotation_id: number;
  ticket_id: number;
  ticket_title: string;
  property_id: number;
  property: string;
  unit_code: string | null;
  vendor: string;
  description: string;
  reason: string;
  amount: string;
  allocated_at: string;
};

type Statement = {
  landlord: { id: number; name: string };
  period_start: string;
  period_end: string;
  property_id: number | null;
  property_options: { id: number; name: string }[];
  commission_basis: string;
  commission_status: 'configured' | 'not_configured' | 'not_allocated';
  tax_status: 'configured' | 'not_configured' | 'not_applicable';
  summary: StatementSummary;
  properties: PropertyStatement[];
  maintenance_cost_allocations: MaintenanceCostAllocation[];
  trust_deposits: {
    id: number;
    property_id: number;
    property: string;
    unit_code: string;
    tenant_name: string;
    balance: string;
    target_amount: string;
  }[];
  trust_deposits_total: string;
  remittances: {
    id: number;
    period_start: string;
    period_end: string;
    revision: number;
    summary: {
      net_payout: string;
    };
    status: string;
    payout_reference: string | null;
    payout_date: string | null;
  }[];
};

type ApiError = { error?: string };

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 2,
});

function initialMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

function monthRange(month: string) {
  const [year, monthNumber] = month.split('-').map(Number);
  if (!year || monthNumber < 1 || monthNumber > 12) {
    throw new Error('Choose a valid month.');
  }
  const lastDay = new Date(year, monthNumber, 0).getDate();
  return {
    from: `${month}-01`,
    to: `${month}-${String(lastDay).padStart(2, '0')}`,
  };
}

function money(value: string | null) {
  return value === null ? null : currency.format(Number(value));
}

export default function LandlordStatementsPage() {
  const [month, setMonth] = useState(initialMonth);
  const [propertyId, setPropertyId] = useState('');
  const [annualYear, setAnnualYear] = useState(String(new Date().getFullYear()));
  const [statement, setStatement] = useState<Statement | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const loadStatement = async (selectedMonth: string, selectedPropertyId: string) => {
    setLoading(true);
    setError('');
    try {
      if (!/^\d{4}-\d{2}$/.test(selectedMonth)) {
        throw new Error('Choose a valid month.');
      }
      const query = new URLSearchParams(monthRange(selectedMonth));
      if (selectedPropertyId) query.set('property_id', selectedPropertyId);
      const response = await fetch(`/api/landlord/statements?${query}`, {
        cache: 'no-store',
      });
      const payload = (await response.json()) as Statement & ApiError;
      if (!response.ok) {
        throw new Error(payload.error || 'Unable to load the statement.');
      }
      if (
        !payload.summary ||
        !Array.isArray(payload.properties) ||
        !Array.isArray(payload.property_options) ||
        !Array.isArray(payload.trust_deposits) ||
        !Array.isArray(payload.remittances) ||
        !Array.isArray(payload.maintenance_cost_allocations)
      ) {
        throw new Error('The statement response was incomplete.');
      }
      setStatement(payload);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Unable to load the statement.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadStatement(month, propertyId);
  }, []);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void loadStatement(month, propertyId);
  };

  const exportUrl = statement ? (() => {
    const query = new URLSearchParams({
      from: statement.period_start,
      to: statement.period_end,
      format: 'csv',
    });
    if (statement.property_id !== null) query.set('property_id', String(statement.property_id));
    return `/api/landlord/statements?${query}`;
  })() : undefined;
  const annualMriUrl = (() => {
    const query = new URLSearchParams({
      report: 'annual_mri',
      year: annualYear,
      format: 'csv',
    });
    if (propertyId) query.set('property_id', propertyId);
    return `/api/landlord/statements?${query}`;
  })();
  const isAnnualYearValid = /^\d{4}$/.test(annualYear)
    && Number(annualYear) >= 1900
    && Number(annualYear) <= 9998;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-slate-900">Statements &amp; Remittances</h1>
          <p className="mt-1 text-sm text-slate-500">
            Review property income, trust deposits, statements, and recorded payout status.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 print:hidden">
          {exportUrl && (
            <a
              href={exportUrl}
              className="inline-flex items-center gap-2 rounded-lg bg-emerald-700 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-800"
            >
              <Download size={16} aria-hidden="true" /> Export statement
            </a>
          )}
          <button
            type="button"
            onClick={() => window.print()}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          >
            <Printer size={16} aria-hidden="true" /> Print / Save PDF
          </button>
        </div>
      </header>

      <form
        onSubmit={handleSubmit}
        className="flex flex-wrap items-end gap-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm print:hidden"
      >
        <label className="grid gap-1 text-sm font-medium text-slate-700">
          Month
          <input
            type="month"
            required
            value={month}
            onChange={(event) => setMonth(event.target.value)}
            className="rounded-lg border border-slate-300 px-3 py-2"
          />
        </label>
        <label className="grid gap-1 text-sm font-medium text-slate-700">
          Property
          <select
            value={propertyId}
            onChange={(event) => setPropertyId(event.target.value)}
            className="min-w-52 rounded-lg border border-slate-300 bg-white px-3 py-2"
          >
            <option value="">All properties</option>
            {(statement?.property_options ?? []).map((property) => (
              <option key={property.id} value={property.id}>{property.name}</option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-slate-50 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100 disabled:opacity-50"
        >
          <RefreshCw size={16} aria-hidden="true" className={loading ? 'animate-spin' : ''} />
          {loading ? 'Loading…' : 'View statement'}
        </button>
      </form>

      <div className="flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm print:hidden">
        <label className="grid gap-1 text-sm font-medium text-slate-700">
          Annual MRI summary year
          <input
            type="number"
            min="1900"
            max="9998"
            value={annualYear}
            onChange={(event) => setAnnualYear(event.target.value)}
            className="w-36 rounded-lg border border-slate-300 px-3 py-2"
          />
        </label>
        <a
          href={isAnnualYearValid ? annualMriUrl : undefined}
          aria-disabled={!isAnnualYearValid}
          className={`inline-flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-semibold ${
            isAnnualYearValid
              ? 'border-emerald-700 text-emerald-800 hover:bg-emerald-50'
              : 'cursor-not-allowed border-slate-300 text-slate-400'
          }`}
        >
          <Download size={16} aria-hidden="true" /> Export annual MRI CSV
        </a>
        <p className="text-xs text-slate-500">
          Annual tax values follow the approved MRI settings on the management agreement.
        </p>
      </div>

      {error && (
        <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          {error}
        </p>
      )}

      {loading && !statement && !error && (
        <p className="text-sm text-slate-600">Loading statement…</p>
      )}

      {statement && (
        <>
          {(statement.commission_status === 'not_configured' || statement.tax_status === 'not_configured') && (
            <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
              <p className="font-semibold">Statement deductions need configuration.</p>
              <p className="mt-1">
                {statement.commission_status === 'not_configured' && 'A commission agreement is missing or incomplete. '}
                {statement.tax_status === 'not_configured' &&
                  'Tax is enabled by the agreement, but no approved statement-tax configuration is available. '}
                Net payout is withheld until the applicable settings are complete.
              </p>
            </div>
          )}
          {statement.commission_status === 'not_allocated' && (
            <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
              This agreement uses a fixed landlord-level fee. It cannot be attributed accurately to one property,
              so a property-filtered net payout is not shown.
            </div>
          )}

          <div className="flex items-center gap-2 text-sm text-slate-600">
            <FileText size={17} aria-hidden="true" />
            <span>
              {statement.landlord.name} · {statement.period_start} to {statement.period_end}
            </span>
          </div>

          <section aria-label="Statement totals" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {[
              ['Billed rent', statement.summary.billed_rent],
              ['Collected invoice allocations', statement.summary.collected_invoice_allocations],
              ['Arrears', statement.summary.arrears],
              ['Commission', statement.summary.commission],
              ['Approved expenses', statement.summary.expenses],
              ['Landlord maintenance allocations', statement.summary.maintenance_costs],
              ['Applicable tax', statement.summary.tax],
              ['Trust deposits held', statement.trust_deposits_total],
            ].map(([label, amount]) => (
              <article key={label} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                <p className="text-sm font-medium text-slate-500">{label}</p>
                <p className="mt-2 text-2xl font-bold text-slate-900">
                  {money(amount) ?? (
                    label === 'Commission' && statement.commission_status === 'not_allocated'
                      ? 'Not allocated'
                      : 'Unavailable'
                  )}
                  {label === 'Applicable tax' && statement.tax_status === 'not_applicable' && (
                    <span className="ml-2 text-xs font-medium text-slate-500">Not applicable</span>
                  )}
                  {label === 'Applicable tax' && statement.tax_status === 'not_configured' && (
                    <span className="ml-2 text-xs font-medium text-amber-700">Not configured</span>
                  )}
                </p>
              </article>
            ))}
          </section>

          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <p className="text-sm font-medium text-slate-500">Estimated net payout</p>
            <p className="mt-2 text-3xl font-bold text-emerald-700">
              {statement.summary.net_payout === null
                ? statement.commission_status === 'not_allocated'
                  ? 'Not allocated'
                  : 'Unavailable'
                : money(statement.summary.net_payout)}
            </p>
            <p className="mt-2 text-xs text-slate-500">
              Calculated from collections less commission, approved expenses, landlord-allocated maintenance costs,
              and configured applicable tax.
              It is a statement estimate, not a payment or remittance record.
            </p>
            <p className="mt-1 text-xs text-slate-500">
              Collection and commission basis: allocated invoice collections (may include utilities and other billed charges).
            </p>
          </section>

          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-200 bg-slate-50 p-5">
              <h2 className="text-lg font-bold text-slate-900">Property breakdown</h2>
            </div>
            {statement.properties.length === 0 ? (
              <p className="p-6 text-sm text-slate-500">No properties are linked to this landlord account.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-slate-200 text-sm">
                  <thead className="bg-white text-left text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th scope="col" className="px-5 py-3">Property</th>
                      <th scope="col" className="px-5 py-3">Billed rent</th>
                      <th scope="col" className="px-5 py-3">Collected allocations</th>
                      <th scope="col" className="px-5 py-3">Arrears</th>
                      <th scope="col" className="px-5 py-3">Approved expenses</th>
                      <th scope="col" className="px-5 py-3">Maintenance costs</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {statement.properties.map((property) => (
                      <tr key={property.property_id}>
                        <th scope="row" className="px-5 py-4 text-left font-semibold text-slate-900">
                          {property.property}
                        </th>
                        <td className="whitespace-nowrap px-5 py-4">{money(property.billed_rent)}</td>
                        <td className="whitespace-nowrap px-5 py-4">{money(property.collected_invoice_allocations)}</td>
                        <td className="whitespace-nowrap px-5 py-4">{money(property.arrears)}</td>
                        <td className="whitespace-nowrap px-5 py-4">{money(property.expenses)}</td>
                        <td className="whitespace-nowrap px-5 py-4">{money(property.maintenance_costs)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-200 bg-slate-50 p-5">
              <h2 className="text-lg font-bold text-slate-900">Landlord maintenance allocations</h2>
              <p className="mt-1 text-xs text-slate-500">
                Approved quotation costs allocated to the landlord during this statement period.
              </p>
            </div>
            {statement.maintenance_cost_allocations.length === 0 ? (
              <p className="p-6 text-sm text-slate-500">No landlord maintenance costs were allocated in this period.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-slate-200 text-sm">
                  <thead className="bg-white text-left text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th scope="col" className="px-5 py-3">Property / unit</th>
                      <th scope="col" className="px-5 py-3">Maintenance ticket</th>
                      <th scope="col" className="px-5 py-3">Vendor / quotation</th>
                      <th scope="col" className="px-5 py-3">Allocation reason</th>
                      <th scope="col" className="px-5 py-3">Allocated</th>
                      <th scope="col" className="px-5 py-3">Date</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {statement.maintenance_cost_allocations.map((allocation) => (
                      <tr key={allocation.id}>
                        <th scope="row" className="px-5 py-4 text-left font-semibold text-slate-900">
                          {allocation.property}
                          {allocation.unit_code ? ` · ${allocation.unit_code}` : ''}
                        </th>
                        <td className="px-5 py-4">
                          #{allocation.ticket_id} · {allocation.ticket_title}
                        </td>
                        <td className="px-5 py-4">
                          <span className="font-medium">{allocation.vendor}</span>
                          <span className="block text-xs text-slate-500">{allocation.description}</span>
                        </td>
                        <td className="px-5 py-4">{allocation.reason || '—'}</td>
                        <td className="whitespace-nowrap px-5 py-4 font-semibold">
                          {money(allocation.amount)}
                        </td>
                        <td className="whitespace-nowrap px-5 py-4">
                          {new Date(allocation.allocated_at).toLocaleDateString('en-KE')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-200 bg-slate-50 p-5">
              <h2 className="text-lg font-bold text-slate-900">Trust deposit balances</h2>
              <p className="mt-1 text-xs text-slate-500">
                Current total tenancy deposit balances; the system does not split security and utility deposits.
              </p>
            </div>
            {statement.trust_deposits.length === 0 ? (
              <p className="p-6 text-sm text-slate-500">No deposit accounts are recorded for this selection.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-slate-200 text-sm">
                  <thead className="bg-white text-left text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th scope="col" className="px-5 py-3">Property / unit</th>
                      <th scope="col" className="px-5 py-3">Tenant</th>
                      <th scope="col" className="px-5 py-3">Target</th>
                      <th scope="col" className="px-5 py-3">Balance held</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {statement.trust_deposits.map((deposit) => (
                      <tr key={deposit.id}>
                        <th scope="row" className="px-5 py-4 text-left font-semibold text-slate-900">
                          {deposit.property} · {deposit.unit_code}
                        </th>
                        <td className="px-5 py-4">{deposit.tenant_name}</td>
                        <td className="whitespace-nowrap px-5 py-4">{money(deposit.target_amount)}</td>
                        <td className="whitespace-nowrap px-5 py-4 font-semibold">{money(deposit.balance)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-200 bg-slate-50 p-5">
              <h2 className="text-lg font-bold text-slate-900">Remittance records</h2>
              <p className="mt-1 text-xs text-slate-500">
                Latest revision for each overlapping remittance period. Remittances are portfolio-wide, even when a property filter is selected.
              </p>
            </div>
            {statement.remittances.length === 0 ? (
              <p className="p-6 text-sm text-slate-500">No remittances have been prepared for this period.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-slate-200 text-sm">
                  <thead className="bg-white text-left text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th scope="col" className="px-5 py-3">Period</th>
                      <th scope="col" className="px-5 py-3">Revision</th>
                      <th scope="col" className="px-5 py-3">Payout</th>
                      <th scope="col" className="px-5 py-3">Status</th>
                      <th scope="col" className="px-5 py-3">Reference / date</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {statement.remittances.map((remittance) => (
                      <tr key={remittance.id}>
                        <td className="whitespace-nowrap px-5 py-4">
                          {remittance.period_start} to {remittance.period_end}
                        </td>
                        <td className="px-5 py-4">{remittance.revision}</td>
                        <td className="whitespace-nowrap px-5 py-4 font-semibold">
                          {money(remittance.summary.net_payout)}
                        </td>
                        <td className="px-5 py-4 capitalize">{remittance.status.replaceAll('_', ' ')}</td>
                        <td className="px-5 py-4">
                          {remittance.payout_reference || '—'}
                          {remittance.payout_date && (
                            <span className="block text-xs text-slate-500">{remittance.payout_date}</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
