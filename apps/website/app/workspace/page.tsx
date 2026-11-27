'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowRight, Building2 } from 'lucide-react';
import { getDjangoWorkspaceOptions } from '../../lib/django-routing';

type Account = {
  name: string;
  roles: string[];
  staffStepUpRequired: boolean;
  staffStepUpRoles: string[];
};

export default function WorkspaceSelectionPage() {
  const router = useRouter();
  const [account, setAccount] = useState<Account | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    void fetch('/api/auth/me', { cache: 'no-store' })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok || !payload.user) {
          throw new Error(payload.detail || 'Sign in to choose a workspace.');
        }
        if (active) {
          setAccount({
            name: payload.user.name,
            roles: payload.user.roles ?? [],
            staffStepUpRequired: payload.user.staff_step_up_required ?? false,
            staffStepUpRoles: payload.user.staff_step_up_roles ?? [],
          });
        }
      })
      .catch((cause: unknown) => {
        if (!active) return;
        setError(cause instanceof Error ? cause.message : 'Unable to load your account.');
      });
    return () => {
      active = false;
    };
  }, []);

  const workspaces = useMemo(
    () => account ? getDjangoWorkspaceOptions(account.roles) : [],
    [account],
  );

  useEffect(() => {
    if (!account) return;
    if (workspaces.length === 0) {
      setError('Your account does not have an assigned portal role yet. Contact your administrator.');
    } else if (workspaces.length === 1 && !account.staffStepUpRequired) {
      router.replace(workspaces[0].path);
    }
  }, [account, router, workspaces]);

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#f4f7f5] px-4 py-10 text-slate-900">
      <section className="w-full max-w-2xl rounded-3xl border border-slate-200 bg-white p-6 shadow-sm sm:p-9">
        <div className="flex items-center gap-3">
          <span className="grid h-11 w-11 place-items-center rounded-2xl bg-[#123b2c] text-emerald-200">
            <Building2 size={22} />
          </span>
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-800">Legacy Real Estate</p>
            <h1 className="mt-1 text-2xl font-bold">Choose a workspace</h1>
          </div>
        </div>
        <p className="mt-4 text-sm text-slate-600">
          {account ? `${account.name}, choose which part of your account to open.` : 'Loading your account…'}
        </p>
        {account?.staffStepUpRequired && (
          <div className="mt-5 rounded-2xl border border-amber-200 bg-amber-50 p-4">
            <h2 className="text-sm font-bold text-amber-950">Staff sign-in required</h2>
            <p className="mt-1 text-sm text-amber-900">
              {account.staffStepUpRoles.join(', ')} access needs a fresh staff sign-in.
              Finance roles will also complete the required authenticator check.
            </p>
            <button
              type="button"
              onClick={() => router.replace('/login')}
              className="mt-3 rounded-xl bg-[#123b2c] px-4 py-2.5 text-sm font-bold text-white hover:bg-[#0e3024]"
            >
              Sign in to a staff workspace
            </button>
          </div>
        )}
        {error && <p role="alert" className="mt-5 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}
        <div className="mt-6 grid gap-3 sm:grid-cols-2">
          {workspaces.map((workspace) => (
            <button
              key={workspace.path}
              type="button"
              onClick={() => router.replace(workspace.path)}
              className="flex min-h-24 items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white p-4 text-left transition hover:border-emerald-700 hover:bg-emerald-50"
            >
              <span>
                <span className="block font-semibold">{workspace.label}</span>
                <span className="mt-1 block text-xs text-slate-500">Open this role's dashboard</span>
              </span>
              <ArrowRight size={18} className="shrink-0 text-emerald-800" />
            </button>
          ))}
        </div>
      </section>
    </main>
  );
}
