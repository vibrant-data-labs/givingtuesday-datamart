"""Classification indexes must match the SQL filter expressions exactly."""
from givingtuesday_datamart.canonical.build import _add_classification_indexes


def test_classification_indexes_support_prefix_and_subsection_filters():
    class Session:
        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(str(statement))

    for name in ("nonprofit_canonical", "funder_canonical"):
        session = Session()
        _add_classification_indexes(session, f"scratch.{name}")
        assert session.statements == [
            f"CREATE INDEX ix_{name}_ntee ON scratch.{name} "
            "(UPPER(BTRIM(ntee_cd)) text_pattern_ops)",
            f"CREATE INDEX ix_{name}_subsection ON scratch.{name} "
            "(LPAD(BTRIM(subsection), 2, '0'))",
        ]
