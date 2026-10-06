---
name: get_session accepts a custom config
description: vdl-tools' get_session(config=...) takes a config override, so pointing at a different Postgres database (or host) is just a dict swap — no wrapper module needed.
type: feedback
originSessionId: dd86232e-0fbe-46bb-8d83-fdd9fe748fb8
---
`vdl_tools.shared_tools.database_cache.database_utils.get_session(config=None)`
accepts a config override. To target a different database on the same RDS host,
clone the result of `get_configuration()` and swap `config["postgres"]["database"]`,
then pass it in:

```python
cfg = get_configuration()
datamart_cfg = {**cfg, "postgres": {**cfg["postgres"], "database": "datamart"}}
with get_session(config=datamart_cfg) as session:
    ...
```

**Why:** I initially proposed writing a local `db.py` to own the datamart
connection. Zein corrected me — `get_session` already supports this pattern.
Overbuilding the connection layer is unnecessary.

**How to apply:** When the datamart needs a separate Postgres database from the
standard VDL one, plumb a config override through; don't introduce a parallel
connection utility. The only reusable piece worth extracting is a tiny helper
that builds the datamart config dict (e.g., `datamart_config()`), not a whole
db module.
