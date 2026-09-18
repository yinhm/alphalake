"""只读核验已保存的历史规则预测；实际值来自标准TTM，不评价股价或FCFF。"""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

from data_sources.alphalake import Snapshot, MissingInputs, content_hash, standard_window_reader
from api.alphalake import ENGINE_REVISION
from tools.compare_valuations import load_run, replay


def review(run, evaluation_as_of, export):
    """run须先通过load_run/replay；export只接收已到期的同季报告期末日期。"""
    request = run['request']
    if request['policy']['policy_id'] not in ('nonfinancial-history-fcff-v1', 'nonfinancial-history-fcff-calibrated-v1'):
        raise ValueError('only historical nonfinancial forecast scope is supported')
    base = request['data']
    cutoff = datetime.fromisoformat(evaluation_as_of)
    if cutoff.utcoffset() is None or cutoff <= datetime.fromisoformat(base['information_as_of']):
        raise ValueError('evaluation ASOF must be timezone-aware and later than forecast ASOF')
    end = date.fromisoformat(base['report_period'])
    identities = {r['instrument_id'] for r in base['facts']}
    if len(identities) != 1:
        raise ValueError('unique forecast security identity required')
    dcf = run['report']['dcf']
    if len(dcf['revenue_projections']) != 10 or len(dcf['ebit_projections']) != 10:
        raise ValueError('ten annual forecast positions required')
    rows = []
    for year in range(1, 11):
        target = end.replace(year=end.year+year)
        predictions = dict(revenue=dcf['revenue_projections'][year-1], adjusted_ebit=dcf['ebit_projections'][year-1])
        if any(not math.isfinite(v) for v in predictions.values()):
            raise ValueError('nonfinite saved forecast')
        row = dict(horizon=year, target_period=target.isoformat(), status='not_yet_observable',
                   predictions_million_cny=predictions, actual_fcff=None)
        rows.append(row)
        if target > cutoff.astimezone(timezone(timedelta(hours=8))).date():
            continue
        row['status'] = 'blocked_actual'
        try:
            raw = export(target.isoformat())
            row['actual_snapshot_sha256'] = content_hash(raw)
            # 保留本次实际版本，日后更正不覆盖本次评价证据。
            row['actual_snapshot'] = raw
            actual = Snapshot.model_validate(raw)
            if actual.code != base['code'] or actual.report_period != target or actual.information_as_of != cutoff:
                raise ValueError('actual security/period/ASOF differs from requested window')
            if {r['instrument_id'] for r in actual.facts} != identities:
                raise ValueError('actual security identity differs from saved forecast')
            if actual.source_conflicts:
                raise ValueError('actual source conflict requires review')
            window, consumed = standard_window_reader(actual)
            for field in ('financial_business_interest_income', 'financial_business_interest_expense', 'financial_business_fee_expense', 'deposits_and_interbank_placements'):
                value = window(field, False)
                if value is not None and value != 0:
                    raise ValueError('actual financial operations require separate model: '+field)
            metrics = {}
            for name in predictions:
                try:
                    value = (window('revenue') if name == 'revenue' else
                             window('operating_profit_cumulative')+window('interest_expense')-window('interest_income')-window('investment_income')-window('fair_value_change_income')-window('asset_disposal_income'))
                    error = predictions[name]-value
                    metrics[name] = dict(status='evaluated', actual_million_cny=value,
                                         signed_error_million_cny=error, absolute_error_million_cny=abs(error))
                except MissingInputs as exc:
                    metrics[name] = dict(status='blocked_missing_inputs', missing=exc.items)
            revenue = metrics['revenue'].get('actual_million_cny')
            for metric in metrics.values():
                if metric['status'] == 'evaluated':
                    metric['absolute_error_pct_actual_revenue'] = (metric['absolute_error_million_cny']/revenue*100 if revenue is not None and revenue > 0 else None)
            evaluated = sum(m['status'] == 'evaluated' for m in metrics.values())
            row.update(status='evaluated' if evaluated == 2 else 'partial_actual' if evaluated else 'blocked_actual',
                       metrics=metrics, consumed_inputs=consumed)
        except (ValueError, KeyError, TypeError, ArithmeticError, OSError, subprocess.SubprocessError) as exc:
            row['reason'] = str(exc)
    result = dict(contract_version='alphalake-forecast-review-v1', run_id=run['run_id'],
                  reviewer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  current_engine_revision=ENGINE_REVISION,
                  forecast_engine_revision=run['engine_revision'], code=base['code'],
                  forecast_period=base['report_period'], forecast_information_as_of=base['information_as_of'],
                  evaluation_as_of=evaluation_as_of, policy_id=request['policy']['policy_id'],
                  statuses=dict(Counter(r['status'] for r in rows)), results=rows,
                  boundary='仅收入与原政策调整EBIT的同季TTM到期核验；亏损实际值保留，非正实际收入不算百分比。沿用原经营范围假设，未逐期重新核验业务可比性；未核实运行首次创建时间，不冒称当时已留存预测或严格PIT。缺项、冲突、身份变化、未到期保留。FCFF、终值、每股价值准确度均未评价，不自动选择或修改政策。')
    result['review_id'] = content_hash(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('run_id')
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--run-directory', default=os.environ.get('ALPHALAKE_VALUATION_RUN_DIR', str(Path(__file__).resolve().parents[1]/'data/alphalake_runs')))
    parser.add_argument('--alphalake', default=str(Path(__file__).resolve().parents[3]/'alphalake'))
    args = parser.parse_args()
    try:
        run, _ = load_run(args.run_directory, args.run_id)
        replay(run)
        def export(period):
            return json.loads(subprocess.check_output([args.alphalake, 'export-valuation', args.database, run['request']['data']['code'],
                '--period', period, '--as-of', args.as_of], text=True, stderr=subprocess.PIPE, timeout=300))
        result = review(run, args.as_of, export)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, KeyError, TypeError, ArithmeticError, OSError) as exc:
        print(json.dumps(dict(status='rejected', reason=str(exc)), ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
