"""Registry of Datamart sources we ingest."""

from __future__ import annotations

from givingtuesday_datamart.sources.spec import DedupeSpec, FormType, IndexSpec, SourceSpec


S3_BUCKET = "gt990datalake-analytics-and-datamarts"
S3_PREFIX = "EfileDataMarts/"

# IRS Exempt Organizations Business Master File: six plain CSVs republished
# in place roughly monthly (no version in the name; the server's
# Last-Modified is the version). Four regions, international (eo_xx) and
# Puerto Rico (eo_pr). eo4, "all other areas", is a few thousand rows of
# territories and foreign addresses that eo_xx / eo_pr list again, which is
# where the cross-file duplicate EINs come from. Order matters: it is the
# dedupe tie-break.
BMF_BASE_URL = "https://www.irs.gov/pub/irs-soi/"
BMF_FILES = ("eo1", "eo2", "eo3", "eo4", "eo_xx", "eo_pr")


def _spec(
    logical_name: str,
    staging_table_name: str,
    form_type: FormType,
    description: str,
    filename_regex: str,
    *,
    primary_key: tuple[str, ...] | None = None,
    required_columns: tuple[str, ...] = (),
    skip_default_refresh: bool = False,
    indexes: tuple[IndexSpec, ...] = (),
) -> SourceSpec:
    return SourceSpec(
        logical_name=logical_name,
        staging_table_name=staging_table_name,
        form_type=form_type,
        description=description,
        s3_bucket=S3_BUCKET,
        s3_prefix=S3_PREFIX,
        filename_regex=filename_regex,
        primary_key=primary_key,
        required_columns=required_columns,
        skip_default_refresh=skip_default_refresh,
        indexes=indexes,
    )


REGISTRY: tuple[SourceSpec, ...] = (
    # The four `filerein` indexes below feed the frontend profile page, which
    # does ~5 parallel `WHERE filerein = $1` lookups across these tables. Cold
    # seq scans against basic_fields (~3 GB) take ~6s each → in parallel they
    # blow past Kysely's 30s statement_timeout. With the indexes, profile p95
    # is ~165ms cold / ~10ms warm.
    _spec(
        logical_name="irs_990_basic_fields",
        staging_table_name="public.basic_fields",
        form_type="990",
        description="Form 990 standard header + financial summary fields, one row per filing.",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990StandardFields\.csv$",
        required_columns=("filerein", "taxyear"),
        indexes=(IndexSpec("ix_basic_fields_filerein", ("filerein",)),),
    ),
    _spec(
        logical_name="irs_990pf_basic_fields",
        staging_table_name="public.basic_fields_pf",
        form_type="990-PF",
        description="Form 990-PF standard header + financial summary fields, one row per filing.",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990PFStandardFields\.csv$",
        required_columns=("filerein", "taxyear"),
        indexes=(IndexSpec("ix_basic_fields_pf_filerein", ("filerein",)),),
    ),
    # 990-EZ is the short-form return for small filers (gross receipts
    # <$200K, assets <$500K). GT ships it as its own extract with its own
    # column names — CONGIFGRAETC/TOTALRREVENU/TOTALEEXPENS/WEBSITADDRES
    # where the 990 says totacashcont/totrevcuryea/totexpcuryea/
    # websitsiteit. The crosswalk lives in the canonical build, not here:
    # staging stays byte-faithful to the source, same invariant as every
    # other spec.
    #
    # Why a separate table rather than GT's Combined DataMart (which does
    # harmonize the names): the combined extract is 48 columns against
    # 990's 164 / 990-PF's 137, drops 24 columns we read today (the whole
    # PF financial panel, the 990 revenue breakdown, incarenm,
    # formationorm), and its 990N rows carry no FileSha256 or URL — which
    # would break the filing-version dedup rule from #33/#34. It is still
    # useful as the authoritative EZ→990 column mapping; we just don't
    # ingest it.
    _spec(
        logical_name="irs_990ez_basic_fields",
        staging_table_name="public.basic_fields_ez",
        form_type="990-EZ",
        description="Form 990-EZ standard header + financial summary fields, one row per filing.",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990EZStandardFields\.csv$",
        required_columns=("filerein", "taxyear"),
        indexes=(IndexSpec("ix_basic_fields_ez_filerein", ("filerein",)),),
    ),
    # Long-shaped, unlike `public.programs`: one row per program service
    # accomplishment (`psadpsaccom`) rather than three wide activity
    # columns. `nonprofit_text` needs its own UNION arm for it.
    _spec(
        logical_name="irs_990ez_programs",
        staging_table_name="public.programs_ez",
        form_type="990-EZ",
        description="Form 990-EZ Part III — primary exempt purpose + program service accomplishments.",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990EZPart3Programs\.csv$",
        required_columns=("filerein",),
        indexes=(IndexSpec("ix_programs_ez_filerein", ("filerein",)),),
    ),
    _spec(
        logical_name="irs_990_missions",
        staging_table_name="public.mission_statements",
        form_type="990",
        description="Form 990 Part I mission statement narrative, one row per filing.",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990Part1Missions\.csv$",
        required_columns=("filerein",),
        indexes=(IndexSpec("ix_mission_statements_filerein", ("filerein",)),),
    ),
    _spec(
        logical_name="irs_990_programs",
        staging_table_name="public.programs",
        form_type="990",
        description="Form 990 Part III program accomplishment narratives (activities 1/2/3).",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990Part3Programs\.csv$",
        required_columns=("filerein",),
        indexes=(IndexSpec("ix_programs_filerein", ("filerein",)),),
    ),
    _spec(
        logical_name="irs_schedule_o",
        staging_table_name="public.schedule_o",
        form_type="990",
        description="Schedule O supplemental narrative (including Part III program descriptions).",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_Schedule_?O\.csv$",
        required_columns=("filerein",),
    ),
    _spec(
        logical_name="irs_schedule_i_grants",
        staging_table_name="public.grants_to_domestic_organizations",
        form_type="990",
        description="Schedule I Part II — grants and other assistance to domestic organizations.",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_ScheduleIPart2Grants\.csv$",
        required_columns=("filerein",),
        indexes=(IndexSpec("ix_grants_to_domestic_organizations_filerein", ("filerein",)),),
    ),
    _spec(
        logical_name="irs_990pf_grants",
        staging_table_name="public.privategrants",
        form_type="990-PF",
        description="Form 990-PF Part XIV Grants/Contributions Paid (3A) — grant line items from private foundations.",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990PFPart14Grants3A\.csv$",
        required_columns=("filerein",),
        indexes=(IndexSpec("ix_privategrants_filerein", ("filerein",)),),
    ),
    # The other half of Part XIV line 3: grants approved for future payment.
    # Same release and same shape as 3A, with the columns prefixed SIGOCAFF
    # where 3A says SIGOCPY, and no person-name column. Placeholder recovery
    # reads it for the amount a future-payment list must add up to
    # (`placeholder_recovery.work_list`). GT published the file under two
    # older names (990PFP15Grants3B, 990PFPart15Grants3B) until 2024; the
    # regex takes the current name only, as the 3A entry does.
    _spec(
        logical_name="irs_990pf_grants_future",
        staging_table_name="public.privategrants_future",
        form_type="990-PF",
        description="Form 990-PF Part XIV Grants/Contributions Approved for Future Payment (3B) — grant line items from private foundations.",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990PFPart14Grants3B\.csv$",
        required_columns=("filerein",),
        indexes=(IndexSpec("ix_privategrants_future_filerein", ("filerein",)),),
    ),
    # Officers staging tables are skipped from default refresh (2026-04-27).
    # The raw officers CSV is ~18 GB hot, ~2 GB for the PF side; together they
    # were the binding constraint on the shared RDS. Both `public.officers`
    # and `public.officers_pf` were dropped, and the `--with-people` canonical
    # build (person_canonical, org_person_role) is indefinitely deferred.
    # Re-introduce by:
    #   1) flipping skip_default_refresh=False here, OR
    #   2) explicit one-off: `refresh --source irs_990_officers`
    # When that happens, also revisit the pre-process plan in the project
    # backbone notes (year filter, column prune, possibly stream-direct-to-
    # canonical) so we don't re-create the disk pressure.
    _spec(
        logical_name="irs_990_officers",
        staging_table_name="public.officers",
        form_type="990",
        description="Form 990 Part VII-A — officers, directors, trustees, and key employees (with compensation).",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990Part7AOfficers\.csv$",
        required_columns=("filerein",),
        skip_default_refresh=True,
    ),
    _spec(
        logical_name="irs_990pf_officers",
        staging_table_name="public.officers_pf",
        form_type="990-PF",
        description="Form 990-PF Part VII p1 — list of officers, directors, trustees, foundation managers.",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990PFPart7p1-?Officers\.csv$",
        required_columns=("filerein",),
        skip_default_refresh=True,
    ),
    # The IRS's own register of exempt orgs: name of record and address, the
    # 501(c) subsection and foundation code, NTEE code, group exemption,
    # ruling date, filing requirement, and the tax period and amounts of the
    # latest return the IRS processed. None of SUBSECTION's number,
    # FOUNDATION, NTEE_CD or GROUP is asked for on a 990, so no e-file
    # datamart can carry them; and 990-N filers and churches appear here and
    # nowhere else. docs/bmf-assessment.md has the measurements.
    #
    # One row per EIN: 5,004 EINs repeat across files with identical rows
    # (territories listed in eo4 and again in eo_xx / eo_pr), collapsed
    # after COPY by latest TAX_PERIOD, then file order. The unique index is
    # the contract every consumer joins on.
    SourceSpec(
        logical_name="irs_eo_bmf",
        staging_table_name="public.eo_bmf",
        form_type="EO-BMF",
        description=(
            "IRS Exempt Organizations Business Master File — every currently recognized "
            "exempt org, one row per EIN (name, address, subsection, foundation code, "
            "NTEE, ruling date, filing requirement, latest processed return)."
        ),
        urls=tuple(f"{BMF_BASE_URL}{name}.csv" for name in BMF_FILES),
        required_columns=("ein", "name"),
        indexes=(IndexSpec("ix_eo_bmf_ein", ("ein",), unique=True),),
        dedupe=DedupeSpec(key=("ein",), order_by="tax_period DESC NULLS LAST"),
        # irs.gov dropped the stream after ~15 MB while a COPY batch held
        # the connection idle; the largest file is 166 MB, so spooling is
        # cheap and the download runs at full speed.
        spool_to_disk=True,
    ),
)


def get_source(logical_name: str) -> SourceSpec:
    for spec in REGISTRY:
        if spec.logical_name == logical_name:
            return spec
    raise KeyError(
        f"No source registered with logical_name={logical_name!r}. "
        f"Known: {sorted(s.logical_name for s in REGISTRY)}"
    )
