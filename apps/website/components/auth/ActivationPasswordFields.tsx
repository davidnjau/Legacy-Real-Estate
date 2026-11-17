'use client';

import { useState } from 'react';
import { Check, Eye, EyeOff, X } from 'lucide-react';

const passwordRequirements = [
  { label: 'At least 12 characters', test: (value: string) => value.length >= 12 },
  { label: 'One lowercase letter', test: (value: string) => /[a-z]/.test(value) },
  { label: 'One uppercase letter', test: (value: string) => /[A-Z]/.test(value) },
  { label: 'One number', test: (value: string) => /\d/.test(value) },
  { label: 'One symbol', test: (value: string) => /[^A-Za-z0-9]/.test(value) },
];

export function isActivationPasswordStrong(value: string) {
  return passwordRequirements.every(({ test }) => test(value));
}

type ActivationPasswordFieldsProps = {
  password: string;
  confirmation: string;
  onPasswordChange: (value: string) => void;
  onConfirmationChange: (value: string) => void;
  disabled?: boolean;
};

export default function ActivationPasswordFields({
  password,
  confirmation,
  onPasswordChange,
  onConfirmationChange,
  disabled = false,
}: ActivationPasswordFieldsProps) {
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmation, setShowConfirmation] = useState(false);
  const strength = passwordRequirements.filter(({ test }) => test(password)).length;

  return (
    <>
      <div>
        <label htmlFor="new-password" className="block text-sm font-semibold">New password</label>
        <div className="relative mt-1.5">
          <input
            id="new-password"
            required
            disabled={disabled}
            type={showPassword ? 'text' : 'password'}
            autoComplete="new-password"
            minLength={12}
            value={password}
            onChange={(event) => onPasswordChange(event.target.value)}
            aria-describedby="password-strength"
            className="w-full rounded-xl border border-slate-300 px-3 py-3 pr-12 disabled:cursor-not-allowed disabled:bg-slate-100"
          />
          <button
            type="button"
            disabled={disabled}
            onClick={() => setShowPassword((visible) => !visible)}
            aria-label={showPassword ? 'Hide new password' : 'Show new password'}
            aria-pressed={showPassword}
            className="absolute inset-y-0 right-0 grid w-12 place-items-center text-slate-500 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
          </button>
        </div>
      </div>
      <div>
        <label htmlFor="confirm-password" className="block text-sm font-semibold">Confirm password</label>
        <div className="relative mt-1.5">
          <input
            id="confirm-password"
            required
            disabled={disabled}
            type={showConfirmation ? 'text' : 'password'}
            autoComplete="new-password"
            minLength={12}
            value={confirmation}
            onChange={(event) => onConfirmationChange(event.target.value)}
            aria-describedby={confirmation ? 'password-match' : undefined}
            className="w-full rounded-xl border border-slate-300 px-3 py-3 pr-12 disabled:cursor-not-allowed disabled:bg-slate-100"
          />
          <button
            type="button"
            disabled={disabled}
            onClick={() => setShowConfirmation((visible) => !visible)}
            aria-label={showConfirmation ? 'Hide confirmation password' : 'Show confirmation password'}
            aria-pressed={showConfirmation}
            className="absolute inset-y-0 right-0 grid w-12 place-items-center text-slate-500 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {showConfirmation ? <EyeOff size={18} /> : <Eye size={18} />}
          </button>
        </div>
        {confirmation && (
          <p id="password-match" className={`mt-1.5 text-xs font-medium ${password === confirmation ? 'text-emerald-700' : 'text-red-700'}`}>
            {password === confirmation ? 'Passwords match.' : 'Passwords do not match.'}
          </p>
        )}
      </div>
      <div id="password-strength" className="mt-1" aria-live="polite">
        <div className="flex items-center justify-between text-xs">
          <span className="font-semibold text-slate-700">Password strength</span>
          <span className={`font-bold ${strength === 5 ? 'text-emerald-700' : strength >= 3 ? 'text-amber-700' : 'text-slate-500'}`}>
            {password.length === 0 ? 'Not set' : strength === 5 ? 'Strong' : strength >= 3 ? 'Getting stronger' : 'Weak'}
          </span>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
          <div
            className={`h-full rounded-full transition-all ${strength === 5 ? 'bg-emerald-600' : strength >= 3 ? 'bg-amber-500' : 'bg-red-400'}`}
            style={{ width: `${strength * 20}%` }}
          />
        </div>
        <ul className="mt-2 grid grid-cols-2 gap-1 text-[11px] text-slate-600">
          {passwordRequirements.map(({ label, test }) => {
            const met = test(password);
            return (
              <li key={label} className={`flex items-center gap-1 ${met ? 'text-emerald-700' : ''}`}>
                {met ? <Check size={12} /> : <X size={12} />}
                {label}
              </li>
            );
          })}
        </ul>
      </div>
    </>
  );
}
