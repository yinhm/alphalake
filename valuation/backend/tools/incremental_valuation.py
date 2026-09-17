"""复用批次入口：先核验当前输入，只对经济输入/证据/引擎变化重新估值。"""
from datetime import datetime
import hashlib
from pathlib import Path

from api.alphalake import ENGINE_REVISION, evaluate
from data_sources.alphalake import AlphaLakeRequest, build_inputs, content_hash
from engine.data_dictionary import CompanyValuationInput
from tools.compare_valuations import compare_runs, load_run


def input_signature(request, inputs):
    raw = request.model_dump(mode='json')
    raw['data'].pop('information_as_of', None)
    for name in ('wacc_binding', 'capital_binding'):
        if raw.get(name):
            raw[name]['references'].pop('information_as_of', None)
            raw[name]['references'].pop('recorded_cutoff', None)
    resolved = inputs.model_dump(mode='json')
    resolved.pop('company_metrics', None)  # 共享引擎运行后生成的诊断，不是适配器输入。
    if resolved.get('prepared_ttm'):
        resolved['prepared_ttm'].pop('information_as_of', None)
        # 来源和政策完整保留在raw；不让包含查询时点的派生哈希造成无效重算。
        resolved['prepared_ttm'].pop('provenance', None)
    return content_hash(dict(request=raw, resolved_inputs=resolved))


def evaluate_changed(request, previous, run_directory):
    # 即使内容未变也检查当前政策有效期、源冲突、缺项和参考时效。
    inputs, _ = build_inputs(request)
    signature = input_signature(request, inputs)
    prior = None
    if previous:
        prior, receipt = load_run(run_directory, previous['run_id'])
        if receipt['sha256'] != previous['run_file_sha256']:
            raise ValueError('previous successful run file changed')
        if prior['request']['data']['code'] != request.data.code:
            raise ValueError('previous successful run security differs')
        prior_signature = input_signature(AlphaLakeRequest.model_validate(prior['request']),
                                          CompanyValuationInput.model_validate(prior['inputs']))
        if previous['signature'] != prior_signature:
            raise ValueError('previous successful input signature differs')
        if (prior_signature == signature and prior['engine_revision'] == ENGINE_REVISION
                and request.data.information_as_of >= datetime.fromisoformat(prior['request']['data']['information_as_of'])):
            return dict(prior, refresh=dict(action='reused_unchanged', checked_as_of=request.data.information_as_of.isoformat(),
                last_success=previous, comparison=None))
    result = evaluate(request)
    path = Path(run_directory)/(result['run_id']+'.json')
    success = dict(run_id=result['run_id'], signature=signature,
                   run_file_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    comparison = None
    if prior is not None:
        try:
            comparison = compare_runs(prior, result)
        except ValueError as error:
            comparison = dict(status='prior_report_not_replayable', reason=str(error),
                              before_run_id=prior['run_id'], after_run_id=result['run_id'])
    return dict(result, refresh=dict(action='recalculated' if prior else 'initial_valuation',
        checked_as_of=request.data.information_as_of.isoformat(), last_success=success, comparison=comparison))


def run_incremental_batch(readiness, policy, export, previous, run_directory):
    from tools.batch_valuate_alphalake import run_batch
    if previous is not None and (previous.get('contract_version') != 'alphalake-batch-v1'
            or not isinstance(previous.get('companies'), list)
            or previous.get('universe_count') != len(previous['companies'])):
        raise ValueError('invalid previous batch contract or universe count')
    records = (previous or {}).get('companies', [])
    old = {}
    for row in records:
        key = (row.get('code'), row['instrument_id'])
        if key in old:
            raise ValueError('duplicate previous company identity')
        old[key] = row.get('last_success')
    current_ids = {s[2:]: c['instrument_id'] for c in readiness['companies'] for s in c.get('symbols') or []}
    outcomes = {}

    def evaluator(request):
        key = (request.data.code, current_ids[request.data.code])
        result = evaluate_changed(request, old.get(key), run_directory)
        outcomes[key] = result.pop('refresh')
        return result

    result = run_batch(readiness, policy, export, evaluator=evaluator)
    counts = dict(computable=0, conditional_valuations=0, requires_human_review=0, rejected=0, execution_failed=0)
    for row in result['companies']:
        key = (row.get('code'), row['instrument_id'])
        refresh = outcomes.get(key)
        if refresh:
            row['refresh'] = {k:v for k,v in refresh.items() if k != 'last_success'}
            row['last_success'] = refresh['last_success']
            counts['computable'] += 1
            counts['conditional_valuations'] += row['status'] != 'illustrative_enterprise_value_only'
            # 成功计算仍是条件政策；不以可计算宣称审核完备。
            row['human_review_required'] = True
            counts['requires_human_review'] += 1
        else:
            row['last_success'] = old.get(key)
            row['refresh'] = dict(action='blocked_current_inputs', comparison=None)
            row['human_review_required'] = row['status'] != 'failed_execution'
            counts['requires_human_review'] += row['human_review_required']
            counts['rejected'] += row['status'].startswith('rejected')
            counts['execution_failed'] += row['status'] == 'failed_execution'
    result['automation_counts'] = counts
    result['automation_boundary'] = 'counts overlap: computable/conditional/review are distinct dimensions; blocked attempts retain but do not publish old runs as current valuations'
    return result
