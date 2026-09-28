"""只改变逐年收入增长路径，经共享引擎比较资本需求与条件估值。"""
import argparse
import hashlib
import json
import math
from pathlib import Path

from engine.data_dictionary import CompanyValuationInput, ForecastYear
from engine.orchestrator import run_full_valuation
from engine.module_4_dcf import _tax_path
from tools.review_native_policy import review_report
from tools.check_native_sqlite import value_digest


def compare(baseline, scenarios):
    review_report(baseline)  # 保存报告必须能重放；重建旧方法请走显式重建入口。
    inputs = CompanyValuationInput.model_validate(baseline['inputs'])
    a, macro = inputs.valuation_assumptions, inputs.macro_inputs
    n = a.projection_years or 10
    revenues = baseline['dcf']['revenue_projections']
    if any(not math.isfinite(v) or v <= 0 for v in revenues):
        raise ValueError('positive baseline revenues required for fixed margin comparison')
    margins = [e/r for e,r in zip(baseline['dcf']['ebit_projections'],revenues,strict=True)]
    if a.annual_forecast is not None:
        taxes = [r.tax for r in a.annual_forecast]
    else:
        effective = a.effective_tax_rate_override_years_1_5
        if effective is None:
            effective = macro.tax_rate_effective if macro.tax_rate_effective is not None else macro.tax_rate_marginal
        taxes, _ = _tax_path(effective,macro.tax_rate_marginal,a.override_tax_convergence,a.high_growth_years or 5,n)
    if not scenarios or len({s['name'] for s in scenarios}) != len(scenarios):
        raise ValueError('nonempty uniquely named scenarios required')
    # 显式逐年路径可能改变终值税率/利润率，先证明原路径等价，避免伪单因素比较。
    replay = inputs.model_copy(deep=True)
    prior = baseline['ltm_financials']['revenues']
    growth = []
    for revenue in revenues:
        growth.append(revenue / prior - 1)
        prior = revenue
    replay.valuation_assumptions.annual_forecast = [
        ForecastYear(growth=g, margin=m, tax=t) for g, m, t in zip(growth, margins, taxes, strict=True)]
    reproduced = run_full_valuation(replay)
    for field, expected in baseline['dcf'].items():
        actual = reproduced.dcf.model_dump(mode='json')[field]
        if isinstance(expected, (int, float)) and expected is not None:
            equal = math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8)
        elif isinstance(expected, list):
            equal = len(actual) == len(expected) and all(
                math.isclose(x, y, rel_tol=1e-10, abs_tol=1e-8) for x, y in zip(actual, expected))
        else:
            equal = actual == expected
        if not equal:
            raise ValueError(f'annual path changes baseline semantics: {field}')
    results = []
    for scenario in scenarios:
        if not scenario.get('reason') or len(scenario['growth']) != n:
            raise ValueError('growth path length and reason required')
        changed = inputs.model_copy(deep=True)
        changed.valuation_assumptions.annual_forecast = [
            ForecastYear(growth=g,margin=m,tax=t) for g,m,t in zip(scenario['growth'],margins,taxes,strict=True)]
        report = run_full_valuation(changed)
        dcf = report.dcf
        if report.cost_of_capital.model_dump() != baseline['cost_of_capital']:
            raise ValueError('growth-only comparison changed WACC')
        # 此处只增加逐年路径；其余输入（含衰减期、税、资本倍率、桥接）必须原样。
        checked = changed.model_dump(mode='json')
        checked['valuation_assumptions']['annual_forecast'] = a.model_dump(mode='json')['annual_forecast']
        if checked != inputs.model_dump(mode='json'):
            raise ValueError('growth-only comparison changed other inputs')
        results.append(dict(name=scenario['name'],reason=scenario['reason'],growth=scenario['growth'],
            inputs=changed.model_dump(mode='json'),final=report.final.model_dump(mode='json'),
            dcf=dcf.model_dump(mode='json'),first_five_reinvestment_million_cny=sum(dcf.reinvestment_projections[:5]),
            terminal_value_share=dcf.pv_terminal_value/dcf.value_of_operating_assets if dcf.value_of_operating_assets else None))
    result = dict(contract='native-growth-comparison-v1',baseline=baseline,scenarios=results,
        automatic_adoption=False,boundary='增长路径条件比较，不认证持续期；其余经济假设沿用基线，不给情景分配概率',
        implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    result['run_id'] = value_digest(result)
    return result


if __name__ == '__main__':
    from data_sources.paths import workspace_path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    output=args.output.resolve()
    if not output.is_relative_to(workspace_path('derived').resolve()) or output.exists():
        raise ValueError('new output under workspace/derived required')
    request=json.loads(args.request.read_bytes())
    result=compare(request['baseline'],request['scenarios'])
    result['request_sha256']=hashlib.sha256(args.request.read_bytes()).hexdigest()
    result.pop('run_id')
    result['run_id']=value_digest(result)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps(dict(run_id=result['run_id'],scenarios=len(result['scenarios'])),ensure_ascii=False))
