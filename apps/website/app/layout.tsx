// app/layout.tsx
import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Property Operations | Multi-agency platform',
  description:
    'A Kenya-focused, multi-agency property operations platform, being delivered in phases.',
  openGraph: {
    title: 'Property Operations | Multi-agency platform',
    description:
      'A Kenya-focused, multi-agency property operations platform, being delivered in phases.',
    type: 'website',
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="font-sans bg-slate-50 text-slate-900 antialiased">
        {children}
      </body>
    </html>
  );
}