export function normalizeKenyanPhoneForStorage(value: string): string | null {
  const digits = String(value || '').replace(/\D/g, '');
  if (/^254[17]\d{8}$/.test(digits)) return digits;
  if (/^[17]\d{8}$/.test(digits)) return `0${digits}`;
  if (/^0[17]\d{8}$/.test(digits)) return digits;
  return null;
}
