"""冻结的利息源零训练过滤开发复验；完整评价分母保留。"""
from collections import defaultdict
import copy
import hashlib
import json

from tools import diagnose_tdx_training_exclusions as prior
from tools.backtest_tdx_history import value

DIRECTORY = prior.DIRECTORY
PROTOCOL_SHA = '3608de611c2d5d3721f879cde78ce94c18425ae8dd1a289ddd1fd50cd5e9e365'


def load_inputs():
    raw = (DIRECTORY/'training-quality-protocol.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROTOCOL_SHA:
        raise ValueError('frozen quality protocol differs')
    p = json.loads(raw)
    raw = (prior.joint.ROOT/p['snapshot']['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != p['snapshot']['sha256']:
        raise ValueError('frozen source differs')
    if p['source_result']['sha256'] != prior.SOURCE_SHA:
        raise ValueError('parent result differs')
    return p, prior.load_result(), json.loads(raw)


def study(p, result, source):
    filtered = copy.deepcopy(result); index = defaultdict(list); removed = {}
    for r in source['records']:
        index[r['code'],r['period']].append(r)
    for origin,pool in filtered['training'].items():
        admitted = []; removed[origin] = []
        for entry in pool['admitted']:
            zeros = []
            refs = entry['prior']['current']['source_inputs'] + entry['realized']['source_inputs']
            for ref in refs:
                if ref['field'] not in ('FN305','FN306'):
                    continue
                found = index[entry['code'],ref['period']]
                if len(found) != 1:
                    raise ValueError('training source identity differs')
                if value(found[0],ref['field']) == 0:
                    zeros.append(dict(period=ref['period'],field=ref['field']))
            if zeros:
                removed[origin].append(dict(code=entry['code'],zero_inputs=zeros))
            else:
                admitted.append(entry)
        pool['admitted'] = admitted
    rows, _ = prior.refit(filtered, ())
    models = prior.joint.MODELS; candidate = prior.joint.COMBINED; base = prior.joint.ZERO
    summary = prior.metrics(rows,models)
    by_origin = {o:prior.metrics([r for r in rows if r['origin']==o],models) for o in removed}
    valid = [r for r in rows if r['status']=='evaluated']; g=p['gates']
    main='ebit_mae_pct_actual_revenue'; wape='ebit_wape_pct'
    current=summary['models'][candidate]; baseline=summary['models'][base]
    losses={m:defaultdict(float) for m in (candidate,base)}
    for r in valid:
        for m in losses:
            losses[m][r['code']]+=abs(r['errors'][m]['ebit_error_pct_actual_revenue'])
    totals={m:sum(v.values()) for m,v in losses.items()}
    checks=dict(minimum_pairs=len(valid)>=g['minimum_pairs'],minimum_companies=len(losses[base])>=g['minimum_companies'],
        each_origin_minimum=all(m['models'][candidate]['ebit_n']>=g['minimum_each_origin'] for m in by_origin.values()),
        baseline_retention=len(valid)/sum(r['status']=='evaluated' for r in result['results'])>=g['minimum_baseline_retention'],
        main_improvement=current[main] <= baseline[main]*(1-g['minimum_main_improvement_vs_zero']),
        wape_nonworse=current[wape]<=baseline[wape],
        original_comparators_nonworse=all(current[k]<=result['summary']['models'][m][k]
            for m in (candidate,prior.joint.CALIBRATED) for k in (main,wape)),
        each_origin_nonworse=all(m['models'][candidate][main]<=m['models'][base][main]*g['maximum_each_origin_main_ratio_vs_zero'] for m in by_origin.values()),
        leave_one_evaluation_company_out_nonworse=all(totals[candidate]-losses[candidate][c]<=totals[base]-losses[base][c] for c in losses[base]))
    return dict(protocol_sha256=PROTOCOL_SHA,source_result_sha256=prior.SOURCE_SHA,removed_training=removed,
        remaining_training={o:len(v['admitted']) for o,v in filtered['training'].items()},
        summary=summary,by_origin=by_origin,decision=dict(passed=all(checks.values()),checks=checks),results=rows,
        boundary=p['boundary'])


if __name__ == '__main__':
    print(json.dumps(study(*load_inputs()),ensure_ascii=False,indent=2,allow_nan=False))
