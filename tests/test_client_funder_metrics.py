from dataclasses import asdict
from decimal import Decimal
import pytest
from test_client_identity_search import _client


def test_bulk_metrics_preserve_exact_amounts_and_unknown_eins():
    row = dict(ein='111111111', total_portfolio_count=4, total_portfolio_amount=Decimal('9007199254740993'),
               scoped_recipient_count=2, scoped_grant_amount=Decimal('123'), grant_count=10,
               unidentified_recipient_grant_count=1, unidentified_recipient_grant_amount=Decimal("999"), first_taxyear=2014, last_taxyear=2025)
    client, session = _client([[row]])
    results = client.get_funder_portfolio_metrics(['11-1111111', '111111111', '000000000'],
        recipient_eins=['01-2345678', '012345678'], min_taxyear=2019, max_taxyear=2025)
    unknown, known = results
    assert unknown.total_portfolio_count is None and unknown.total_portfolio_amount is None
    assert known.total_portfolio_amount == 9007199254740993
    assert known.scoped_grant_amount == 123
    assert known.scope_min_taxyear == 2019
    assert asdict(known)['source_ref'].endswith('unioned_grants')
    assert len(session.calls) == 1
    sql, params = session.calls[0]
    assert params['eins'] == ['000000000', '111111111']
    assert params['recipients'] == ['012345678']
    assert 'COUNT(DISTINCT grantee_ein)' in sql and 'SUM(grant_amount)' in sql
    assert 'FROM public.unioned_grants WHERE granter_ein' in sql


def test_metrics_empty_invalid_inputs_and_unscoped_amounts():
    client, session = _client()
    assert client.get_funder_portfolio_metrics([]) == []
    for kwargs in [dict(eins=['bad']), dict(eins=['111111111'], recipient_eins=['bad']),
                   dict(eins=['111111111'], min_taxyear=2025, max_taxyear=2019),
                   dict(eins=['111111111'], min_taxyear=True)]:
        with pytest.raises(ValueError):
            client.get_funder_portfolio_metrics(**kwargs)
    assert not session.calls
    record = client.get_funder_portfolio_metrics(['111111111'])[0]
    assert record.scoped_recipient_count is None and record.scoped_grant_amount is None
