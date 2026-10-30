# The `_current` relations' stamp: one rule for staleness

*October 2026. A design, not built yet: the rule that would replace the two
ways the pipeline decides today whether a `_current` relation must be
rebuilt, what each writer and reader would do, the migration, the cost and
the decisions it needs. The code it would replace:
`current_grants.stale_basic_fields_current`, the matcher's stamp
(`grant_matching._current_stamp`, `_unstamped_current_relations`) and the
run-id check of the canonical build's identity transaction (PR #88).*

## The problem

Two parts of the pipeline decide whether a `_current` relation is stale,
and they decide it differently.

- **The canonical build scans.** `stale_basic_fields_current` compares the
  largest `_ingest_run_id` in each basic-fields staging table with the
  largest in its relation. The column has no index, so the check reads
  every row of three staging tables and three relations (the relations
  alone hold about 8 GB with their indexes), on every canonical build.
- **The matcher stamps.** A matcher run writes on each relation it builds
  a JSON comment naming the source versions and the input shape, and
  `--skip-rebuild` compares it with the latest successful ingest runs: a
  catalog read. It covers only the relations the matcher built; one
  rebuilt by a refresh carries no stamp and is rebuilt again.

Between them they leave four gaps:

1. **A failed ingest gets rebuilt from.** When validation fails, the
   staging table keeps the failed run's rows (`ingestion.ingest_source`:
   "the staging table stays as-is; consumers filter by status='success'").
   The `_current` builders do not filter: they read every row. The scan
   sees the failed run's id in staging and calls the relation stale, so
   the next canonical build rebuilds it from the failed drop, and so does
   every full matcher run.
2. **A load in progress is visible.** A refresh commits the new, empty
   staging table first and then each batch of rows
   (`write_data_to_sql._create_table_from_columns` and the COPY loop). A
   build that runs meanwhile reads a partial table, and the scan, seeing
   the new run id, asks for exactly that build.
3. **The own-extract patch is invisible to the matcher.** The matcher's
   stamp and its checkpoint prefix are keyed on the versions of GT's
   release (`_source_versions`). The patch replaces rows of
   `privategrants`, `basic_fields` and `basic_fields_pf` under the same
   release and records its runs under another logical name
   (`irs_extract_patch`). After a re-patch on the same release, with the
   corrections, the recovered grants and the shape unchanged, a run
   resolves the same prefix: `--skip-rebuild` takes the pre-patch
   relations as current, and the chunk loop resumes pre-patch chunks
   against shifted rows. Whether it has happened would show in the
   prefixes the runs logged; I have not checked them.
4. **The scan's maximum is fragile on patched tables.** A patched staging
   table holds GT's run id and one run id per batch load. The relation
   keeps one row per filer-year, so its largest run id equals staging's
   only if it keeps a row of the run whose id sorts last. It normally
   does; when it does not, every canonical build rebuilds the relation.

## The design

Every table a `_current` relation reads, and every `_current` relation,
carries a stamp: a JSON table comment written in the transaction that
writes the table. A relation's stamp names the stamps of what it was built
from. Staleness becomes a comparison of comments: no scan, and no stamp
that disagrees with its table, because Postgres drops a comment with its
table and a new table has none.

```
ingest run R (success)  writes  public.basic_fields_pf       {"run_id": R, "patch_run_id": null}
own_extract patch P     writes  public.basic_fields_pf       {"run_id": R, "patch_run_id": P}
build B   reads those stamps under ACCESS SHARE, builds, then writes
                                public.privategrants_current {"inputs": {"basic_fields_pf": {R, P},
                                                                         "privategrants": {...}},
                                                              "ddl": "3f9a0c...", "build_id": B}
build B2  writes                public.privategrants_future_current
                                                             {"inputs": {..., "privategrants_current": B}, ...}
```

### Staging stamps

- `sources refresh` writes `{"stamp": 1, "run_id", "source_version",
  "patch_run_id": null}` on the staging table, in the transaction that
  marks the ingest run `success` (`_finalize_run`). A failed or
  interrupted ingest leaves its new table without a stamp. A failure
  before the old table is dropped leaves the old stamp, which still
  describes the old rows.
- `own_extract patch` refuses a table without a stamp (its last ingest did
  not finish), and sets `patch_run_id` to its own run on each table it
  patches, in the transaction that patches it.
- `own_extract gt-release`, on a scratch host, stamps each table it loads
  with its own run id and the release.

Every ingested table gets a stamp, not only the builders' inputs, so the
canonical build can also check the staging tables it reads directly
(Schedule O, missions, programs, the BMF).

### Relation stamps

`build_current_relations` writes the stamp itself, for every caller, last
in each relation's transaction:

- `inputs`: the stamp of each table the relation is built from, read at
  the start of the transaction under `LOCK TABLE ... IN ACCESS SHARE
  MODE`, so no ingest can replace an input until the build commits. For a
  relation input (the future relation reads the paid one) the entry is
  that relation's `build_id`.
- `ddl`: a hash of the relation's DDL and indexes. A change to the dedup
  rules makes every relation built under the old ones stale, with no
  number to remember to bump.
- `build_id` and `built_by` (the command), for lineage.

The inputs are declared beside the DDL (`_INPUTS`), and a test holds the
declaration to the tables each DDL reads:

| relation | inputs |
|---|---|
| `basic_fields_current` | `basic_fields` |
| `basic_fields_pf_current` | `basic_fields_pf` |
| `basic_fields_ez_current` | `basic_fields_ez` |
| `grants_to_domestic_organizations_current` | `grants_to_domestic_organizations`, `basic_fields` |
| `privategrants_current` | `privategrants`, `basic_fields_pf` |
| `privategrants_future_current` | `privategrants_future`, `basic_fields_pf`, `privategrants_current` |

A build whose staging input has no stamp does not run: it raises, naming
the input and the reasons it can have none. `--allow-unstamped-inputs` on
the command line (and a parameter of the function) builds anyway, for a
scratch host or an emergency, and records the input as `null`, so the
relation is never taken as current.

### The rule

`relation_states(connection, tables)` reads comments only and gives each
relation one of three states, in build order:

| state | when | what a caller does |
|---|---|---|
| current | its stamp's `ddl` is the DDL's hash, and its `inputs` are its inputs' stamps now | nothing |
| stale | otherwise: never stamped, missing, built from other inputs or rules, or reading a relation that is stale | rebuild it |
| blocked | one of its staging inputs has no stamp | refuse, naming the input |

`with_dependents` and `_READS_CURRENT` follow from `_INPUTS`: a rebuilt
paid relation has a new `build_id`, so the future relation reads as stale.

### Readers

- **The canonical build** replaces the scan with `relation_states` on the
  basic-fields relations: a blocked one stops the build, stale ones are
  rebuilt one transaction each, as PR #88 does now. The identity
  transaction locks the tables it reads and compares their stamps with
  the ones it started from. That replaces the run-id comparison #88
  added, which reads `ingest_runs` and so cannot see a load still in
  progress (its run is not a success yet); an unstamped table can. The
  build row records the stamps in `source_runs`, so the canonical tables
  name the relation builds they read.
- **The matcher** takes its states from the same function:
  `--skip-rebuild` rebuilds the stale relations, and a blocked input stops
  the run before any build, with or without the flag. The reads
  transaction compares the stamps of everything it reads with the ones
  the run started from: the relations, and the staging tables the
  universe views read (`basic_fields`, `basic_fields_pf`, `eo_bmf`).
  `_current_stamp`, `_stamp_of`, `_unstamped_current_relations` and the
  versions comparison go.
- **`current_grants --check`** prints each relation's state and the
  reason, without building: the cheap status the runbooks lack today.

### The matcher's checkpoint prefix

The prefix gains a level, `in_<hash>`: a hash of the stamps of the
matcher's inputs, which are the paid relation's stamp (naming its own
staging inputs) and the staging stamps of `basic_fields`,
`basic_fields_pf` and `eo_bmf`. A patch, or any reload, changes it. The
version levels stay, for `aws s3 ls` and for deleting one release's
chunks. The first run after the change recomputes from scratch, once.

## Migration

Staging tables loaded before the change carry no stamp, so every build
would be blocked. `current_grants --adopt` stamps them from
`datamart_meta.ingest_runs`: each table's latest run, when it is a success
with no later failed or unfinished run of the same table, plus the patch
run after it if there is one. It checks a sample of each table's rows
against those run ids and stops on a mismatch; `--adopt --verify` reads
every row instead, by night. A table it cannot adopt stays unstamped until
it is reloaded.

Relations: the next matcher run rebuilds the grants relations anyway. For
the basic-fields relations, `--adopt` runs today's scan check once per
relation, by night, and stamps the ones that pass; the canonical build
rebuilds the rest (11 to 13 minutes each on the medium instance class).

## Cost

- Steady state: about a dozen catalog reads per check, where the canonical
  build now reads six tables in full on every build.
- Once: the adoption's samples (seconds), or its full verification (the
  same reads as one scan check, by night).
- No API cost and no new dependency; a stamp is a few hundred bytes.

## Risks and limits

- **A writer that does not stamp blocks the builds.** The writers of the
  builders' inputs are `ingest_source`, the patch and the scratch-host
  release. A new writer that forgets to stamp fails loudly, at the next
  build, with the input named, never silently.
- **Edits in place are invisible.** An UPDATE by hand on a staging table
  keeps its stamp, as it keeps its run ids today. Such an edit must be
  followed by `--adopt` of that table, or a reload.
- **A stamp needs the table's owner.** The pipeline's role creates and owns
  every staging table and relation, so `COMMENT ON` is allowed. A table
  created by another role would block.
- **The rule trusts the comment.** Nothing stops a comment written by hand.
  The stamp guards against the pipeline's own ordering mistakes, not
  against tampering.

## Decisions

Each with the choice I recommend first.

1. **Where stamps live:** table comments, as the matcher's are now (they
   commit with the data and die with the table), or a
   `datamart_meta.table_stamps` table (easier to query, but it can outlive
   or disagree with the table it describes).
2. **An unstamped input:** refuse to build or patch, with the override
   flag, or warn and build as today.
3. **Freshness of the rules:** a hash of the DDL, or the hand-bumped
   `MATCHING_INPUT_SHAPE_VERSION`. The prefix keeps the shape version
   either way: it also covers the views and the name normalization.
4. **The prefix:** add `in_<hash>` now, at the cost of one recompute, or
   leave the patch blind spot to a later change.
5. **Adoption:** stamp the staging tables from `ingest_runs` with a sample
   check and adopt the basic-fields relations by one scan each, or adopt
   only the staging tables and rebuild every relation once.

## Plan

Three PRs, each tested offline on the faked-session harness of
`tests/test_current_stamp.py`. Nothing runs against the shared instance
until the adoption, which runs by night, on an explicit go.

1. **Write the stamps.** Staging stamps in the ingest, the patch and the
   release; relation stamps in `build_current_relations` (`_INPUTS`, the
   DDL hash, the locks); `relation_states`; `--check` and `--adopt`.
   Nothing reads the new stamps yet. The matcher passes its own keys to
   the builder, which writes one comment, so `--skip-rebuild` works as
   before.
2. **Read them.** The canonical build, the matcher's `--skip-rebuild`, its
   reads check and its prefix move to `relation_states`; builds and the
   patch refuse unstamped inputs.
3. **Remove the rest.** `stale_basic_fields_current`, the matcher's stamp
   functions and `_READS_CURRENT`; the README's matcher and runbook
   sections.

The tests that matter most, beside each PR's own:

- A failed validation leaves the table blocked, and no builder builds from it.
- A load in progress (an unstamped table) blocks the canonical build and the matcher.
- A re-patch makes the paid relation and the future relation stale, and changes the matcher's prefix.
- A DDL change makes the relations built under the old one stale.
- The input map matches the tables each DDL reads.
