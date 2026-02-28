import Link from 'next/link';
import Image from 'next/image';
import icon from '@/app/icon.png';

export function Header() {
  return (
    <header className="sticky top-0 z-40 bg-white/90 backdrop-blur-sm border-b border-zinc-200">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-20">
          <Link href="/" className="flex items-center gap-3 group">
            <Image
              src={icon}
              alt=""
              width={64}
              height={64}
              className="w-16 h-16 object-contain"
            />
            <span className="font-semibold text-zinc-900 text-xl group-hover:text-indigo-600 transition-colors">
              990 Explorer
            </span>
          </Link>
          <nav className="flex items-center gap-4">
            <Link href="/?type=nonprofit" className="text-sm text-zinc-500 hover:text-zinc-900 transition-colors">
              Nonprofits
            </Link>
            <Link href="/?type=foundation" className="text-sm text-zinc-500 hover:text-zinc-900 transition-colors">
              Foundations
            </Link>
          </nav>
        </div>
      </div>
    </header>
  );
}
