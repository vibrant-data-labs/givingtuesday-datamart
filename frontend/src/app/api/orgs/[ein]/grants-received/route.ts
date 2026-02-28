export const revalidate = 3600;

import { NextRequest, NextResponse } from 'next/server';
import { getGrantsReceived } from '@/lib/queries/grants';
import { sanitizeEIN, sanitizePage, sanitizeLimit } from '@/lib/utils/validation';

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
  const limit = sanitizeLimit(searchParams.get('limit'), 50);

  try {
    const { grants, total } = await getGrantsReceived(ein, page, limit);
    return NextResponse.json(
      { grants, total, page, limit },
      { headers: { 'Cache-Control': 's-maxage=3600, stale-while-revalidate=86400' } }
    );
  } catch (err) {
    console.error('Grants received error:', err);
    return NextResponse.json({ error: 'Failed to load grants' }, { status: 500 });
  }
}
