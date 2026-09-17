"""标准请求的公司输入台账及单因素取证优先级；不自动批准新估值政策。"""
import argparse
from copy import deepcopy
import gzip
import json
from pathlib import Path

from api.alphalake import evaluate
from data_sources.alphalake import AlphaLakeRequest, standard_window_reader
from engine.data_dictionary import CompanyValuationInput
from engine.orchestrator import run_full_valuation
from tools.verify_nonfinancial_dcf import verify

ASSET_FIELDS = ('FN9', 'FN19', 'FN25', 'FN430', 'FN431', 'FN433')


def review(request):
    if request.policy.policy_id != 'nonfinancial-history-fcff-v1':
        raise ValueError('input review supports nonfinancial-history-fcff-v1 only')
    baseline = evaluate(request)
    verify(baseline)
    inputs = CompanyValuationInput.model_validate(baseline['inputs'])
    base_value = baseline['report']['final']['value_per_share']
    window, _ = standard_window_reader(request.data)
    windows = {r['field']: r for r in request.data.windows}
    assets = []
    for field in ASSET_FIELDS:
        value = window(field, False)
        item = dict(field=field, value_million_cny=value,
                    status='missing_standard_value' if value is None else 'unreviewed_classification',
                    standard_window=windows.get(field), adopted=False)
        if value is not None:
            if value < 0:
                raise ValueError('negative financial asset requires review: '+field)
            scenario = inputs.model_copy(deep=True)
            scenario.equity_bridge.components['unreviewed_asset_book_sensitivity'] = value
            report = run_full_valuation(scenario)
            # 单资产账面加回仅为取证优先级，不是公允价值上界或已批准桥接。
            item['book_addback_sensitivity'] = dict(
                status='not_adopted_not_fair_value_bound',
                value_per_share=report.final.value_per_share,
                delta_per_share=report.final.value_per_share-base_value,
                operating_value=report.dcf.value_of_operating_assets,
                component_million_cny=value)
        assets.append(item)
    capital = []
    for multiplier in (.75, 1.25):
        changed = request.model_copy(deep=True)
        if changed.capital_binding is not None:
            changed.capital_binding.policy.ratio_multiplier *= multiplier
        else:
            changed.policy.sales_to_capital *= multiplier
        result = evaluate(AlphaLakeRequest.model_validate(changed.model_dump()))
        verify(result)
        dcf = result['report']['dcf']
        capital.append(dict(multiplier=multiplier, run_id=result['run_id'],
            status='not_adopted_not_confidence_interval',
            value_per_share=result['report']['final']['value_per_share'],
            delta_per_share=result['report']['final']['value_per_share']-base_value,
            annual_fcff=dcf['fcff_projections'], annual_reinvestment=dcf['reinvestment_projections']))
    return dict(schema_version='company-input-review-v1', code=request.data.code,
        report_period=request.data.report_period.isoformat(),
        information_as_of=request.data.information_as_of.isoformat(),
        scope='archived_standard_request_not_live_database_or_full_market',
        baseline_run_id=baseline['run_id'], baseline_value_per_share=base_value,
        baseline_annual_fcff=baseline['report']['dcf']['fcff_projections'],
        company_facts=dict(consumed_inputs=baseline['audit']['consumed_inputs'],
                           source_facts=deepcopy(request.data.facts)),
        external_references=dict(capital=baseline['audit'].get('capital_reference'),
                                 wacc=baseline['audit'].get('wacc_reference')),
        assumptions=baseline['audit']['assumptions'],
        missing_or_unreviewed=baseline['method_assessment']['unresolved'],
        financial_assets=assets, capital_efficiency_sensitivity=capital,
        coverage=dict(asset_fields_considered=len(assets),
                      asset_fields_with_standard_values=sum(r['value_million_cny'] is not None for r in assets),
                      asset_fields_approved_for_addback=0),
        boundary='账面加回分项不相加；未核验经营属性、重复计值、限制及公允价值。资本倍率是对称压力测试，不是公司估计。所有情景均不替换原政策。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--check', action='store_true', help='逐字节核对已有台账，不覆盖')
    args = parser.parse_args()
    raw = args.request.read_bytes()
    if args.request.suffix == '.gz':
        raw = gzip.decompress(raw)
    result = review(AlphaLakeRequest.model_validate_json(raw))
    payload = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode()
    if args.output.suffix == '.gz':
        payload = gzip.compress(payload, mtime=0)
    if args.check:
        if args.output.read_bytes() != payload:
            raise ValueError('company input review differs from frozen ledger')
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload)


if __name__ == '__main__':
    main()
