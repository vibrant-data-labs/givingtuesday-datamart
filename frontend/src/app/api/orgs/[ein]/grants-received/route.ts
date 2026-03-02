export const revalidate = 3600;

import { NextRequest, NextResponse } from 'next/server';
import { getGrantsReceived } from '@/lib/queries/grants';
import {
  sanitizeEIN,
  sanitizePage,
  sanitizeLimit,
  sanitizeSearchQuery,
  sanitizeSortColumn,
  sanitizeSortOrder,
  sanitizeAmount,
  sanitizeYear,
} from '@/lib/utils/validation';

const ALLOWED_SORT_COLS = ['name', 'amount', 'year'] as const;

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

  try {
    const { grants, total } = await getGrantsReceived(ein, page, limit, {
      name,
      purpose,
      year,
      minAmount,
      maxAmount,
      sortCol,
      sortOrder,
    });
    return NextResponse.json(
      { grants, total, page, limit },
      { headers: { 'Cache-Control': 's-maxage=3600, stale-while-revalidate=86400' } }
    );
  } catch (err) {
    console.error('Grants received error:', err);
    return NextResponse.json({ error: 'Failed to load grants' }, { status: 500 });
  }
}
