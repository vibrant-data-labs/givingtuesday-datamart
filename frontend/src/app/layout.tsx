import type { Metadata } from "next";
import localFont from "next/font/local";
import { Header } from "@/components/layout/Header";
import "./globals.css";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-geist-sans",
  weight: "100 900",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

export const metadata: Metadata = {
  title: "990 Explorer — IRS Filing Data",
  description: "Search and explore IRS Form 990 data for nonprofits and private foundations.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        <Header />
        <main className="min-h-screen">
          {children}
        </main>
        <footer className="border-t border-zinc-200 mt-16">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
            <p className="text-xs text-zinc-400 text-center">
              IRS 990 data sourced from public filings. Data may be delayed or incomplete.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
