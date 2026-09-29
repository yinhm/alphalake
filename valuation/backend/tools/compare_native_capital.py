"""对已核验统一情景比较终值过渡或公司历史资本代理；不改变经营、折现或终值政策。"""
import argparse
import hashlib
import json
import math
from pathlib import Path
from urllib.request import Request, urlopen

from engine.data_dictionary import CompanyValuationInput
from engine.orchestrator import run_full_valuation
from tools.check_native_sqlite import value_digest
from tools.review_native_policy import review_directory, review_report

POLICY = dict(version='native-capital-transition-v1', unchanged_years=5, end_year=10,
    interpolation='linear_capital_per_revenue', terminal_basis='same_existing_terminal_reinvestment_and_lag',
    automatic_adoption=False, boundary='五年过渡为固定分析情景，不是公司新增资本效率事实；终值ROIC/WACC及经营假设不变')


HISTORY_POLICY = dict(version='native-company-capital-v2', history_years=3,
    aggregation='sum_revenue_divided_by_sum_research_adjusted_capital_including_minority',
    equity_basis='parent_book_equity_plus_book_minority_interests',
    application='constant_ratio_for_explicit_forecast_only', automatic_adoption=False,
    boundary='公司三年存量倍率仅为显式预测代理；现金、债务及投资范围未闭合；少数股权按账面额配套合并收入，不代表新增投资效率或已批准预测；终值保持原政策')


def compare(baseline, basis='terminal-transition'):
    if basis not in ('terminal-transition', 'company-history'):
        raise ValueError('unknown capital comparison basis')
    before = review_report(baseline)
    original = CompanyValuationInput.model_validate(baseline['inputs'])
    a = original.valuation_assumptions
    if a.projection_years != 10 or a.annual_sales_to_capital is not None:
        return dict(status='outside_policy_scope', reason='requires ten-year two-stage capital baseline')
    policy = POLICY if basis == 'terminal-transition' else HISTORY_POLICY
    evidence = None
    if basis == 'terminal-transition':
        target = before['economic_checks']['terminal_transition']['equivalent_sales_to_capital']
        if target is None or not math.isfinite(target) or target <= 0:
            return dict(status='outside_policy_scope', reason='positive terminal investment and equivalent capital ratio required')
        ratios = [row['capital_funding']['sales_to_capital'] for row in before['forecast']]
        start = ratios[4]
        ratios[5:] = [1 / ((1-i/5)/start + (i/5)/target) for i in range(1,6)]
    else:
        from datetime import date
        year = date.fromisoformat(original.period_date_10k[:10]).year
        years = list(range(year-2, year+1))
        history = before['sustainability_evidence']
        rows = [r for r in history['annual_rows'] if r['year'] in years]
        evidence = dict(required_years=years, rows=rows, missing=[], ratio=None)
        if original.adjustment_inputs.has_operating_leases or original.prepared_ttm is not None:
            evidence['missing'].append('historical_capital_requires_matching_lease_or_prepared_ttm_scope')
        if sorted(r['year'] for r in rows) != years:
            evidence['missing'].append('three_consecutive_unique_capital_years_required')
        raw = {r.fiscal_year:r for r in original.raw_financials}
        capital_rows = []
        for row in rows:
            revenue, ratio = raw[row['year']].revenues, row['research_adjusted_sales_to_capital']
            if any(v is None or not math.isfinite(v) or v <= 0 for v in (revenue, ratio)):
                evidence['missing'].append(dict(year=row['year'], reason='positive_revenue_and_adjusted_capital_required'))
                continue
            minority = raw[row['year']].minority_interests
            if minority is None or not math.isfinite(minority):
                evidence['missing'].append(dict(year=row['year'], reason='book_minority_equity_required'))
                continue
            capital = revenue/ratio + minority
            capital_rows.append(dict(year=row['year'], revenue_million_cny=revenue,
                input_capital_million_cny=revenue/ratio, minority_equity_million_cny=minority,
                consolidated_capital_million_cny=capital))
            if not math.isfinite(capital) or capital <= 0:
                evidence['missing'].append(dict(year=row['year'], reason='positive_consolidated_capital_required'))
        evidence['capital_scope_bridge'] = capital_rows
        if evidence['missing']:
            return dict(status='outside_policy_scope', policy=policy, reason='company_history_basis_incomplete', evidence=evidence)
        total_revenue = sum(raw[y].revenues for y in years)
        total_capital = sum(r['consolidated_capital_million_cny'] for r in capital_rows)
        ratio = total_revenue/total_capital
        evidence.update(total_revenue_million_cny=total_revenue, total_capital_million_cny=total_capital, ratio=ratio)
        ratios = [ratio]*a.projection_years
    changed = original.model_copy(deep=True)
    changed.valuation_assumptions.annual_sales_to_capital = ratios
    report = run_full_valuation(changed)
    candidate = dict(inputs=changed.model_dump(mode='json'), **{key:getattr(report,key).model_dump(mode='json')
        for key in ('ltm_financials','adjusted','cost_of_capital','cashflow','dcf','final')})
    for key in ('ltm_financials','adjusted','cost_of_capital','cashflow'):
        if candidate[key] != baseline[key]:
            raise ValueError('capital-only scenario changed '+key)
    for key in ('revenue_projections','ebit_projections','discount_factors','terminal_value_firm','pv_terminal_value'):
        if candidate['dcf'][key] != baseline['dcf'][key]:
            raise ValueError('capital-only scenario changed '+key)
    restored = changed.model_copy(deep=True)
    restored.valuation_assumptions.annual_sales_to_capital = None
    if restored != original:
        raise ValueError('capital-only scenario changed other inputs')
    after = review_report(candidate)
    if basis == 'terminal-transition' and after['economic_checks']['terminal_transition']['requires_capital_transition_basis']:
        raise ValueError('terminal reinvestment rules do not meet')
    investment_delta = [new-old for new,old in zip(report.dcf.reinvestment_projections,baseline['dcf']['reinvestment_projections'],strict=True)]
    pv_delta = -sum(delta*df for delta,df in zip(investment_delta,report.dcf.discount_factors,strict=True))
    operating_delta = report.dcf.value_of_operating_assets-baseline['dcf']['value_of_operating_assets']
    if not math.isclose(pv_delta,report.dcf.pv_cash_flows_sum-baseline['dcf']['pv_cash_flows_sum'],rel_tol=1e-10,abs_tol=1e-7):
        raise ValueError('reinvestment PV does not reconcile operating value change')
    return dict(status='calculated_conditional', policy=policy, evidence=evidence, baseline=baseline, candidate=candidate,
        annual_sales_to_capital=ratios, annual_reinvestment_change_million_cny=investment_delta,
        going_concern_value_change_million_cny=pv_delta, operating_value_change_million_cny=operating_delta,
        value_per_share_change=report.final.value_per_share-baseline['final']['value_per_share'],
        before_terminal=before['economic_checks']['terminal_transition'],
        after_terminal=after['economic_checks']['terminal_transition'], automatic_adoption=False)


def run(source, output, web=None, basis='terminal-transition'):
    if basis not in ('terminal-transition', 'company-history'):
        raise ValueError('unknown capital comparison basis')
    from data_sources.paths import workspace_path
    from api.alphalake import ENGINE_REVISION
    output = output.resolve()
    if output.exists() or not output.is_relative_to(workspace_path('derived').resolve()):
        raise ValueError('new output under workspace/derived required')
    verified = review_directory(source)
    results = []
    for company in verified['results']:
        row = dict(ticker=company['ticker'], source_status=company['source_status'],
            reason=company.get('reason'), selection=company.get('assumption_selection'), scenarios={})
        for name, reviewed in company.get('variants', {}).items():
            if not name.startswith('scenario_') or reviewed.get('status') != 'requires_analyst_judgment':
                continue
            path = source/(company['ticker'].replace(':','-')+'-'+name+'-result.json')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != verified['source_files'][path.name]:
                raise ValueError('source changed after review')
            result = compare(json.loads(raw), basis)
            if web and result['status'] == 'calculated_conditional':
                request = Request(web.rstrip('/')+'/api/valuation', data=json.dumps(dict(inputs=result['candidate']['inputs'])).encode(),headers={'Content-Type':'application/json'})
                with urlopen(request,timeout=60) as response:
                    body = json.load(response)
                if body.get('reference_snapshot') != result['baseline'].get('reference_snapshot'):
                    raise ValueError('live reference snapshot mismatch')
                for key in result['candidate']:
                    if body[key] != result['candidate'][key]:
                        raise ValueError('live capital scenario mismatch: '+key)
                result['live_verified'] = True
            row['scenarios'][name] = result
        results.append(row)
    result = dict(contract='native-capital-comparison-v1', policy=POLICY if basis == 'terminal-transition' else HISTORY_POLICY, companies=len(results), results=results,
        source_protocol=verified['source_protocol'], source_files=verified['source_files'], engine_revision=ENGINE_REVISION,
        implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    result['run_id'] = value_digest(result)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.write('\n')
    return dict(run_id=result['run_id'],companies=len(results),scenarios=sum(len(r['scenarios']) for r in results))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--web')
    parser.add_argument('--basis', choices=['terminal-transition','company-history'], default='terminal-transition')
    args = parser.parse_args()
    print(json.dumps(run(args.source,args.output,args.web,args.basis),ensure_ascii=False))
