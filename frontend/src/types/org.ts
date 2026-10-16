export type OrgType = 'nonprofit' | 'foundation' | 'unknown';

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

export interface NarrativeEntry {
  taxyear: number | null;
  text: string;
}

export interface OrgNarrativeBundle {
  mission: NarrativeEntry[];
  programs: NarrativeEntry[];
  scheduleO: NarrativeEntry[];
}

export interface ActivitySlot {
  description: string;
  amount: number | null;
}

export interface FoundationActivitiesYear {
  year: number;
  url: string | null;
  charitableActivities: ActivitySlot[];
  programRelatedInvestments: ActivitySlot[];
  otherProgramRelatedInvestmentsTotal: number | null;
  totalProgramRelatedInvestments: number | null;
}

export interface OrgLineage {
  sourceVersion: string | null;
  sourceRunId: string | null;
  builtAt: string | null;
}

// Where the canonical name came from: the latest 990 / 990-EZ / 990-PF on
// file, or the IRS Business Master File for orgs with no return in the
// datamart (990-N filers, churches).
export type NameSource = '990' | '990-EZ' | '990-PF' | 'BMF' | 'grant reports';

export interface OrgProfile {
  ein: string;
  name1: string;
  name2: string | null;
  // Extended identity (from nonprofit_canonical / funder_canonical). Falls back
  // to null on cutover EINs that don't have a canonical row yet.
  dba1: string | null;
  dba2: string | null;
  careOf: string | null;
  address1: string | null;
  address2: string | null;
  city: string | null;
  state: string | null;
  zip: string | null;
  country: string | null;
  website: string | null;
  formationYear: string | null;
  // IRS Business Master File fields (null when the org is not in the BMF,
  // i.e. revoked or terminated). nteeCode is the National Taxonomy of
  // Exempt Entities code; subsection the IRS code ('03' = 501(c)(3), '92' = 4947(a)(1), ...).
  nameSource: NameSource;
  inBmf: boolean;
  irsName: string | null;
  irsSortName: string | null;
  nteeCode: string | null;
  subsection: string | null;
  foundationCode: string | null;
  rulingYear: number | null;
  totalRevenue: number | null;
  // Filing years come from the 990 tables; null for a BMF-only org, which
  // has no return in the datamart.
  firstYear: number | null;
  lastYear: number | null;
  orgType: OrgType;
  revenueByYear: { year: number; revenue: number | null }[];
  revenueDetails: RevenueDetail[];
  // Donor-Advised Fund sponsorship (Form 990 Part IV line 6 / Schedule D Part I).
  // 990-only; foundations get `false` / empty list.
  //   isDafEver  — true if any filing year was Yes (drives header badge +
  //                search filter; hovering the badge highlights the DAF-Yes
  //                years in the funding bar chart so it's clear which years)
  isDafEver: boolean;
  dafByYear: { year: number; isDaf: boolean }[];
  narrative: OrgNarrativeBundle;
  foundationActivities: FoundationActivitiesYear[];
  lineage: OrgLineage;
}

export interface SearchResponse {
  results: OrgResult[];
  total: number;
  page: number;
  limit: number;
}
