export const revalidate = 3600;

import { NextRequest, NextResponse } from 'next/server';
import { searchOrgs } from '@/lib/queries/search';
import {
  sanitizeSearchQuery,
  sanitizePage,
  sanitizeLimit,
  sanitizeOrgType,
} from '@/lib/utils/validation';

export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const q = sanitizeSearchQuery(searchParams.get('q') ?? '');
  const type = sanitizeOrgType(searchParams.get('type'));
  const page = sanitizePage(searchParams.get('page'));
  const limit = sanitizeLimit(searchParams.get('limit'), 50);

  if (!q) {
    return NextResponse.json({ results: [], total: 0, page: 1, limit });
  }

  try {
    const { results, total } = await searchOrgs(q, type, page, limit);
    return NextResponse.json(
      { results, total, page, limit },
      { headers: { 'Cache-Control': 's-maxage=3600, stale-while-revalidate=86400' } }
    );
  } catch (err) {
    console.error('Search error:', err);
    return NextResponse.json({ error: 'Search failed' }, { status: 500 });
  }
}
