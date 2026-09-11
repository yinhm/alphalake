"""事后训练剔除敏感性；评价分母不变，不据此批准生产规则。"""
from collections import Counter
import copy
import gzip
import hashlib
import json

from tools import backtest_tdx_zero_calibration as joint
from tools.backtest_tdx_history import error
from tools.backtest_tdx_normalized_margin import metrics

DIRECTORY = joint.ROOT / 'valuation/research/tdx-zero-calibration'
SOURCE_SHA = '9ac4c2357b4212aefcc02230bdfb24d9a3405b4b948d63b17c2c5b880e74d637'


def load_result():
    raw = (DIRECTORY / 'development-result.json.gz').read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA:
        raise ValueError('frozen result differs')
    return json.loads(gzip.decompress(raw))


def refit(result, excluded):
    """只读取既有起点输入及滞后训练池拟合；最后才读评价实际值。"""
    rows = copy.deepcopy(result['results'])
    pools = {}; removed = {}
    for origin, pool in result['training'].items():
        removed[origin] = [x['code'] for x in pool['admitted'] if x['code'] in excluded]
        pools[origin] = {base: [joint.CalibrationObservation(code=x['code'],
            predicted_ebit=x['prior']['forecasts'][base]['ebit'],
            actual_ebit=x['realized']['ebit'], actual_revenue=x['realized']['revenue'])
            for x in pool['admitted'] if x['code'] not in excluded]
            for base in (joint.ZERO, joint.SHORT)}
    for row in rows:
        for base, model in ((joint.ZERO, joint.COMBINED), (joint.SHORT, joint.CALIBRATED)):
            row['forecasts'].pop(model, None)
            if base not in row['forecasts']:
                continue
            forecast = row['forecasts'][base]
            if forecast['ebit'] <= 0:
                row['forecasts'][model] = dict(forecast)
                row['calibration'][model] = dict(status='nonpositive_unscaled')
                continue
            try:
                coefficient = joint.fit(pools[row['origin']][base], row['code'], minimum=30)
                row['calibration'][model] = coefficient
                row['forecasts'][model] = forecast | dict(ebit=forecast['ebit'] * coefficient['multiplier'])
            except ValueError as exc:
                row['calibration'][model] = dict(status='blocked_training', reason=str(exc))
    for row in rows:
        if 'actual' in row:
            row['errors'] = {m: error(f, row['actual']) for m, f in row['forecasts'].items()}
            row['status'] = 'evaluated' if len(row['forecasts']) == len(joint.MODELS) else 'calibration_blocked'
    return rows, removed


def diagnose(result):
    cases = {}
    for excluded in ((), ('600751',), ('688670',), ('600751', '688670')):
        rows, removed = refit(result, excluded)
        if not excluded and rows != result['results']:
            raise ValueError('unfiltered replay differs')
        cases['+'.join(excluded) or 'none'] = dict(excluded_training_codes=excluded,
            removed_training_by_origin=removed, candidates=len(rows),
            statuses=dict(Counter(r['status'] for r in rows)), summary=metrics(rows, joint.MODELS),
            by_origin={o: metrics([r for r in rows if r['origin'] == o], joint.MODELS)
                       for o in result['training']},
            changed_forecasts={m: sum(a['forecasts'].get(m) != b['forecasts'].get(m)
                for a, b in zip(rows, result['results'])) for m in (joint.COMBINED, joint.CALIBRATED)})
    return dict(source_result_sha256=SOURCE_SHA, cases=cases,
        boundary='posthoc_training_exclusion_sensitivity_not_new_validation; '
        'all_evaluation_companies_and_losses_retained; own_company_excluded_from_fit; '
        'no_production_change; no_source_override; no_adoption_gate')


if __name__ == '__main__':
    print(json.dumps(diagnose(load_result()), ensure_ascii=False, indent=2, allow_nan=False))
