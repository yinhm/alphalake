"""复验真实补充导入、撤销、恢复导出；生成供统一入口显式采用的单公司政策。"""
from copy import deepcopy
import gzip
import json
import os
from pathlib import Path

from data_sources.alphalake import AlphaLakeRequest, content_hash
from tools.incremental_valuation import evaluate_changed
from tools.verify_nonfinancial_dcf import verify
from tools.verify_reviewed_main import bind, ROOT


def request_for(data):
    original = json.loads(gzip.decompress((ROOT/'valuation/research/reviewed-assets-20260917/after-request.json.gz').read_bytes()))
    request = bind(original, data)
    request['policy']['reviewed_at'] = '2026-09-18T13:30:43Z'
    return request


def approve_zeros(request):
    request = deepcopy(request)
    data = request['data']
    notes = [n for n in data['supplements'] if n['item']=='reviewed_asset_disposal_cash_zero']
    assert len(notes)==2
    bindings = []
    for note in notes:
        f, = [f for f in data['facts'] if f['period']==note['period'] and f['field']=='FN114']
        bindings.append(dict(period=note['period'], item=note['item'], import_sha256=note['import_sha256'],
            evidence_sha256=content_hash(note), source_artifact_sha256=f['artifact_sha256']))
    request['policy']['disposal_cash_zeros'] = bindings
    return request


def verify_chain(directory, runs):
    directory, runs = Path(directory), Path(runs)
    os.environ['ALPHALAKE_VALUATION_RUN_DIR'] = str(runs.resolve())
    read = lambda name: json.loads((directory/(name+'.json')).read_text())
    reviewed, revoked, restored = [read('disposal-'+stage+'-export') for stage in ('reviewed','revoked','restored')]
    original = read('disposal-export')
    for data in (reviewed,revoked,restored):
        assert data['facts']==original['facts'] and data['windows']==original['windows']
    baseline = request_for(reviewed)
    request = approve_zeros(baseline)
    first = evaluate_changed(AlphaLakeRequest.model_validate(baseline), None, runs)
    current = evaluate_changed(AlphaLakeRequest.model_validate(request), first['refresh']['last_success'], runs)
    assert current['refresh']['action']=='recalculated'
    assert current['report']==first['report']
    verify(current)
    evidence = current['audit']['company_capital_evidence']
    assert evidence['asset_disposal_cash_million_cny'] is None
    assert evidence['reviewed_asset_disposal_cash_million_cny']==0.01735
    assert abs(evidence['reviewed_cash_capex_after_disposals_million_cny']-319.61693)<1e-10
    assert current['report']['cashflow']['fcff'] is None
    repeated = evaluate_changed(AlphaLakeRequest.model_validate(request),current['refresh']['last_success'],runs)
    assert repeated['refresh']['action']=='reused_unchanged'
    for stage,data in [('revoked',revoked),('restored_old_policy',restored)]:
        bad = dict(request,data=data)
        try:
            evaluate_changed(AlphaLakeRequest.model_validate(bad),current['refresh']['last_success'],runs)
        except ValueError:
            pass
        else:
            raise AssertionError('accepted '+stage)
    recovered_request = approve_zeros(request_for(restored))
    recovery = evaluate_changed(AlphaLakeRequest.model_validate(recovered_request),current['refresh']['last_success'],runs)
    assert recovery['report']==current['report'] and recovery['refresh']['action']=='recalculated'
    # 复原审核仍是新版本，旧运行保留且下一次同输入复用。
    receipt = dict(scope='isolated_standard_plus_reviewed_supplements_not_main_publication',
        code='300866',report_period='2026-06-30',information_as_of=reviewed['information_as_of'],
        standard_disposal_ttm=None, reviewed_disposal_cash_cny='17350',
        reviewed_cash_capex_after_disposals_cny='319616930',
        full_net_reinvestment=None, historical_fcff=None, standard_facts_unchanged=True,
        dcf_report_unchanged=True,value_per_share=current['report']['final']['value_per_share'],
        baseline_run_id=first['run_id'],reviewed_run_id=current['run_id'],restored_run_id=recovery['run_id'],
        replay='reused_unchanged',revoked='rejected',restored_old_policy='rejected',rebound='recalculated')
    policy = dict(policy_version='reviewed-disposal-zero-20260918', review_note='仅显式采用安克两期原文处置现金零，现金分量诊断；原WACC/预测不变。',
        assignments={'300866':{k:v for k,v in recovered_request.items() if k!='data'}})
    return receipt, policy


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    receipt,policy=verify_chain(args.directory,args.directory/'runs')
    for name,value in [('zero-acceptance',receipt),('zero-policy',policy)]:
        (args.directory/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(receipt,ensure_ascii=False,indent=2))
