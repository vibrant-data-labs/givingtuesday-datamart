export type OrgType = 'nonprofit' | 'foundation';

export interface OrgResult {
  ein: string;
  name1: string;
  name2: string | null;
  city: string | null;
  state: string | null;
  firstYear: number;
  lastYear: number;
  orgType: OrgType;
}

export interface OrgProfile {
  ein: string;
  name1: string;
  name2: string | null;
  address1: string | null;
  address2: string | null;
  city: string | null;
  state: string | null;
  zip: string | null;
  totalRevenue: number | null;
  firstYear: number;
  lastYear: number;
  orgType: OrgType;
  revenueByYear: { year: number; revenue: number | null }[];
}

export interface SearchResponse {
  results: OrgResult[];
  total: number;
  page: number;
  limit: number;
}
