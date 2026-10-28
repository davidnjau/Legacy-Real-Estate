const TEST_TIME_ZONE_OFFSET_HOURS = 3;

function getNairobiDateParts(date: Date) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Nairobi',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date);

  return Object.fromEntries(parts.map((part) => [part.type, part.value]));
}

export function getInvoiceReleaseAt(billingMonth: string): string {
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(billingMonth)) {
    throw new Error('A valid billing month in YYYY-MM format is required.');
  }
  if (process.env.BILLING_TEST_MODE !== 'true') {
    const [year, month] = billingMonth.split('-').map(Number);
    const nairobiMonthStartUtc = Date.UTC(year, month - 1, 1) - 3 * 60 * 60 * 1000;
    return new Date(nairobiMonthStartUtc).toISOString();
  }

  const now = new Date();
  const dateParts = getNairobiDateParts(now);
  const releaseDate = process.env.BILLING_TEST_RELEASE_DATE === 'today'
    ? `${dateParts.year}-${dateParts.month}-${dateParts.day}`
    : process.env.BILLING_TEST_RELEASE_DATE || `${dateParts.year}-${dateParts.month}-${dateParts.day}`;
  const [hours, minutes] = (process.env.BILLING_TEST_RELEASE_TIME || '15:00').split(':').map(Number);
  const utcDate = new Date(`${releaseDate}T00:00:00.000Z`);
  utcDate.setUTCHours(hours - TEST_TIME_ZONE_OFFSET_HOURS, minutes, 0, 0);
  return utcDate.toISOString();
}

export function getInvoiceReviewAt(): string | null {
  if (process.env.BILLING_TEST_MODE !== 'true') return null;

  const now = new Date();
  const dateParts = getNairobiDateParts(now);
  const reviewDate = process.env.BILLING_TEST_RELEASE_DATE === 'today'
    ? `${dateParts.year}-${dateParts.month}-${dateParts.day}`
    : process.env.BILLING_TEST_RELEASE_DATE || `${dateParts.year}-${dateParts.month}-${dateParts.day}`;
  const [hours, minutes] = (process.env.BILLING_TEST_REVIEW_TIME || '14:40').split(':').map(Number);
  const utcDate = new Date(`${reviewDate}T00:00:00.000Z`);
  utcDate.setUTCHours(hours - TEST_TIME_ZONE_OFFSET_HOURS, minutes, 0, 0);
  return utcDate.toISOString();
}

export function isWaterReminderDue(date = new Date()): boolean {
  if (process.env.BILLING_TEST_MODE === 'true') {
    const [hours, minutes] = (process.env.BILLING_TEST_REVIEW_TIME || '09:30').split(':').map(Number);
    const nowParts = new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Africa/Nairobi',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).formatToParts(date);
    const currentHour = Number(nowParts.find((part) => part.type === 'hour')?.value || 0);
    const currentMinute = Number(nowParts.find((part) => part.type === 'minute')?.value || 0);
    return (currentHour * 60 + currentMinute) >= (hours * 60 + minutes);
  }

  const NairobiParts = getNairobiDateParts(date);
  const currentDay = Number(NairobiParts.day);
  const lastDay = new Date(Number(NairobiParts.year), Number(NairobiParts.month), 0).getDate();
  const reminderDay = Number(NairobiParts.month) === 2 ? lastDay - 3 : 27;
  return currentDay >= reminderDay;
}