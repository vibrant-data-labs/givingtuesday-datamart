import type { Metadata } from "next";
import { Sora, Plus_Jakarta_Sans } from "next/font/google";
import localFont from "next/font/local";
import { Header } from "@/components/layout/Header";
import { Providers } from "./providers";
import "./globals.css";

const display = Sora({
  subsets: ["latin"],
  variable: "--font-serif",
  display: "swap",
});

const sans = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const mono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-mono",
  weight: "100 900",
});

export const metadata: Metadata = {
  title: "peerlo",
  description:
    "Search millions of IRS Form 990 filings by name, EIN, or mission, and explore the grants that connect US nonprofits and foundations.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${display.variable} ${sans.variable} ${mono.variable} font-sans`}>
        <Providers>
          <Header />
          <main className="min-h-screen">
            {children}
          </main>
          <footer className="border-t border-border mt-16">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-2">
              <p className="text-xs text-muted-foreground text-center tracking-wide">
                IRS 990 data sourced from public filings via{' '}
                <a
                  href="https://nonprofitecosystem.givingtuesday.org/datamarts/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-foreground/80 hover:text-primary transition-colors underline-offset-2 hover:underline"
                >
                  GivingTuesday&rsquo;s Data Commons
                </a>
                . Data may be delayed or incomplete.
              </p>
            </div>
          </footer>
        </Providers>
      </body>
    </html>
  );
}
