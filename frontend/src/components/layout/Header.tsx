import Link from 'next/link';

export function Header() {
  return (
    <header className="sticky top-0 z-40 bg-card/90 backdrop-blur-md border-b border-border">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          <Link href="/" className="flex items-center gap-2.5 group">
            <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center">
              <span className="text-primary-foreground font-serif text-lg leading-none">9</span>
            </div>
            <span className="font-serif text-xl text-foreground group-hover:text-primary transition-colors">
              990 Explorer
            </span>
          </Link>
          <nav className="flex items-center gap-6">
            <Link href="/?type=nonprofit" className="text-sm text-muted-foreground hover:text-foreground transition-colors">
              Nonprofits
            </Link>
            <Link href="/?type=foundation" className="text-sm text-muted-foreground hover:text-foreground transition-colors">
              Foundations
            </Link>
          </nav>
        </div>
      </div>
    </header>
  );
}
