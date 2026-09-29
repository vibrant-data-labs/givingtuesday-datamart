"""Placeholder recovery, the production package: the work list a run reads
(stage 0) and the load of the recovered grants into the datamart (stage 5).

    python -m givingtuesday_datamart.placeholder_recovery work-list
    python -m givingtuesday_datamart.placeholder_recovery run --policy v2 --tax-years 2020-2025 --dry-run --no-fetch
    python -m givingtuesday_datamart.placeholder_recovery load --policy v2

The stages between the two (fetch, read, agree, select) are
``filing_images``, ``page_readings``, ``page_verdicts`` and
``attachment_grants``. The design is ``docs/placeholder_recovery_pipeline.md``,
stages 0 and 5; how to run it is ``docs/placeholder_recovery_operations.md``.

==========================  ==================================================
``classifier``              the pointer pattern, as SQL
``exclusions``              the filers left out, from ``data/placeholder_recovery/exclusions.csv``
``work_list``               ``pf_placeholder_filings``: one row per filing to fetch and read
``address``                 the state and zip an address ends with
``loader``                  ``privategrants_recovered``: one row per recovered grant
``view``                    ``privategrants_current_w_ocred``
``run``                     ``run`` over the work list in place of a frame file
==========================  ==================================================
"""
