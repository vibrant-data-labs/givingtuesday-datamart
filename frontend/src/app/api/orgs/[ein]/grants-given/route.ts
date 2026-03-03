export const revalidate = 3600;

import { NextRequest, NextResponse } from 'next/server';
import { getGrantsGiven, getGrantsGivenGrouped } from '@/lib/queries/grants';
import {
  sanitizeEIN,
  sanitizePage,
  sanitizeLimit,
  sanitizeSearchQuery,
  sanitizeSortColumn,
  sanitizeSortOrder,
  sanitizeAmount,
  sanitizeYear,
  sanitizeGroupBy,
} from '@/lib/utils/validation';

const ALLOWED_SORT_COLS = ['name', 'amount', 'year'] as const;
const CACHE_HEADERS = { 'Cache-Control': 's-maxage=3600, stale-while-revalidate=86400' };

export async function GET(
  request: NextRequest,
  { params }: { params: { ein: string } }
) {
  const ein = sanitizeEIN(params.ein);
  if (!ein) {
    return NextResponse.json({ error: 'Invalid EIN' }, { status: 400 });
  }

  const { searchParams } = request.nextUrl;
  const page = sanitizePage(searchParams.get('page'));
  const limit = sanitizeLimit(searchParams.get('limit'), 100);

  const rawName = searchParams.get('q') ?? '';
  const rawPurpose = searchParams.get('purpose') ?? '';
  const name = rawName ? sanitizeSearchQuery(rawName) : undefined;
  const purpose = rawPurpose ? sanitizeSearchQuery(rawPurpose) : undefined;
  const year = sanitizeYear(searchParams.get('year'));
  const minAmount = sanitizeAmount(searchParams.get('minAmount'));
  const maxAmount = sanitizeAmount(searchParams.get('maxAmount'));
  const sortCol = sanitizeSortColumn(searchParams.get('sort'), [...ALLOWED_SORT_COLS]);
  const sortOrder = sanitizeSortOrder(searchParams.get('order'));
  const groupBy = sanitizeGroupBy(searchParams.get('groupBy'));

  const rawEntityEin = searchParams.get('entityEin') ?? '';
  const entityEin = rawEntityEin ? sanitizeEIN(rawEntityEin) : undefined;

  const filter = { name, purpose, year, minAmount, maxAmount, entityEin, sortCol, sortOrder };

  try {
    if (groupBy) {
      const { groups, total, aggregates } = await getGrantsGivenGrouped(ein, page, limit, groupBy, filter);
      return NextResponse.json({ groups, total, page, limit, aggregates }, { headers: CACHE_HEADERS });
    }

    const { grants, total, aggregates } = await getGrantsGiven(ein, page, limit, filter);
    return NextResponse.json({ grants, total, page, limit, aggregates }, { headers: CACHE_HEADERS });
  } catch (err) {
    console.error('Grants given error:', err);
    return NextResponse.json({ error: 'Failed to load grants' }, { status: 500 });
  }
}
