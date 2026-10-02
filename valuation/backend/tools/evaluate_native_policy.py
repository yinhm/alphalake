"""从原生SQLite准备显式人民币政策，经既有API验收；不修改网页默认或财务事实。"""
import argparse
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import math
import os
import re
from pathlib import Path
import sqlite3
from urllib.request import Request, urlopen

from data_sources.alphalake_wacc import WACCBinding, resolve_wacc
from engine.data_dictionary import CompanyValuationInput
from tools.check_native_sqlite import value_digest
from tools.compare_valuations import changes


def prepare(baseline, references, recipe, metadata, revenue_facts):
    inputs = CompanyValuationInput.model_validate(baseline['inputs'])
    if inputs.reporting_currency != 'CNY' or inputs.stock_price_currency != 'CNY' or not inputs.ticker.startswith(('SHSE:', 'SZSE:')):
        raise ValueError('CNY SH/SZ company required')
    if baseline['reference_snapshot']['id'] != metadata['reference_snapshot_id']:
        raise ValueError('baseline/reference publication mismatch')
    if max(filter(None,(inputs.period_date_10k,inputs.period_date_10q)))[:10] != metadata['report_period']:
        raise ValueError('financial report period mismatch')
    financial_cutoff = datetime.fromisoformat(metadata['information_as_of'])
    reference_cutoff = datetime.fromisoformat(references['information_as_of'])
    if reference_cutoff < financial_cutoff:
        raise ValueError('reference cutoff precedes financial information cutoff')
    if set(recipe) != {'version','wacc','forecast','boundaries'}:
        raise ValueError('explicit native policy recipe required')
    # 单行业、全中国暴露和信用档均是显式情景，不写回公司分类或评级。
    from tools.audit_native_coverage import FINANCIAL_INDUSTRIES
    if inputs.industry_data.industry_name in FINANCIAL_INDUSTRIES:
        raise ValueError('financial industry outside native policy scope')
    policy = deepcopy(recipe['wacc'])
    industry = inputs.industry_data.industry_name
    policy.update(code=inputs.ticker.split(':')[1],report_period=metadata['report_period'],scope='consolidated',
                  industries=[dict(industry=industry,weight=1,reason='公司来源分类；Global行业Beta/D/E显式代理，非原生US默认')])
    binding = WACCBinding.model_validate(dict(references=references,policy=policy))
    if binding.policy.synthetic_debt or binding.policy.market:
        raise ValueError('native policy has no reviewed gross-interest/operating-capital bridge')
    components, audit = resolve_wacc(binding,policy['code'],date.fromisoformat(metadata['report_period']),reference_cutoff,scope='consolidated')
    wacc = audit['result']['wacc']
    # 只测折现率：保留原预测、终值增长和ROIC；这不是完整人民币政策。
    discount = inputs.model_copy(deep=True)
    discount.methodology_choices.cost_of_capital_approach='reference_snapshot'
    discount.methodology_choices.reference_capital_inputs=components
    discount.valuation_assumptions.cost_of_capital_stable_override=wacc
    base_assumptions=inputs.valuation_assumptions
    base_terminal_wacc=base_assumptions.cost_of_capital_stable_override
    if base_terminal_wacc is None:
        base_terminal_wacc=inputs.macro_inputs.risk_free_rate+inputs.macro_inputs.equity_risk_premium
    discount.valuation_assumptions.roic_stable_override=(base_assumptions.roic_stable_override
        if base_assumptions.roic_stable_override is not None else base_terminal_wacc)
    variants={'discount_only':discount}
    forecast=recipe['forecast']
    if forecast != dict(growth='three_year_revenue_cagr',margin='hold_adjusted_ttm',capital='us_industry_historical_proxy',terminal_growth_cap=.02,terminal_roic='wacc'):
        raise ValueError('unreviewed forecast recipe')
    issues=[]; evidence={}
    try:
        year=date.fromisoformat(inputs.period_date_10k[:10]).year
        annual={}
        for fact in revenue_facts:
            period=date.fromisoformat(fact['period'])
            if not year-3<=period.year<=year or (period.month,period.day)!=(12,31):
                continue
            if (fact['unit'],fact['period_type'],fact['statement_scope'])!=('CNY','FY','provider_default') or period.year in annual:
                raise ValueError('ambiguous annual revenue unit/period/scope')
            amount=Decimal(fact['value'])
            if not amount.is_finite() or amount<=0:
                raise ValueError('positive annual revenue required')
            annual[period.year]=dict(year=period.year,revenue=float(amount/Decimal(1000000)),source=fact)
        if set(annual)!=set(range(year-3,year+1)):
            raise ValueError('four consecutive positive annual revenues required; no compressed history')
        history=[annual[y] for y in sorted(annual)]
        growth=(history[-1]['revenue']/history[0]['revenue'])**(1/3)-1
        if not -1<growth<=1:
            raise ValueError('historical growth outside explicit engine bounds; not clipped')
        ratio=inputs.industry_data.sales_to_capital
        if ratio is None or not math.isfinite(ratio) or ratio<=0:
            raise ValueError('positive industry sales/capital reference required')
        revenue=baseline['ltm_financials']['revenues']
        if revenue is None or revenue<=0:
            raise ValueError('positive TTM revenue required')
        margin=baseline['adjusted']['adjusted_ebit']/revenue
        if not math.isfinite(margin) or not -1<=margin<=1:
            raise ValueError('adjusted margin outside engine bounds; not clipped')
        joint=discount.model_copy(deep=True)
        joint.macro_inputs.risk_free_rate=components.risk_free_rate
        joint.macro_inputs.equity_risk_premium=components.mature_market_erp
        terminal=min(forecast['terminal_growth_cap'],components.risk_free_rate)
        if terminal<0 or wacc<=terminal:
            raise ValueError('invalid terminal growth/discount boundary')
        a=joint.valuation_assumptions
        a.revenue_growth_next_year=a.revenue_growth_years_2_5=growth
        a.operating_margin_next_year=a.target_operating_margin=margin
        a.sales_to_capital_high=a.sales_to_capital_stable=ratio
        a.stable_growth_rate=terminal
        a.roic_stable_override=wacc
        variants['joint_candidate']=joint
        evidence=dict(annual_revenue=history,growth=growth,
                      adjusted_ttm_margin=margin,industry=industry,capital_region='US',sales_to_capital=ratio,
                      terminal_growth=terminal,terminal_roic=wacc)
    except ValueError as error:
        issues.append(str(error))
    payloads={k:dict(inputs=CompanyValuationInput.model_validate(v.model_dump()).model_dump(mode='json')) for k,v in variants.items()}
    return payloads,dict(version=recipe['version'],status='explicit_candidates_not_approved_forecasts',
        financial_cutoff=metadata['information_as_of'],reference_cutoff=references['information_as_of'],
        wacc=audit,wacc_binding=binding.model_dump(mode='json'),forecast=evidence,forecast_missing=issues,
        boundaries=recipe['boundaries'],changes={k:list(changes(baseline['inputs'],v['inputs'])) for k,v in payloads.items()})


def evaluate(database, references, recipe, tickers, output, web=None, selection_policy=None):
    if not tickers or len(set(tickers))!=len(tickers) or any(not re.fullmatch(r"(?:SHSE|SZSE):[0-9]{6}",t) for t in tickers):
        raise ValueError("unique SH/SZ tickers required")
    from fastapi.testclient import TestClient
    from api.main import app
    database=database.resolve(strict=True)
    if output.exists():
        raise ValueError('output exists; keep immutable prior run')
    output.mkdir(parents=True)
    os.environ['US_CN_HK_DB_PATH']=str(database)
    with database.open('rb') as stream: snapshot_hash=hashlib.file_digest(stream,'sha256').hexdigest()
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as conn:
        metadata=dict(conn.execute('SELECT key,value FROM metadata'))
        conn.row_factory=sqlite3.Row
        from tools.review_native_policy import load_annual_evidence
        annual_evidence=load_annual_evidence(conn,tickers)
        capital_references=[dict(r) for r in conn.execute("SELECT v.*,r.available_at,r.sha256,r.source_locator AS release_locator FROM reference_value v JOIN reference_release r USING(release_id) WHERE metric='sales_to_invested_capital_ltm'")]
        histories={t:[dict(r) for r in conn.execute("SELECT period,value,unit,period_type,statement_scope FROM standard_facts WHERE ticker=? AND field='revenue_cumulative' AND period LIKE '%12-31' ORDER BY period",(t,))] for t in tickers}
    def save(name,value):
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    from tools.publish_native_valuation import runtime_identity
    # 固定请求/方法后才计算结果；不根据估值输出选择规则或删公司。
    from tools.select_native_assumptions import SCENARIO_RULES
    save('protocol.json',dict(recipe=recipe,selection_policy=selection_policy,scenario_rules=SCENARIO_RULES if selection_policy else None,tickers=tickers,sqlite_sha256=snapshot_hash,
        reference_sha256=value_digest(references),metadata=metadata,runtime=runtime_identity(database.parent),scope='policy_transmission_not_forecast_accuracy'))
    save('references.json',references)
    rows=[]
    with TestClient(app,raise_server_exceptions=False) as client:
        for ticker in tickers:
            key=ticker.replace(':','-'); row=dict(ticker=ticker)
            response=client.post('/api/valuation/from-database',json=dict(ticker=ticker))
            if response.status_code!=200:
                row.update(status='outside_snapshot_scope' if response.status_code==404 else 'blocked_inputs',http_status=response.status_code,reason=response.text);rows.append(row);continue
            baseline=response.json();save(key+'-baseline.json',baseline)
            save(key+'-annual-evidence.json',annual_evidence[ticker])
            from tools.select_native_assumptions import prediction_check
            row['prediction_check']=prediction_check(baseline)
            save(key+'-prediction-check.json',row['prediction_check'])
            try:
                payloads,audit=prepare(baseline,references,recipe,metadata,histories[ticker])
            except ValueError as error:
                row.update(status='blocked_policy',reason=str(error));rows.append(row);continue
            if selection_policy is not None:
                from tools.select_native_assumptions import select
                selected,selection=select(baseline,payloads['discount_only']['inputs'],capital_references,
                    references['information_as_of'],selection_policy)
                audit['selection']=selection
                if selected is not None:
                    payloads['evidence_selected']=selected
                    from tools.select_native_assumptions import scenario_payloads
                    scenarios,scenario_audit=scenario_payloads(baseline,selected)
                    payloads.update(scenarios)
                    audit['scenario_generation']=scenario_audit
            save(key+'-audit.json',audit)
            row.update(status='evaluated',baseline=baseline['final']['value_per_share'],forecast_missing=audit['forecast_missing'],selection=audit.get('selection'),scenario_generation=audit.get('scenario_generation'),variants={})
            for name,payload in payloads.items():
                save(key+'-'+name+'-request.json',payload)
                result=client.post('/api/valuation',json=payload)
                if result.status_code!=200:
                    row['variants'][name]=dict(status='rejected',http_status=result.status_code,reason=result.text);continue
                body=result.json();save(key+'-'+name+'-result.json',body)
                from tools.review_native_policy import review_report
                reviewed=review_report(body,annual_evidence[ticker])
                save(key+'-'+name+'-review.json',reviewed)
                if name=='discount_only':
                    for field in ('revenue_projections','ebit_projections','reinvestment_projections','fcff_projections'):
                        if body['dcf'][field]!=baseline['dcf'][field]:raise ValueError('discount-only changed '+field)
                if web:
                    req=Request(web.rstrip('/')+'/api/valuation',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
                    with urlopen(req,timeout=60) as live: actual=json.load(live)
                    if any(actual[field]!=body[field] for field in ('inputs','final','dcf','reference_snapshot')):
                        raise ValueError('live policy inputs/reference/result mismatch')
                row['variants'][name]=dict(status='calculated',value_per_share=body['final']['value_per_share'],
                    wacc=body['cost_of_capital']['wacc'],first_fcff=body['dcf']['fcff_projections'][0],
                    revenue_year5=body['dcf']['revenue_projections'][4], revenue_year10=body['dcf']['revenue_projections'][-1],
                    reinvestment_first5=sum(body['dcf']['reinvestment_projections'][:5]),
                    incremental_returns=[r['incremental_return_bridge'] for r in reviewed['forecast']],
                    economic_checks=reviewed['economic_checks'],
                    terminal_share=(body['dcf']['pv_terminal_value']/body['dcf']['value_of_operating_assets']
                                    if body['dcf']['value_of_operating_assets'] else None),
                    evaluated_input_changes=list(changes(baseline['inputs'],body['inputs'])),live_verified=bool(web))
            rows.append(row)
    with database.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=snapshot_hash:raise ValueError('publication changed during evaluation')
    from tools.select_native_assumptions import prediction_summary
    summary=dict(companies=len(tickers),results=rows,prediction_summary=prediction_summary(rows),scope='conditional_policy_comparison_not_market_targets')
    save('summary.json',summary)
    return summary


def deliver_conditions(database, request_files, output, web=None):
    """保存用户完整输入，组合既有增长/资本对照，不选择胜出者。"""
    from fastapi.testclient import TestClient
    from api.main import app
    from tools.audit_native_coverage import FINANCIAL_INDUSTRIES
    from tools.compare_native_capital import compare as compare_capital, POLICY, HISTORY_POLICY
    from tools.compare_native_growth import compare as compare_growth
    from tools.review_native_policy import review_report
    from tools.select_native_assumptions import growth_scenarios, SCENARIO_RULES
    from tools.publish_native_valuation import runtime_identity
    from data_sources.paths import workspace_path

    database = database.resolve(strict=True)
    output = output.resolve()
    if not request_files or output.exists() or not output.is_relative_to(workspace_path('derived').resolve()):
        raise ValueError('explicit requests and new output under workspace/derived required')
    requests = []
    for path in request_files:
        raw = path.read_bytes(); payload = json.loads(raw)
        if set(payload) != {'inputs'}:
            raise ValueError('complete native request must contain only inputs')
        inputs = CompanyValuationInput.model_validate(payload['inputs'])
        if not re.fullmatch(r'(?:SHSE|SZSE):[0-9]{6}', inputs.ticker):
            raise ValueError('explicit SH/SZ ticker required')
        requests.append((inputs.ticker, payload, dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())))
    if len({ticker for ticker, _, _ in requests}) != len(requests):
        raise ValueError('duplicate explicit company requests')
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as connection:
        metadata = dict(connection.execute('SELECT key,value FROM metadata'))
        snapshot_tickers = {r[0] for r in connection.execute('SELECT ticker FROM companies')}
    if metadata.get('contract') != 'alphalake-sqlite-v10':
        raise ValueError('current native SQLite contract required')
    with database.open('rb') as stream: snapshot_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    os.environ['US_CN_HK_DB_PATH'] = str(database)
    output.mkdir(parents=True)
    def save(name, value):
        (output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    protocol = dict(contract='native-conditional-delivery-v1', sqlite_sha256=snapshot_hash,
        metadata=metadata,
        requests=[source | dict(ticker=ticker) for ticker, _, source in requests],
        growth_rules=SCENARIO_RULES, capital_rules=[POLICY, HISTORY_POLICY],
        runtime=runtime_identity(database.parent), selected_scenario=None,
        boundary='用户完整输入优先；来源归档/经济依据须另行核验，既有条件无概率和自动胜出者，不改默认或事实')
    save('protocol.json', protocol)
    rows = []
    with TestClient(app, raise_server_exceptions=False) as client:
        for ticker, payload, source in requests:
            inputs = CompanyValuationInput.model_validate(payload['inputs'])
            key = ticker.replace(':', '-')
            row = dict(ticker=ticker, source=source, selected_scenario=None, automatic_adoption=False,
                scenarios={}, conditions_status='not_generated')
            rows.append(row)
            if ticker not in snapshot_tickers:
                row.update(status='outside_scope', reason='outside_sqlite_security_scope'); continue
            if inputs.industry_data.industry_name in FINANCIAL_INDUSTRIES:
                row.update(status='outside_scope', reason='financial_industry'); continue
            def calculate(name, request):
                save(key+'-'+name+'-request.json', request)
                response = client.post('/api/valuation', json=request)
                if response.status_code != 200:
                    row['scenarios'][name] = dict(status='rejected', http_status=response.status_code, reason=response.text)
                    return None
                body = response.json()
                if body.get('reference_snapshot', {}).get('id') != metadata.get('reference_snapshot_id'):
                    raise ValueError('native reference publication mismatch')
                supplied = CompanyValuationInput.model_validate(request['inputs'])
                returned = CompanyValuationInput.model_validate(body['inputs'])
                # 原引擎每次刷新公司派生指标；只有股价波动率可由用户供给。
                if supplied.company_metrics is not None and supplied.company_metrics.std_dev_stock is not None:
                    if returned.company_metrics is None or returned.company_metrics.std_dev_stock != supplied.company_metrics.std_dev_stock:
                        raise ValueError('explicit stock deviation changed')
                supplied.company_metrics = returned.company_metrics
                if supplied != returned:
                    raise ValueError('explicit input changed during calculation')
                reviewed = review_report(body)
                if web:
                    req = Request(web.rstrip('/')+'/api/valuation', json.dumps(request).encode(), {'Content-Type':'application/json'})
                    with urlopen(req, timeout=60) as live: actual = json.load(live)
                    if any(actual[field] != body[field] for field in ('dcf', 'final', 'reference_snapshot')):
                        raise ValueError('live result/reference mismatch')
                    if CompanyValuationInput.model_validate(actual['inputs']) != CompanyValuationInput.model_validate(body['inputs']):
                        raise ValueError('live explicit input mismatch')
                save(key+'-'+name+'-result.json', body)
                save(key+'-'+name+'-review.json', reviewed)
                row['scenarios'][name] = dict(status='calculated_conditional', value_per_share=body['final']['value_per_share'],
                    projection_years=len(reviewed['forecast']), revenue_multiple_end=reviewed['forecast'][-1]['revenue_multiple_of_base'],
                    forecast=reviewed['forecast'], terminal=reviewed['terminal'], economic_checks=reviewed['economic_checks'],
                    reference_snapshot=body.get('reference_snapshot'), live_verified=bool(web),
                    request_file=key+'-'+name+'-request.json', result_file=key+'-'+name+'-result.json')
                return body
            baseline = calculate('user_explicit', payload)
            if baseline is None:
                row['status'] = 'blocked_explicit_inputs'; continue
            row['status'] = 'explicit_calculated'
            if inputs.valuation_assumptions.projection_years != 10:
                row['conditions_reason'] = 'existing_conditions_require_ten_year_projection'; continue
            revenue = baseline['ltm_financials']['revenues']
            if revenue is None or revenue <= 0:
                row['conditions_reason'] = 'positive_base_revenue_required'; continue
            initial = baseline['dcf']['revenue_projections'][0]/revenue-1
            terminal_growth = row['scenarios']['user_explicit']['terminal']['growth']
            try:
                comparison = compare_growth(baseline, growth_scenarios(initial, terminal_growth))
            except ValueError as error:
                row['conditions_reason'] = str(error); continue
            save(key+'-growth-comparison.json', comparison)
            row['conditions_status'] = 'generated_without_selection'
            for scenario in comparison['scenarios']:
                name = scenario['name']
                body = calculate(name, dict(inputs=scenario['inputs']))
                if body is None: continue
                if body['dcf'] != scenario['dcf'] or body['final'] != scenario['final']:
                    raise ValueError('growth condition differs from comparison')
                for basis in ('company-history', 'terminal-transition'):
                    capital = compare_capital(body, basis)
                    save(key+'-'+name+'-'+basis+'-comparison.json', capital)
                    if capital['status'] != 'calculated_conditional':
                        row['scenarios'][name+'_'+basis] = dict(status=capital['status'], reason=capital['reason'], evidence=capital.get('evidence'))
                        continue
                    actual = calculate(name+'_'+basis, dict(inputs=capital['candidate']['inputs']))
                    if actual is not None and (actual['dcf'] != capital['candidate']['dcf'] or actual['final'] != capital['candidate']['final']):
                        raise ValueError('capital condition differs from comparison')
    with database.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != snapshot_hash:
            raise ValueError('publication changed during delivery')
    for _, _, source in requests:
        if hashlib.sha256(Path(source['path']).read_bytes()).hexdigest() != source['sha256']:
            raise ValueError('explicit request changed during delivery')
    result = dict(contract=protocol['contract'], protocol=protocol, companies=len(rows), results=rows,
        selected_scenario=None, automatic_adoption=False)
    result['run_id'] = value_digest(result)
    save('summary.json', result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--references',type=Path)
    parser.add_argument('--recipe',type=Path)
    parser.add_argument('--ticker',action='append')
    parser.add_argument('--explicit-input',type=Path,action='append',help='完整原生inputs请求；重复提供公司，自动交付具名条件，无胜出者')
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--selection-policy',type=Path,help='explicit automatic conditional assumption selection policy')
    parser.add_argument('--web',help='optional unchanged full-input API verification URL')
    args=parser.parse_args()
    if args.explicit_input:
        if args.references or args.recipe or args.ticker or args.selection_policy:
            parser.error('explicit inputs cannot be mixed with policy selection')
        result = deliver_conditions(args.database, args.explicit_input, args.output, args.web)
        print(json.dumps(dict(contract=result['contract'], run_id=result['run_id'], companies=result['companies'],
            selected_scenario=None, summary_file=str(args.output/'summary.json')), ensure_ascii=False))
        return
    if not args.references or not args.recipe or not args.ticker:
        parser.error('references, recipe and ticker required without explicit inputs')
    print(json.dumps(evaluate(args.database,json.loads(args.references.read_text()),json.loads(args.recipe.read_text()),args.ticker,args.output,args.web,json.loads(args.selection_policy.read_text()) if args.selection_policy else None),ensure_ascii=False))


if __name__=='__main__':main()
