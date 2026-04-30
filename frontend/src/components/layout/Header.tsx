import Link from 'next/link';
import { LogoMark } from './Logo';

export function Header() {
  return (
    <header className="sticky top-0 z-40 bg-card/90 backdrop-blur-md border-b border-border">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          <Link href="/" className="flex items-center gap-2.5 group">
            <LogoMark className="w-9 h-5 text-primary group-hover:text-primary/80 transition-colors" />
            <span className="font-serif text-2xl text-foreground group-hover:text-primary transition-colors tracking-tight">
              peerlo
            </span>
          </Link>
          <nav className="flex items-center gap-6">
            <Link href="/about" className="text-sm text-muted-foreground hover:text-foreground transition-colors">
              About
            </Link>
          </nav>
        </div>
      </div>
    </header>
  );
}
