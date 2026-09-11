"""年度研发输入边界；真实600家结果另由冻结本地源快照运行。"""
import copy
import struct

from tools.audit_tdx_rd_history import audit


def test_rd_history_requires_oldest_amortization_cohort_and_preserves_rejections():
    bits = lambda n: struct.unpack('<I', struct.pack('<f', n))[0]
    source = dict(contract_version='tdx-history-source-v1', records=[], artifacts=[])
    for year in range(2017, 2026):
        file = f'{year}.zip'
        source['artifacts'].append(dict(file=file, report_period=f'{year}-12-31', fetched_at='2026-09-11T00:00:00Z'))
        source['records'].append(dict(code='000001', period=f'{year}-12-31', artifact=file,
                                     bits={'FN304':bits(100), 'FN314':bits((year-1999)*10000+430)}))
    samples = [dict(code='000001')]
    expected = audit(source, samples)
    assert all(r['complete_by_life'] == {'3':True, '5':True} for r in expected['results'])
    future = copy.deepcopy(source)
    future['records'][-1]['bits'] = {'FN304':-1, 'FN314':-1}
    assert audit(future, samples) == expected
    for mutation, reason in [('remove','missing_record'), ('duplicate','duplicate_identity'),
                             ('zero','source_zero_ambiguous'), ('late','unavailable_at_cutoff')]:
        changed = copy.deepcopy(source)
        if mutation == 'remove': changed['records'].pop(0)
        elif mutation == 'duplicate': changed['records'].append(copy.deepcopy(changed['records'][0]))
        elif mutation == 'zero': changed['records'][0]['bits']['FN304'] = bits(0)
        else: changed['records'][0]['bits']['FN314'] = bits(230901)
        actual = audit(changed, samples)
        first = actual['results'][0]
        assert first['complete_by_life'] == {'3':True, '5':False}
        assert first['annual_inputs'][-1]['status'] == reason
        assert actual['summary']['2023']['candidates'] == 1
        assert actual['results'][1:] == expected['results'][1:]
