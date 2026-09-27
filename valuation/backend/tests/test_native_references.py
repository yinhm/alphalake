"""真实单元格、源歧义及原生参考快照的拒绝边界。"""
from pathlib import Path
import hashlib
import json
from decimal import Decimal
import pytest
import xlrd
from data_sources.damodaran_parsers.native_reference_parser import snapshot

ROOT=Path(__file__).resolve().parents[3]/'internal/source/damodaran/testdata/native'


def test_reviewed_native_workbooks_and_source_cells():
    sources=json.loads((ROOT/'sources.json').read_text())
    total=0
    for entry in sources:
        path=ROOT/entry['file']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256']
        packet=snapshot(path)
        assert len(packet['observations'])==entry['observations']
        total+=len(packet['observations'])
        if entry['file']=='betas.xls':
            row=next(o for o in packet['observations'] if o['subject']=='Computers/Peripherals' and o['metric_code']=='beta_unlevered_cash_adjusted')
            assert float(row['value'])==pytest.approx(1.3245870396777912,abs=5e-13)
        if entry['file']=='countrytaxrates.xls':
            rows={o['subject']:o for o in packet['observations']}
            assert rows['CN']['value']=='0.250000000000'
            assert rows['HK']['value']=='0.165000000000'
            assert rows['US']['value'] is None and rows['US']['value_status']=='ambiguous'
            assert rows['US']['source_locator']=='Sheet1!B217;Sheet1!B246'
    assert total==3575


def test_region_and_header_tampering_rejected(monkeypatch):
    real=xlrd.open_workbook
    def tampered(*args,**kwargs):
        wb=real(*args,**kwargs)
        sheet=wb.sheet_by_name('Industry Averages')
        sheet._cell_values[2][5]='Global'
        return wb
    monkeypatch.setattr(xlrd,'open_workbook',tampered)
    with pytest.raises(ValueError,match='region mismatch'):
        snapshot(ROOT/'betas.xls')


def test_sqlite_snapshot_roundtrip_hash_and_missing_reference():
    import sqlite3
    from data_sources.native_references import write_snapshot,load_snapshot
    from data_sources.damodaran_parsers.country_risk_parser import alphalake_country_snapshot
    packet=dict(contract='alphalake-native-references-v1',information_as_of='2026-09-27T14:00:00Z',
        releases=[],industry_stats=[],country_tax=[],country_risk=[],companies=[])
    def release(identity,dataset,source_hash):
        packet['releases'].append(dict(release_id=identity,dataset=dataset,source_version='2026-01-05',
            available_at=packet['information_as_of'],first_seen_at=packet['information_as_of'],source_locator='fixture:'+dataset,
            sha256=source_hash,content_key=source_hash,parser_version='fixture',normalization_version='fixture'))
    for identity,entry in enumerate(json.loads((ROOT/'sources.json').read_text()),1):
        parsed=snapshot(ROOT/entry['file']);release(identity,entry['file'],entry['sha256'])
        group='country_tax' if entry['file']=='countrytaxrates.xls' else 'industry_stats'
        for row in parsed['observations']:
            row=dict(row,release_id=identity,method_code='provider_reported',observation_date=parsed['observation_date'],industry=row['subject'],subject_code=row['subject'],sample_region=parsed['sample_region'])
            packet[group].append(row)
    risk=alphalake_country_snapshot(ROOT.parent/'ctrypremJuly26.xlsx')
    release(24,'country-risk',risk['workbook_sha256'])
    packet['country_risk']=[dict(o,release_id=24,method_code='rating_based',observation_date=risk['observation_date'],raw_unit='fraction',value_status='reported') for o in risk['observations']]
    release(25,'company-industry','a'*64)
    packet['companies']=[dict(release_id=25,source_locator='fixture!A2:H2',raw_payload=json.dumps(dict(
        ticker='SZSE:300866',name='Anker',industry='Computers/Peripherals',country='China',sector='Technology',sic_code='test',broad_group='test',sub_group='test')))]
    with sqlite3.connect(':memory:') as conn:
        conn.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        write_snapshot(conn,packet)
        loaded=load_snapshot(conn)
        assert loaded.industry_mapper.lookup('SZSE:300866').industry_group=='Computers/Peripherals'
        assert loaded.lookup_industry('Computers/Peripherals','US').beta_u_corrected_for_cash==pytest.approx(1.3245870396777912,abs=5e-13)
        from data_sources.damodaran_store import DamodaranStore
        source=DamodaranStore.from_directory(ROOT)
        for region in ('US','Global'):
            for industry in loaded.list_industries(region):
                expected=source.lookup_industry(industry,region).model_dump()
                actual=loaded.lookup_industry(industry,region).model_dump()
                for key,value in expected.items():
                    if isinstance(value,(int,float)):
                        assert abs(Decimal(str(actual[key]))-Decimal(str(value)))<=Decimal('0.0000000000005'), (industry,key)
                    else:
                        assert actual[key]==value, (industry,key)
        assert loaded.lookup_industry('Chemical (Diversified)','US').industry_effective_tax_rate == 0
        assert loaded.lookup_industry('invented','US') is None
        from data_sources.native_references import reference_gaps,attach_reference_diagnostic
        gaps=reference_gaps(loaded,'SZSE:003816')
        assert {g['field'] for g in gaps}=={'industry','country_erp_and_tax'}
        diagnostic=attach_reference_diagnostic(dict(status='ready',blockers=[]),loaded,'SZSE:003816')
        assert diagnostic['financial_status']=='ready' and diagnostic['status']=='blocked_reference_inputs'
        assert reference_gaps(loaded,'SZSE:003816','Power','China')==[]
        assert loaded.lookup_country('China').tax_rate_marginal==.25
        with pytest.raises(ValueError,match='ambiguous'):
            loaded.lookup_country('United States')
        conn.execute("UPDATE reference_value SET value='0.99' WHERE metric='cost_of_debt_pretax' AND region='us'")
        with pytest.raises(ValueError,match='hash mismatch'):
            load_snapshot(conn)
