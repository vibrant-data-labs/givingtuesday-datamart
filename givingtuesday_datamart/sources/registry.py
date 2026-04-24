"""Registry of Datamart sources we ingest."""

from __future__ import annotations

from givingtuesday_datamart.sources.spec import FormType, SourceSpec


S3_BUCKET = "gt990datalake-analytics-and-datamarts"
S3_PREFIX = "EfileDataMarts/"


def _spec(
    logical_name: str,
    staging_table_name: str,
    form_type: FormType,
    description: str,
    filename_regex: str,
    *,
    primary_key: tuple[str, ...] | None = None,
    required_columns: tuple[str, ...] = (),
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
    )


REGISTRY: tuple[SourceSpec, ...] = (
    _spec(
        logical_name="irs_990_basic_fields",
        staging_table_name="public.basic_fields",
        form_type="990",
        description="Form 990 standard header + financial summary fields, one row per filing.",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990StandardFields\.csv$",
        required_columns=("filerein", "taxyear"),
    ),
    _spec(
        logical_name="irs_990pf_basic_fields",
        staging_table_name="public.basic_fields_pf",
        form_type="990-PF",
        description="Form 990-PF standard header + financial summary fields, one row per filing.",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990PFStandardFields\.csv$",
        required_columns=("filerein", "taxyear"),
    ),
    _spec(
        logical_name="irs_990_missions",
        staging_table_name="public.mission_statements",
        form_type="990",
        description="Form 990 Part I mission statement narrative, one row per filing.",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990Part1Missions\.csv$",
        required_columns=("filerein",),
    ),
    _spec(
        logical_name="irs_990_programs",
        staging_table_name="public.programs",
        form_type="990",
        description="Form 990 Part III program accomplishment narratives (activities 1/2/3).",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990Part3Programs\.csv$",
        required_columns=("filerein",),
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
    ),
    _spec(
        logical_name="irs_990pf_grants",
        staging_table_name="public.privategrants",
        form_type="990-PF",
        description="Form 990-PF Part XIV Grants/Contributions Paid (3A) — grant line items from private foundations.",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990PFPart14Grants3A\.csv$",
        required_columns=("filerein",),
    ),
    _spec(
        logical_name="irs_990_officers",
        staging_table_name="public.officers",
        form_type="990",
        description="Form 990 Part VII-A — officers, directors, trustees, and key employees (with compensation).",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990Part7AOfficers\.csv$",
        required_columns=("filerein",),
    ),
    _spec(
        logical_name="irs_990pf_officers",
        staging_table_name="public.officers_pf",
        form_type="990-PF",
        description="Form 990-PF Part VII p1 — list of officers, directors, trustees, foundation managers.",
        filename_regex=r"^(\d{4}_\d{2}_\d{2})_All_Years_990PFPart7p1-?Officers\.csv$",
        required_columns=("filerein",),
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
