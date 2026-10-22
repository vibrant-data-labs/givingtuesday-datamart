"""Bulk source-profile contract; no tracker identifiers or publication state."""
from dataclasses import asdict
from datetime import datetime, timezone
import json

import pytest

from givingtuesday_datamart.client import FunderProfile
from test_client_identity_search import _client


def row(ein, **overrides):
    record = dict(ein=ein, businessname1=None, businessname2=None, dba_1=None, dba_2=None,
                  website=None, city=None, state=None, country=None, latest_taxyear=None,
                  source_built_at=None, source_form=None, filing_text=None, text_built_at=None,
                  has_nonprofit_record=False, has_funder_record=False)
    record.update(overrides)
    return record


def test_profiles_preserve_names_text_and_source_provenance():
    built = datetime(2026, 10, 20, tzinfo=timezone.utc)
    client, session = _client([[row('012345678', businessname1='Legal', businessname2='Line two',
                                  dba_1=' Trading ', dba_2=' Name ', has_nonprofit_record=True,
                                  website='example.org', city='Boston', state='MA', country='US',
                                  latest_taxyear=2024, source_built_at=built,
                                  source_form='990', filing_text='A substantive mission.', text_built_at=built)]])
    result = client.get_funder_profiles(['01-2345678', '012345678'])[0]
    assert isinstance(result, FunderProfile)
    assert result.dba_name == 'Trading Name'
    assert result.businessname2 == 'Line two'
    assert result.filing_text == 'A substantive mission.'
    assert result.source_built_at == built and result.text_built_at == built
    assert result.latest_taxyear == 2024
    assert result.has_nonprofit_record and not result.has_funder_record
    assert 'nonprofit_canonical' in result.source_ref
    serialized = json.loads(json.dumps(asdict(result), default=str))
    assert serialized['ein'] == '012345678'
    assert 'funder_uid' not in serialized and 'match_method' not in serialized
    assert session.calls[0][1] == {'eins': ['012345678']}


def test_pf_precedence_and_nonprofit_text_fallback_are_in_source_query():
    client, session = _client([[row('111111111', businessname1='PF name', has_funder_record=True)]])
    result = client.get_funder_profiles(['111111111'])[0]
    assert result.dba_name is None and result.filing_text is None
    sql, _ = session.calls[0]
    assert 'CASE WHEN fc.ein IS NOT NULL THEN fc.name ELSE nc.name END' in sql
    assert 'CASE WHEN fc.ein IS NOT NULL THEN fc.name_secondary ELSE nc.name_secondary END' in sql
    assert 'CASE WHEN fc.ein IS NULL THEN nc.dba_1 END' in sql
    assert 'nc.website' in sql and 'nt.unique_text_compact AS filing_text' in sql


def test_unknown_ein_returns_an_explicit_absent_identity():
    client, _ = _client([[row('000000000')]])
    result = client.get_funder_profiles(['000000000'])[0]
    assert result.ein == '000000000'
    assert not result.has_nonprofit_record and not result.has_funder_record
    assert all(v is None for k, v in asdict(result).items()
               if k not in ('ein', 'source_ref', 'has_nonprofit_record', 'has_funder_record'))


def test_empty_and_invalid_inputs_do_not_query():
    client, session = _client()
    assert client.get_funder_profiles([]) == []
    for value in ['bad', '123', 123456789, None]:
        with pytest.raises(ValueError, match='Invalid funder EIN'):
            client.get_funder_profiles(['012345678', value])
    assert not session.calls


def test_bulk_batches_are_deduplicated_bounded_and_sorted():
    identities = [f'{i:09d}' for i in range(1002)]
    client, session = _client([[row(ein) for ein in identities[:1000]],
                               [row(ein) for ein in identities[1000:]]])
    profiles = client.get_funder_profiles(list(reversed(identities)) + identities[:2])
    assert [p.ein for p in profiles] == identities
    assert len(session.calls) == 2
    assert session.calls[0][1]['eins'] == identities[:1000]
    assert session.calls[1][1]['eins'] == identities[1000:]
    assert all('ORDER BY ids.ein' in sql for sql, _ in session.calls)
