"""Proofs and measurements: a rule or a change shown on a copy, with nothing
of production's written.

Each module runs the production code's own pieces on scratch copies or on a
subset and reports what would change. The production tables, the matcher's
two output tables and the loaded recovered rows are never written here. The
commands are in ``docs/placeholder_recovery_operations.md``; the SQL the
measurements run is under ``data/proofs/``.

=========================  ==================================================
``matching_subset``         the matcher on a subset of the grant tuples, before
                            and after a change, under the prefix ``scratch_matcher_``
``pf_current_scratch``      the 990-PF ``_current`` rules proved on scratch
                            copies: ``build``, ``compare``, ``placeholders``, ``drop``
``pf_doubling_basic_rows``  the doubled basic row and the fills measured on the
                            extract, and ``xml``: a filing's own groups against
                            its table rows, the oracle for every line-25 rule
``recovered_rows``          what the recovered rows carry for the matcher (the
                            address, the names), measured on a frame
``renderer_widths``         the page widths of the IRS's older renderers, the
                            measurement behind ``irs_source.RENDERED_WIDTHS``
=========================  ==================================================
"""
