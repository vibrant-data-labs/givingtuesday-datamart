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

export interface RevenueDetail {
  year: number;
  url: string | null;
  totalRevenue: number | null;
  totalContributions: number | null;
  // 990-only Part 8 Line 1 breakdown (null for foundations)
  federatedCampaigns: number | null;
  membershipDues: number | null;
  fundraisingEvents: number | null;
  relatedOrganizations: number | null;
  governmentGrants: number | null;
  allOtherContributions: number | null;
  nonCashContributions: number | null;
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
  revenueDetails: RevenueDetail[];
}

export interface SearchResponse {
  results: OrgResult[];
  total: number;
  page: number;
  limit: number;
}
