"""Our own extract of the IRS e-file XML, straight from the IRS's monthly batches.

The IRS's archives (``batches``) are parsed return by return (``parse``) into
rows shaped like GivingTuesday's ten staging tables (``extract``, through
``data/own_extract/concordance.csv``), loaded into the ``irs_extract`` schema
with a run in ``datamart_meta.ingest_runs`` (``load``), and measured against
GT's tables (``parity``, ``report``). ``discover`` proposes the concordance
from returns both sides hold. Command line: ``python -m
givingtuesday_datamart.own_extract``; results and what they mean:
``docs/own-extract.md``.
"""
