---
name: feedback-gt-datamarts-first
description: When data seems missing from the loaded tables, check GivingTuesday's published datamart catalog for a file we never ingested before proposing any other source (per-filing XML, one-off extracts)
metadata:
  type: feedback
---

Before saying "the loaded tables do not hold X" and proposing another source,
list GivingTuesday's catalog (`sources.resolver.list_bucket(S3_BUCKET,
S3_PREFIX)`, bucket `gt990datalake-analytics-and-datamarts`, prefix
`EfileDataMarts/`, 157 files) and check for a datamart we have not registered
in `sources/registry.py`. Advise loading that datamart as a source.

**Why:** on 2026-09-29 I told Zein future-payment lists could not load because
we hold no future total, and proposed reading it from each filing's XML. He
corrected me: "No, this is wrong. You should instead advise to load in the
GivingTuesday future grants datamart that we have left out." It exists:
`2026_06_16_All_Years_990PFPart14Grants3B.csv`, 172 MB, same version as the
paid one (`...Grants3A.csv`, 5.9 GB), columns `SIGOCAFF*` (20 columns; no
person-name column). We ingest only 3A. A published datamart keeps one source
version and one lineage; per-filing XML is a second pipeline.

**How to apply:** the registry holds 11 sources; GT publishes many more 990-PF
parts (managers, contractors, related orgs, states registered). "Use what we
have first" includes GT's catalog. Related:
[[placeholder-session5-work-list-loader]].
