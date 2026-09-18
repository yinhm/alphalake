"""源盘点保留零、缺字段、身份歧义与截止，不生成经济回报。"""
import copy
import struct
from tools.audit_tdx_reinvestment_inputs import audit, FIELDS
from tools.tdx_research_source import source_field


def test_reinvestment_source_boundaries():
    bits = lambda n: struct.unpack('<I', struct.pack('<f', n))[0]
    source = dict(contract_version='tdx-history-source-v1',artifacts=[],records=[])
    for year in range(2021,2025):
        period=f'{year}-12-31';file=period+'.zip'
        source['artifacts'].append(dict(file=file,report_period=period,fetched_at='2026-09-11T00:00:00Z'))
        source['records'].append(dict(code='000001',period=period,artifact=file,
            bits={**{source_field(f):bits(100) for f in FIELDS},'FN314':bits((year-1999)*10000+430)}))
    expected=audit(source,[dict(code='000001')])
    assert all(all(r['complete_source_groups'].values()) for r in expected['results'])
    for action,reason in [('zero','source_zero_ambiguous'),('missing','missing_field'),
                          ('duplicate','duplicate_identity'),('late','unavailable_at_cutoff')]:
        changed=copy.deepcopy(source)
        row=changed['records'][0]
        if action=='zero': row['bits']['FN52']=bits(0)
        elif action=='missing': del row['bits']['FN52']
        elif action=='duplicate': changed['records'].append(copy.deepcopy(row))
        else: row['bits']['FN314']=bits(230901)
        actual=audit(changed,[dict(code='000001')])
        assert actual['results'][0]['annual_inputs'][1]['fields']['FN52']['status']==reason
        assert not actual['results'][0]['complete_source_groups']['debt_components']
        assert actual['results'][1:]==expected['results'][1:]
        assert all(r['economic_roic'] is None and r['classified_reinvestment'] is None for r in actual['results'])
    changed=copy.deepcopy(source)
    changed['records'][-1]['bits']['FN314']=-1
    assert audit(changed,[dict(code='000001')])['results'][:2]==expected['results'][:2]
