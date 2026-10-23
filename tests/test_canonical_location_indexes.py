"""Location indexes must match the SQL filter expressions exactly."""
from givingtuesday_datamart.canonical.build import _add_location_indexes


def test_location_indexes_support_state_and_city_key_filters():
    class Session:
        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(str(statement))

    for name in ("nonprofit_canonical", "funder_canonical"):
        session = Session()
        _add_location_indexes(session, f"scratch.{name}")
        assert session.statements == [
            f"CREATE INDEX ix_{name}_state ON scratch.{name} (state)",
            f"CREATE INDEX ix_{name}_city_key ON scratch.{name} "
            "(REGEXP_REPLACE(UPPER(city), '[^A-Z]', '', 'g'))",
        ]
