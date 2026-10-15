from dataclasses import asdict

import pytest

from givingtuesday_datamart.client.models import combine_dba, NonprofitHit, FunderIdentity
from test_client_identity_search import _client, _row


@pytest.mark.parametrize('lines,expected', [((None, None), None), ((' \t', '\n'), None),
    ((' DBA ', None), 'DBA'), ((None, ' DBA '), 'DBA'), ((' COMMUNITY ', '\tCENTER\n'), 'COMMUNITY CENTER')])
def test_dba_lines(lines, expected):
    assert combine_dba(*lines) == expected


def test_search_returns_names_without_changing_match_sql():
    row = _row('123456789', name='Legal name', name_secondary='Line two',
               dba_1=' Trading ', dba_2=' Name ', unique_text='mission')
    client, session = _client([[row]])
    result = asdict(client.search_nonprofits(['education'])[0])
    assert [result[k] for k in ('businessname1', 'businessname2', 'dba_name')] == ['Legal name', 'Line two', 'Trading Name']
    assert 'nc.dba_1' in session.calls[0][0]


def test_identity_single_and_bulk_have_same_dba():
    from givingtuesday_datamart.client import IdentityQuery
    row = _row('123456789', dba_1=' A ', dba_2=' B ')
    client, _ = _client([[row], [dict(row, key='a')]])
    single = client.search_identity(name='A', org_type='nonprofit')[0]
    bulk = client.search_identity_bulk([IdentityQuery(key='a', name='A')], org_type='nonprofit')['a'][0]
    assert asdict(single) == asdict(bulk)
    assert single.dba_name == 'A B'


def test_funder_lookup_unknown_and_nullable_names():
    client, session = _client([[dict(ein='012345678', businessname1=None, businessname2=None, dba_1=None, dba_2=None)]])
    assert client.get_funder_identities(['01-2345678', '012345678']) == [FunderIdentity('012345678', None, None, None)]
    sql, params = session.calls[0]
    assert params['eins'] == ['012345678']
    assert 'CASE WHEN fc.ein IS NOT NULL THEN fc.name ELSE nc.name END' in sql
    assert 'CASE WHEN fc.ein IS NULL THEN nc.dba_1 END' in sql
    with pytest.raises(ValueError, match='Invalid funder EIN'):
        client.get_funder_identities(['bad'])


def test_summaries_bulk_resolve_once_preserve_totals_and_serialize():
    summaries = [dict(ein='111111111', taxyear=y, total_grant_amount=250, grant_count=3,
                      granter_eins=['987654321', '012345678']) for y in [2023, 2024]]
    identities = [dict(ein=e, businessname1='Same name', businessname2='Line two' if e == '012345678' else None,
                       dba_1='DBA' if e == '012345678' else None, dba_2=None) for e in ['012345678', '987654321']]
    client, session = _client([summaries, identities])
    rows = client.get_grant_summaries(['111111111'])
    assert len(session.calls) == 2
    assert rows[0].granters == rows[1].granters
    serialized = asdict(rows[0])
    assert 'granter_eins' not in serialized and 'granter_names' not in serialized
    assert serialized['total_grant_amount'] == 250
    assert serialized['grant_count'] == 3
    assert [r['ein'] for r in serialized['granters']] == ['012345678', '987654321']
    assert serialized['granters'][0]['dba_name'] == 'DBA'
    assert serialized['granters'][1]['dba_name'] is None
