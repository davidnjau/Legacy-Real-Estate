// app/reset-password/page.tsx
import { Suspense } from 'react';
import { Building2 } from 'lucide-react';
import ResetPasswordForm from './form';

function ResetPasswordPageContent() {
  return <ResetPasswordForm />;
}

export default function ResetPasswordPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4 py-12">
          <div className="max-w-md w-full bg-white rounded-2xl shadow-xl border border-slate-100 p-8 text-center">
            <Building2 className="w-12 h-12 text-emerald-400 mx-auto mb-4" />
            <h1 className="text-2xl font-bold text-slate-900">Loading...</h1>
          </div>
        </div>
      }
    >
      <ResetPasswordPageContent />
    </Suspense>
  );
}
