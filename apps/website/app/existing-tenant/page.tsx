import Link from 'next/link';
import { Building2, KeyRound, Mail, ArrowRight } from 'lucide-react';

export default function ExistingTenantPage() {
  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
      <div className="max-w-md w-full bg-white rounded-2xl shadow-xl border border-slate-100 overflow-hidden">
        <div className="bg-slate-900 p-8 text-center">
          <Building2 className="w-12 h-12 text-emerald-400 mx-auto mb-4" />
          <h1 className="text-2xl font-bold text-white">Existing Tenant Activation</h1>
          <p className="text-slate-400 mt-2 text-sm">
            Your landlord has already created your apartment account.
          </p>
        </div>

        <div className="p-8 space-y-4">
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
            <div className="flex gap-3">
              <KeyRound className="text-amber-600 shrink-0" size={20} />
              <div>
                <h2 className="font-bold text-amber-900">Have a claim code?</h2>
                <p className="text-sm text-amber-800 mt-1">
                  Enter your unit number and 48-hour code, then create your password.
                </p>
              </div>
            </div>
            <Link href="/activate" className="mt-4 w-full inline-flex items-center justify-center gap-2 bg-amber-600 hover:bg-amber-700 text-white font-bold py-2.5 rounded-lg">
              Activate with Claim Code <ArrowRight size={18} />
            </Link>
          </div>

          <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
            <div className="flex gap-3">
              <Mail className="text-emerald-600 shrink-0" size={20} />
              <div>
                <h2 className="font-bold text-emerald-900">Received an email?</h2>
                <p className="text-sm text-emerald-800 mt-1">
                  Open the activation link in your email to create your password directly.
                </p>
              </div>
            </div>
          </div>

          <p className="text-center text-sm text-slate-600">
            New tenant with a landlord-provided setup code?{' '}
            <Link href="/register" className="font-bold text-emerald-600 hover:text-emerald-500">Register here</Link>
          </p>
        </div>
      </div>
    </div>
  );
}