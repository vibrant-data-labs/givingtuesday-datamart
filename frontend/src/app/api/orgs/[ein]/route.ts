export const revalidate = 3600;

import { NextRequest, NextResponse } from 'next/server';
import { getOrgProfile } from '@/lib/queries/orgs';
import { sanitizeEIN } from '@/lib/utils/validation';

export async function GET(
  _request: NextRequest,
  { params }: { params: { ein: string } }
) {
  const ein = sanitizeEIN(params.ein);
  if (!ein) {
    return NextResponse.json({ error: 'Invalid EIN' }, { status: 400 });
  }

  try {
    const org = await getOrgProfile(ein);
    if (!org) {
      return NextResponse.json({ error: 'Organization not found' }, { status: 404 });
    }
    return NextResponse.json(
      org,
      { headers: { 'Cache-Control': 's-maxage=3600, stale-while-revalidate=86400' } }
    );
  } catch (err) {
    console.error('Org profile error:', err);
    return NextResponse.json({ error: 'Failed to load organization' }, { status: 500 });
  }
}
