'use client';

import { useEffect, useMemo, useState } from 'react';
import { ArrowUpRight, ArrowDownRight, Users, CheckCircle, AlertCircle, ArrowRight, PlayCircle } from 'lucide-react';
import { LedgerTable } from '../../../components/landlord/LedgerTable';
import Link from 'next/link';

type DashboardSummary = {
  totalExpectedRevenue: number;
  totalCollected: number;
  outstandingArrears: number;
  occupancyRate: number;
  occupiedUnits: number;
  totalUnits: number;
};

type LedgerEntry = {
  id: string;
  unitId: string;
  unitCode: string;
  tenantName: string;
  newCharges: number;
  priorArrears: number;
  expected: number;
  paid: number;
  balance: number;
  status: string;
};

const currency = new Intl.NumberFormat('en-KE', {
  style: 'currency',
  currency: 'KES',
  maximumFractionDigits: 0,
});

export default function LandlordDashboard() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [pendingData, setPendingData] = useState<any>(null);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadDashboard = async () => {
      try {
        const response = await fetch('/api/dashboard');
        const payload = await response.json();

        if (!response.ok) {
          throw new Error(payload.error || 'Unable to load dashboard data');
        }

        setSummary(payload.summary);
        setPendingData(payload.pendingActionsData);
        setLedger(payload.ledger || []);
      } catch (error) {
        console.error('Dashboard load failed:', error);
      } finally {
        setLoading(false);
      }
    };

    loadDashboard();
  }, []);

  const collectionRate = useMemo(() => {
    if (!summary || summary.totalExpectedRevenue === 0) return 0;
    return (summary.totalCollected / summary.totalExpectedRevenue) * 100;
  }, [summary]);

  const overdueUnits = useMemo(
    () => ledger.filter((entry) => Number(entry.balance || 0) > 0).length,
    [ledger]
  );

  const pendingActions = useMemo(() => {
    const actions: { title: string; description: string; tone: 'warning' | 'info' | 'urgent' }[] = [];

    if (!summary || !pendingData) return actions;

    if (summary.totalUnits === 0) {
      actions.push({
        title: 'No units configured yet',
        description: 'Add properties and assign tenants before billing begins.',
        tone: 'info',
      });
      return actions;
    }

    if (pendingData.maintenanceCount > 0) {
      actions.push({
        title: `${pendingData.maintenanceCount} unresolved maintenance ticket(s)`,
        description: 'Review and respond to pending tenant maintenance requests.',
        tone: 'warning',
      });
    }

    if (pendingData.offboardingCount > 0) {
      actions.push({
        title: `${pendingData.offboardingCount} tenant(s) pending offboarding`,
        description: 'Settle deposit balances and mark units as vacant.',
        tone: 'warning',
      });
    }

    if (pendingData.isWaterReadingWindow) {
      actions.push({
        title: 'Water readings due soon',
        description: 'Ensure caretaker inputs water meter readings before the end of the month for accurate billing.',
        tone: 'urgent',
      });
    }

    if (overdueUnits > 0) {
      actions.push({
        title: `${overdueUnits} unit(s) need follow-up`,
        description: 'Review unpaid and partial balances in the ledger before the next billing cycle.',
        tone: 'info', // Using info since maintenance/offboarding take precedence as warning
      });
    }

    if (actions.length === 0) {
      actions.push({
        title: 'Everything is on track',
        description: 'No immediate action is required at the moment.',
        tone: 'info',
      });
    }

    return actions;
  }, [overdueUnits, summary, pendingData]);

  if (loading) {
    return <div className="text-slate-600">Loading dashboard data...</div>;
  }

  return (
    <div className="space-y-6" data-tour-id="dashboard-main">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold text-slate-900">Executive Summary</h1>
        <div className="flex items-center gap-3">
          <button
            onClick={() => window.location.assign('?tour=true')}
            className="flex items-center gap-2 border border-emerald-200 bg-emerald-50 hover:bg-emerald-100 text-emerald-700 rounded-lg px-3 py-2 text-sm font-semibold transition-colors"
          >
            <PlayCircle size={16} /> Take Tour
          </button>
          <div className="bg-white border border-slate-300 rounded-lg px-4 py-2 text-sm font-medium shadow-sm text-slate-600">
            {summary ? 'Current month' : 'No data'}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <p className="text-sm font-medium text-slate-500">Total Expected Revenue</p>
          <h2 className="text-2xl font-bold text-slate-900 mt-2">
            {currency.format(summary?.totalExpectedRevenue || 0)}
          </h2>
          <div className="flex items-center gap-1 text-emerald-600 text-sm mt-2 font-medium">
            <ArrowUpRight size={16} /> <span>Live billing summary</span>
          </div>
        </div>

        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <p className="text-sm font-medium text-slate-500">Total Collected</p>
          <h2 className="text-2xl font-bold text-emerald-600 mt-2">
            {currency.format(summary?.totalCollected || 0)}
          </h2>
          <div className="flex items-center gap-1 text-emerald-600 text-sm mt-2 font-medium">
            <span>{collectionRate.toFixed(1)}% Collection Rate</span>
          </div>
        </div>

        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <p className="text-sm font-medium text-slate-500">Outstanding Arrears</p>
          <h2 className="text-2xl font-bold text-red-600 mt-2">
            {currency.format(summary?.outstandingArrears || 0)}
          </h2>
          <div className="flex items-center gap-1 text-red-600 text-sm mt-2 font-medium">
            <ArrowDownRight size={16} /> <span>{overdueUnits} units outstanding</span>
          </div>
        </div>

        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <p className="text-sm font-medium text-slate-500">Occupancy Status</p>
          <h2 className="text-2xl font-bold text-slate-900 mt-2">
            {summary ? `${summary.occupiedUnits} / ${summary.totalUnits}` : '0 / 0'}
          </h2>
          <div className="flex items-center gap-1 text-slate-500 text-sm mt-2 font-medium">
            <span>{summary ? `${summary.occupiedUnits < summary.totalUnits ? summary.totalUnits - summary.occupiedUnits : 0} units vacant` : 'No units assigned'}</span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
          <h3 className="text-lg font-bold text-slate-900 mb-4">Pending Actions</h3>
          <ul className="space-y-4">
            {pendingActions.map((action, index) => (
              <li key={`${action.title}-${index}`} className="flex items-start gap-3">
                {action.tone === 'warning' ? (
                  <AlertCircle className="text-amber-500 mt-0.5" size={20} />
                ) : action.tone === 'urgent' ? (
                  <AlertCircle className="text-red-500 mt-0.5 animate-pulse" size={20} />
                ) : (
                  <CheckCircle className="text-emerald-500 mt-0.5" size={20} />
                )}
                <div>
                  <p className="font-medium text-slate-900">{action.title}</p>
                  <p className="text-sm text-slate-500">{action.description}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
          <h3 className="text-lg font-bold text-slate-900 mb-4">Occupancy Snapshot</h3>
          <div className="space-y-4">
            <div>
              <div className="flex items-center justify-between text-sm text-slate-600 mb-1">
                <span>Occupied</span>
                <span>{summary ? `${summary.occupancyRate.toFixed(1)}%` : '0%'}</span>
              </div>
              <div className="h-2.5 bg-slate-200 rounded-full overflow-hidden">
                <div
                  className="h-full bg-emerald-500 rounded-full"
                  style={{ width: `${summary?.occupancyRate || 0}%` }}
                />
              </div>
            </div>

            <div className="bg-slate-50 rounded-lg p-4 text-sm text-slate-600">
              <div className="flex items-center justify-between">
                <span>Vacant units</span>
                <strong className="text-slate-900">
                  {summary ? summary.totalUnits - summary.occupiedUnits : 0}
                </strong>
              </div>
              <div className="flex items-center justify-between mt-2">
                <span>Active units</span>
                <strong className="text-slate-900">{summary?.occupiedUnits ?? 0}</strong>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <div className="p-6 border-b border-slate-200 flex justify-between items-center bg-slate-50">
          <h3 className="text-lg font-bold text-slate-900">Current Billing Cycle Ledger</h3>
        </div>
        <LedgerTable records={ledger.slice(0, 5)} />
        {ledger.length === 0 ? (
          <div className="p-8 text-center text-slate-500">
            No active billing records for the current month.
          </div>
        ) : (
          <div className="p-4 border-t border-slate-200 bg-slate-50 flex justify-center">
            <Link href="/landlord/ledger" className="text-sm font-bold text-emerald-600 hover:text-emerald-700 flex items-center gap-1 bg-white border border-emerald-200 px-4 py-2 rounded-lg hover:bg-emerald-50 transition-colors">
              View full ledger table <ArrowRight size={16} />
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
