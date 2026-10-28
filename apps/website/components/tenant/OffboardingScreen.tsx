'use client';
// components/tenant/OffboardingScreen.tsx

import { AlertCircle, Home, Clock, CheckCircle } from 'lucide-react';

interface OffboardingScreenProps {
  unitCode: string | null;
  unitStatus?: string;
}

export function OffboardingScreen({ unitCode, unitStatus }: OffboardingScreenProps) {
  const isDepositPending = unitStatus === 'deposit_pending';

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-amber-50 p-8">
      <div className="max-w-lg w-full text-center">
        {/* Icon */}
        <div className="w-24 h-24 bg-amber-100 border-4 border-amber-300 rounded-full flex items-center justify-center mx-auto mb-6">
          <Home size={40} className="text-amber-600" />
        </div>

        {/* Heading */}
        <h1 className="text-3xl font-bold text-amber-900">
          {isDepositPending ? 'You Have Moved Out' : 'Account in Offboarding'}
        </h1>

        {unitCode && (
          <p className="text-amber-700 mt-2 font-medium">Unit: {unitCode}</p>
        )}

        {/* Info card */}
        <div className="mt-8 bg-white border border-amber-200 rounded-2xl p-6 shadow-sm text-left space-y-4">
          {isDepositPending ? (
            <>
              <div className="flex items-start gap-3">
                <CheckCircle size={20} className="text-emerald-500 shrink-0 mt-0.5" />
                <div>
                  <p className="font-semibold text-slate-800">Move-out recorded</p>
                  <p className="text-sm text-slate-500 mt-1">
                    Your landlord has confirmed that you have physically moved out. Your tenancy has officially ended.
                  </p>
                </div>
              </div>
              <div className="flex items-start gap-3">
                <Clock size={20} className="text-amber-500 shrink-0 mt-0.5" />
                <div>
                  <p className="font-semibold text-slate-800">Deposit settlement pending</p>
                  <p className="text-sm text-slate-500 mt-1">
                    Your landlord is reviewing damages, arrears, and any other deductions before processing your deposit refund via M-Pesa. This may take a few days.
                  </p>
                </div>
              </div>
            </>
          ) : (
            <>
              <div className="flex items-start gap-3">
                <AlertCircle size={20} className="text-amber-500 shrink-0 mt-0.5" />
                <div>
                  <p className="font-semibold text-slate-800">Your tenancy has ended</p>
                  <p className="text-sm text-slate-500 mt-1">
                    Your account is currently in <strong>offboarding mode</strong>. New payments, maintenance requests, and other actions have been disabled.
                  </p>
                </div>
              </div>
              <div className="flex items-start gap-3">
                <Clock size={20} className="text-amber-500 shrink-0 mt-0.5" />
                <div>
                  <p className="font-semibold text-slate-800">Deposit settlement in progress</p>
                  <p className="text-sm text-slate-500 mt-1">
                    Your landlord is currently processing your deposit refund. You will receive your M-Pesa payment once the review is complete.
                  </p>
                </div>
              </div>
            </>
          )}
        </div>

        <p className="mt-8 text-sm text-amber-700">
          Please contact your landlord if you have any questions about your deposit. Thank you for being a tenant here.
        </p>
      </div>
    </div>
  );
}

