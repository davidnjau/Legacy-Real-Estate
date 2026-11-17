import { redirect } from 'next/navigation';

export default function LegacyMigrationRedirect() {
  redirect('/property-officer/imports');
}
