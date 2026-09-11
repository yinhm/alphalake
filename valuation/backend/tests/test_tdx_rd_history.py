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


def test_real_rd_semantics_keep_positive_bits_and_zero_counterexample():
    import pytest
    from tools.verify_tdx_rd_semantics import load_inputs, verify

    ledger, anker, old = load_inputs()
    result = verify(ledger, anker, old)
    assert len(result) == 8
    assert sum(r['status'] == 'matched_source_bits' for r in result) == 7
    assert result[-1]['status'] == 'zero_source_positive_comparative'
    assert result[-1]['pdf_value_cny'] == '537932446.49'
    changed = copy.deepcopy(ledger)
    changed['reports'][0]['values'][1] = '14566949.81'
    with pytest.raises(ValueError, match='PDF amounts differ'):
        verify(changed, anker, old)
    changed = copy.deepcopy(old)
    next(r for r in changed['records'] if r['code']=='002943')['bits']['FN304'] ^= 1
    with pytest.raises(ValueError, match='FN304 bits differ'):
        verify(ledger, anker, changed)
    changed = copy.deepcopy(old)
    next(r for r in changed['records'] if r['code']=='000066')['bits']['FN304'] = 1
    with pytest.raises(ValueError, match='zero counterexample differs'):
        verify(ledger, anker, changed)
