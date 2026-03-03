export interface GrantRow {
  granterEin: string;
  granterName: string | null;
  granterName2: string | null;
  taxyear: number;
  taxperbegin: string | null;
  taxperend: string | null;
  granteeEin: string | null;
  granteePersonName: string | null;
  granteeOrgName1: string | null;
  granteeOrgName2: string | null;
  granteeAddress1: string | null;
  granteeCity: string | null;
  granteeState: string | null;
  granteeZip: string | null;
  grantAmount: number | null;
  grantPurpose: string | null;
  grantStatus: string | null;
  grantRelationship: string | null;
}

export interface GrantsAggregates {
  totalCount: number;
  totalAmount: number;
  avgAmount: number;
}

export interface GrantGroupRow {
  groupKey: string;
  groupKeyEin: string | null;
  grantCount: number;
  totalAmount: number;
  avgAmount: number;
}

export interface GrantsResponse {
  grants: GrantRow[];
  total: number;
  page: number;
  limit: number;
  aggregates: GrantsAggregates;
}

export interface GrantsGroupResponse {
  groups: GrantGroupRow[];
  total: number;
  page: number;
  limit: number;
  aggregates: GrantsAggregates;
}
