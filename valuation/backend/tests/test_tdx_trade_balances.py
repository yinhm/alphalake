"""真实三起点余额候选重放，以及来源时点/未来数据隔离。"""
from copy import deepcopy
from decimal import Decimal as D
import gzip
import json
from pathlib import Path
import struct

from tools.backtest_tdx_trade_balances import evaluate, run

ROOT = Path(__file__).resolve().parents[3]/'valuation/research/continuing-operations-five'


def test_real_multiyear_result_and_evidence_boundaries(tmp_path):
    plan = json.loads((ROOT/'trade-balance-mean-plan.json').read_bytes())
    source = json.loads((ROOT/'capital-snapshot.json').read_bytes())
    baseline = json.loads(gzip.decompress((ROOT/'trade-balance-result.json.gz').read_bytes()))
    expected = json.loads(gzip.decompress((ROOT/'trade-balance-mean-result.json.gz').read_bytes()))
    assert run(ROOT/'trade-balance-mean-plan.json') == expected
    assert expected['decision'] == 'stop_candidate_no_adoption'
    assert expected['summary']['statuses'] == {'evaluated': 30, 'not_yet_observable': 15}
    for model in ('proportional_revenue', 'repeat_balance'):
        assert expected['summary']['models'][model]['gross_component_mae_pct_revenue'] == baseline['summary']['models'][model]['gross_component_mae_pct_revenue']
    # 直接从八个季度源位交叉乘积复算首个预测，不调用比例或TTM助手。
    records = {r['period']: r for r in source['records'] if r['code'] == '300866'}
    raw_value = lambda period, field: D.from_float(struct.unpack('<f', struct.pack('<I', records[period]['bits'][field]))[0])
    current_revenue = sum((raw_value(p, 'FN230') for p in ('2022-09-30', '2022-12-31', '2023-03-31', '2023-06-30')), D(0))
    prior_revenue = sum((raw_value(p, 'FN230') for p in ('2021-09-30', '2021-12-31', '2022-03-31', '2022-06-30')), D(0))
    first = expected['results'][0]
    for field in ('FN11', 'FN17', 'FN44'):
        predicted = D(first['predicted_revenue_cny'])*(raw_value('2023-06-30', field)*prior_revenue+raw_value('2022-06-30', field)*current_revenue)/(2*current_revenue*prior_revenue)
        assert abs(predicted-D(first['candidate_balances_cny'][field])) < D('0.00000001')
    valid = [r for r in expected['results'] if r['status'] == 'evaluated']
    error = sum((sum((abs(D(r['errors_cny']['mean_two_intensities'][f])) for f in ('FN11', 'FN17', 'FN44')), D(0))/D(r['actual_revenue_cny']) for r in valid), D(0))*100/len(valid)
    assert float(error) == expected['summary']['models']['mean_two_intensities']['gross_component_mae_pct_revenue']
    for mode in ('missing', 'duplicate', 'tamper'):
        changed = deepcopy(source)
        target = next(r for r in changed['records'] if (r['code'], r['period']) == ('300866', '2026-06-30'))
        if mode == 'missing':
            changed['records'].remove(target)
        elif mode == 'duplicate':
            changed['records'].append(deepcopy(target))
        else:
            target['bits']['FN17'] ^= 1
        result = evaluate(plan, changed, baseline)
        assert [r.get('candidate_balances_cny') for r in result] == [r.get('candidate_balances_cny') for r in expected['results']]
        assert sum(r['status'] == 'blocked_actual' for r in result) == 3
        assert len(result) == 45
    late = deepcopy(source)
    prior = next(r for r in late['records'] if (r['code'], r['period']) == ('300866', '2022-06-30'))
    prior['bits']['FN314'] = struct.unpack('<I', struct.pack('<f', 20230902))[0]
    result = evaluate(plan, late, baseline)
    blocked = [r for r in result if r['status'] == 'blocked_candidate_input']
    assert len(blocked) == 3 and all(r['code'] == '300866' and r['origin'] == '2023-06-30' for r in blocked)
    assert all('candidate_balances_cny' not in r for r in blocked)
    assert all(r == old for r, old in zip(result, expected['results']) if r not in blocked)
    # 从真实文件复制，改变源字节；输入摘要必须先拒绝，不发布新研究结果。
    (tmp_path/'trade-balance-mean-plan.json').write_bytes((ROOT/'trade-balance-mean-plan.json').read_bytes())
    for name in plan['inputs']:
        raw = (ROOT/name).read_bytes()
        (tmp_path/name).write_bytes(raw+b' ' if name == 'capital-snapshot.json' else raw)
    try:
        run(tmp_path/'trade-balance-mean-plan.json')
    except ValueError as error:
        assert 'input digest differs' in str(error)
    else:
        raise AssertionError('changed source accepted')
