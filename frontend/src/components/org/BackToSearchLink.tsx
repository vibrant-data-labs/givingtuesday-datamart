'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

export function BackToSearchLink() {
  const [href, setHref] = useState('/');

  useEffect(() => {
    const saved = sessionStorage.getItem('lastSearchUrl');
    if (saved) setHref(saved);
  }, []);

  return (
    <Link
      href={href}
      className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
    >
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
      </svg>
      Back to Search
    </Link>
  );
}
