"""周期行业条件批次：复用原生输入和共享引擎，保留基线、缺项与经济审核边界。"""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import io
import json
import math
import os
from pathlib import Path
import re
import resource
import sqlite3
import tarfile
import time

from engine.data_dictionary import CompanyValuationInput, ForecastYear
from engine.module_1_adjustments import capitalize_r_and_d
from engine.orchestrator import run_full_valuation
from tools.audit_native_coverage import FINANCIAL_INDUSTRIES
from tools.check_native_sqlite import value_digest
from tools.evaluate_native_policy import research_tax_condition
from tools.export_alphalake_sqlite import digest
from tools.review_native_policy import review_report

METHOD_SOURCE = 'https://pages.stern.nyu.edu/~adamodar/pdfiles/papers/commodity.pdf'
# 行业只筛选暴露候选，不证明公司周期性、当前峰谷或业务同质性。
PROFILES = {
    'resources': ('资源价格与储量', '正常价格、销量／品位、开采成本及储量补充／有限寿命',
        ('Coal & Related Energy', 'Oil/Gas (Integrated)', 'Oil/Gas (Production and Exploration)', 'Precious Metals', 'Metals & Mining')),
    'materials': ('材料供需与价差', '产品与原料价差、产能利用率、成本位置及库存资本',
        ('Steel', 'Chemical (Basic)', 'Chemical (Diversified)', 'Chemical (Specialty)', 'Paper/Forest Products', 'Packaging & Container')),
    'agriculture': ('农业供给与价格', '品种价格、产量、天气／生物周期、饲料与原料成本', ('Farming/Agriculture',)),
    'technology_capacity': ('技术供需与产能', '价格和销量分拆、产品升级、产能／库存及研发经济寿命',
        ('Semiconductor', 'Computers/Peripherals', 'Electronics (General)', 'Electronics (Consumer & Office)')),
    'capital_spending': ('资本开支与订单', '客户投资周期、订单／交付、建设领先期和维护投入',
        ('Semiconductor Equip', 'Machinery', 'Electrical Equipment', 'Oilfield Svcs/Equip.', 'Shipbuilding & Marine', 'Telecom. Equipment')),
    'durables': ('耐用品需求', '销量、替换需求、产能、产品结构及金融兼营范围',
        ('Auto & Truck', 'Auto Parts', 'Rubber& Tires', 'Furn/Home Furnishings', 'Office Equipment & Services')),
    'property_construction': ('地产建设与信用', '开工／交付、合同／存货资本、周期融资及存续风险',
        ('Homebuilding', 'Building Materials', 'Construction Supplies', 'Engineering/Construction', 'Real Estate (Development)', 'Real Estate (General/Diversified)', 'Real Estate (Operations & Services)')),
    'transport_capacity': ('运价与运力', '运价、运输量、运力／载运率、租赁和燃料成本',
        ('Air Transport', 'Transportation', 'Transportation (Railroads)', 'Trucking')),
    'discretionary_demand': ('可选消费与商业需求', '价格／客流、客户需求、租赁和品牌／结构性增长',
        ('Advertising', 'Apparel', 'Shoe', 'Hotel/Gaming', 'Recreation', 'Restaurant/Dining', 'Retail (Automotive)', 'Retail (Building Supply)', 'Retail (Distributors)', 'Retail (General)', 'Retail (Special Lines)', 'Entertainment')),
    'regulated_energy': ('能源与受监管业务（混合）', '电价／监管回报、燃料价差、合同保护和资本密集度',
        ('Power', 'Green & Renewable Energy', 'Oil/Gas Distribution', 'Utility (General)', 'Utility (Water)')),
    'mixed_business': ('混合经营待核实', '业务分部与经济暴露；不能把行业标签统一外推到公司',
        ('Diversified', 'Aerospace/Defense', 'Business & Consumer Services', 'Food Processing', 'Food Wholesalers', 'Household Products', 'Broadcasting', 'Cable TV', 'Software (Entertainment)', 'Software (Internet)')),
}
INDUSTRY_PROFILE = {industry: key for key, (_, _, industries) in PROFILES.items() for industry in industries}
VARIANTS = (('arithmetic', 1), ('arithmetic', 3), ('arithmetic', 5), ('revenue_weighted', 3))
POLICY = dict(version='cyclical-observation-conditions-v1', history_years=5,
    revenue='flat_first_three_years_then_inherited_terminal_growth',
    margin='reported_observation_plus_explicit_research_bridge',
    capital='inherited_ratio_to_terminal_consistency_by_year_five',
    research='current_expense_ratio_ordinary_deduction', financing='preserve_original_condition',
    selected_scenario=None, automatic_adoption=False)


def industry_scope(industry):
    if industry in FINANCIAL_INDUSTRIES | {'Reinsurance'}:
        return dict(status='outside_financial_scope', profile=None)
    profile = INDUSTRY_PROFILE.get(industry)
    if profile:
        return dict(status='mixed_cycle_exposure_candidate' if profile in ('mixed_business', 'regulated_energy') else 'cycle_exposure_candidate',
            profile=profile, title=PROFILES[profile][0], required_business_evidence=PROFILES[profile][1],
            classification_basis='explicit_industry_screen_not_company_cycle_fact', company_cycle_verified=False)
    return dict(status='unresolved_industry' if industry is None else 'not_screened_by_industry_policy', profile=None)


def load_scope(path, snapshot_hash, metadata, tickers):
    """消费既有主库范围审核；行业标签不能推翻金融冲突或未知身份。"""
    coverage = json.loads(path.read_text())
    if (coverage.get('contract_version') != 'alphalake-native-coverage-v1'
        or coverage.get('scope_policy') != 'nonfinancial-tdx-damodaran-2026-v1'
        or coverage.get('sqlite_sha256') != snapshot_hash
        or coverage.get('source_database_sha256') != metadata['source_database_sha256']
        or coverage.get('report_period') != metadata['report_period']):
        raise ValueError('current canonical coverage contract and source hashes required')
    cutoff = datetime.fromisoformat(coverage['information_as_of'])
    if cutoff.tzinfo is None or cutoff != datetime.fromisoformat(metadata['information_as_of']):
        raise ValueError('coverage information cutoff mismatch')
    companies = coverage['companies']
    scopes = {r.get('ticker'):r['scope'] for r in companies}
    valid = {'nonfinancial_by_reference', 'outside_financial_scope', 'unresolved_industry_scope'}
    if (len(scopes) != len(companies) or set(scopes) != set(tickers)
        or set(scopes.values())-valid or coverage['snapshot_candidates'] != len(tickers)
        or dict(Counter(scopes.values())) != coverage['scope_counts']):
        raise ValueError('complete unique publication scope and matching counts required')
    return scopes


def cycle_conditions(baseline):
    """附加固定观察条件；不更改原事实，不覆盖用户已填未来路径。"""
    inputs = CompanyValuationInput.model_validate(baseline['inputs'])
    a, adj = inputs.valuation_assumptions, inputs.adjustment_inputs
    if (a.annual_forecast is not None or a.annual_sales_to_capital is not None
            or a.annual_net_reinvestment is not None or a.research_tax is not None):
        raise ValueError('explicit_future_path_preserved; submit a separately reviewed cycle condition')
    if adj.has_operating_leases:
        raise ValueError('reported_margin_requires_matching_lease_adjustment_scope')
    if a.projection_years != 10:
        raise ValueError('observation_condition_requires_ten_year_path; baseline_preserved')
    if inputs.reporting_currency != 'CNY' or inputs.stock_price_currency != 'CNY':
        raise ValueError('CNY_condition_scope_required')
    base_year = date.fromisoformat(inputs.period_date_10k[:10]).year
    years = list(range(base_year-4, base_year+1))
    history = {}
    for row in inputs.raw_financials:
        if row.fiscal_year in years:
            if row.fiscal_year in history:
                raise ValueError('ambiguous_observation_year')
            history[row.fiscal_year] = row
    missing = [y for y in years if y not in history or any(v is None or not math.isfinite(v)
        for v in (history[y].revenues, history[y].ebit)) or history[y].revenues <= 0]
    if missing:
        raise ValueError('observation_margin_unavailable_years='+','.join(map(str, missing)))
    # 亏损年份参与均值；不按价格、利润符号或估值结果筛历史。
    observed = [dict(year=y, revenue=history[y].revenues, reported_ebit=history[y].ebit,
        reported_margin=history[y].ebit/history[y].revenues) for y in years]
    revenue0, ebit0 = baseline['ltm_financials']['revenues'], baseline['ltm_financials']['ebit']
    if revenue0 is None or revenue0 <= 0 or ebit0 is None:
        raise ValueError('positive_current_revenue_and_reported_profit_required_for_margin_path')
    g, roic = baseline['dcf']['terminal_growth'], baseline['dcf']['terminal_roic']
    if g <= -1 or not math.isfinite(g) or roic <= 0:
        raise ValueError('invalid_inherited_terminal_condition')
    growth = [0.0]*3+[g]*7
    revenues, value = [], revenue0
    for rate in growth:
        value *= 1+rate
        revenues.append(value)
    expenses, amortization, research_evidence = [0.0]*10, [0.0]*10, None
    prepared = deepcopy(baseline)
    prepared['dcf']['revenue_projections'] = revenues
    request = dict(inputs=inputs.model_dump(mode='json'))
    if adj.has_r_and_d:
        request, research_evidence = research_tax_condition(prepared, g)
        expenses = request['inputs']['valuation_assumptions']['research_tax']['expenses']
        life = adj.amortization_period_n
        cohorts = [adj.r_and_d_expense_current, *adj.r_and_d_expense_past[:life-1]]
        if len(cohorts) != life or any(v is None or not math.isfinite(v) or v < 0 for v in cohorts):
            raise ValueError('research_cohorts_incomplete; do_not_estimate_or_shorten_life')
        amortization = []
        for expense in expenses:
            _, amort, _ = capitalize_r_and_d(expense, cohorts, life)
            amortization.append(amort)
            cohorts = [expense, *cohorts[:life-1]]
    taxes = baseline['dcf']['tax_rate_projections']
    sc_start = a.sales_to_capital_high
    if sc_start is None or not math.isfinite(sc_start) or sc_start <= 0:
        raise ValueError('explicit_positive_starting_capital_ratio_required')
    lag = max(0, min(3, a.reinvestment_lag_years)) if a.override_reinvestment_lag else 1
    variants = {}
    for average, convergence in VARIANTS:
        target = (sum(r['reported_margin'] for r in observed)/len(observed) if average == 'arithmetic'
            else sum(r['reported_ebit'] for r in observed)/sum(r['revenue'] for r in observed))
        reported = [ebit0/revenue0+(target-ebit0/revenue0)*min((i+1)/convergence, 1) for i in range(10)]
        margins = [m+(expense-amort)/revenue for m, expense, amort, revenue in zip(reported, expenses, amortization, revenues, strict=True)]
        name = f'{average}-{convergence}y'
        try:
            changed = deepcopy(request)
            va = changed['inputs']['valuation_assumptions']
            va['annual_forecast'] = [ForecastYear(growth=rate, margin=margin, tax=tax).model_dump()
                for rate, margin, tax in zip(growth, margins, taxes, strict=True)]
            # 从共享引擎读取稳态税／研发结果，不以最后一期现金税或NOL代替稳态NOPAT。
            trial = run_full_valuation(CompanyValuationInput.model_validate(changed['inputs']))
            terminal_nopat_margin = trial.dcf.terminal_nopat/trial.dcf.terminal_revenue
            if terminal_nopat_margin <= 0:
                variants[name] = dict(status='requires_terminal_recovery_condition', target_reported_margin=target,
                    reason='nonpositive_terminal_NOPAT; no fabricated positive_margin_or_capital_ratio')
                continue
            sc_terminal = trial.dcf.terminal_roic*(1+g)**(lag-1)/terminal_nopat_margin
            va['annual_sales_to_capital'] = [sc_start+(sc_terminal-sc_start)*min(i/4, 1) for i in range(10)]
            CompanyValuationInput.model_validate(changed['inputs'])
            variants[name] = dict(status='prepared_explicit_condition', request=changed,
                target_reported_margin=target, convergence_year=convergence, capital_ratio_terminal=sc_terminal)
        except ValueError as error:
            variants[name] = dict(status='condition_rejected', target_reported_margin=target, reason=str(error))
    return variants, dict(policy=POLICY, observation=observed, units='million CNY', period='FY',
        historical_basis='reported_EBIT_not_capitalized_RD_or_normalized_operating_fact',
        full_cycle_verified=False, research=research_evidence,
        economic_requirements=['cycle_window_and_business_scope', 'normal_revenue_price_volume_and_mix',
            'operating_profit_and_corresponding_asset_scope', 'nonresearch_capital_need_or_release',
            'normal_financing_and_survival', 'terminal_return_risk_and_growth_consistency'],
        selected_scenario=None, automatic_adoption=False)


def economic_review(body):
    review = review_report(body)
    releases = [dict(year=r['year'], amount=r['implied_nonresearch_net_reinvestment_million_cny'])
        for r in review['forecast'] if r.get('implied_nonresearch_net_reinvestment_million_cny', 0) < -1e-8]
    last, terminal = body['dcf']['reinvestment_projections'][-1], body['dcf']['terminal_reinvestment']
    g = body['dcf']['terminal_growth']
    if not math.isclose(last*(1+g), terminal, rel_tol=1e-10, abs_tol=1e-7):
        raise ValueError('terminal_reinvestment_discontinuity')
    research = body['inputs']['valuation_assumptions']['research_tax']
    terminal_research = None
    if research is not None:
        # 税基恒等式只重建研发净投入组成，不替代引擎稳态计算。
        amort = body['dcf']['terminal_tax_base']-body['dcf']['terminal_ebit']+research['terminal_deduction']
        net = research['terminal_expense']-amort
        residual = terminal-net
        terminal_research = dict(expense_million_cny=research['terminal_expense'], amortization_million_cny=amort,
            net_investment_million_cny=net, nonresearch_net_investment_million_cny=residual,
            positive_growth_with_nonresearch_release=g > 0 and residual < -1e-8,
            status='capital_composition_requires_evidence', evidence_verified=False)
    return dict(arithmetic_verified=True, research_tax_basis=body['dcf']['operating_tax_basis'],
        nonresearch_capital_release_years=releases, capital_release_evidence_verified=False,
        terminal_research_capital=terminal_research,
        terminal_reinvestment_continuous=True, economic_forecast_verified=False, selected_scenario=None)


def profit_components(connection, report_period):
    """一次读取当前累计三分量；缺项与来源冲突保留，不自动剔除收益。"""
    end = date.fromisoformat(report_period)
    periods = {end.isoformat(): 1}
    if end.month != 12:
        periods.update({f'{end.year-1}-12-31': 1, end.replace(year=end.year-1).isoformat(): -1})
    from tools.export_alphalake_sqlite import query_standard_inputs
    facts = {}
    for row in query_standard_inputs(connection,('investment_income','fair_value_change_income'),list(periods)):
        ticker, period, field = row['ticker'],row['period'],row['field']
        facts.setdefault((ticker,field),{}).setdefault(period,[]).append({k:row[k] for k in
            ('period','value','unit','period_type','statement_scope','fact_id','source_record_id')})
    result = {}
    for ticker in {k[0] for k in facts}:
        components = {}
        for field in ('investment_income', 'fair_value_change_income'):
            rows = facts.get((ticker, field), {})
            missing = [p for p in periods if p not in rows]
            invalid = [p for p in periods if p in rows and (len(rows[p]) != 1 or any(
                r['unit'] != 'CNY' or r['statement_scope'] != 'provider_default' or r['period_type'] !=
                {3:'Q1',6:'H1',9:'9M',12:'FY'}[date.fromisoformat(p).month] or not Decimal(r['value']).is_finite() for r in rows[p]))]
            amount = (sum(Decimal(rows[p][0]['value'])*sign for p, sign in periods.items())/Decimal(1000000)
                if not missing and not invalid else None)
            components[field] = dict(amount_million_cny=float(amount) if amount is not None else None,
                missing_periods=missing, invalid_periods=invalid, source_components=rows)
        result[ticker] = dict(report_period=report_period, units='million CNY', components=components,
            treatment='scope_review_required; no_automatic_exclusion_or_asset_bridge_change')
    return result


def run(database, output, scope_file, tickers=None):
    """全发布范围逐公司复用API；报告按压缩包流写，服务内只保留当前公司。"""
    from fastapi.testclient import TestClient
    from api.main import app
    from api.routes import _get_damodaran_store
    from data_sources.paths import workspace_path
    started = time.monotonic()
    database, output = database.resolve(strict=True), output.resolve()
    if output.exists() or not output.is_relative_to(workspace_path('derived').resolve()):
        raise ValueError('new output under workspace/derived required')
    os.environ['US_CN_HK_DB_PATH'] = str(database)
    before = digest(database)
    store = _get_damodaran_store()
    with sqlite3.connect('file:'+str(database)+'?mode=ro', uri=True) as conn:
        all_tickers = [r[0] for r in conn.execute('SELECT ticker FROM companies ORDER BY ticker')]
        metadata = dict(conn.execute("SELECT key,value FROM metadata WHERE key < 'input_context:' OR key >= 'input_context;'"))
        profits = profit_components(conn, metadata['report_period'])
    scope_file = scope_file.resolve(strict=True)
    scope_hash = digest(scope_file)
    scopes = load_scope(scope_file, before, metadata, all_tickers)
    if tickers is not None and (len(set(tickers)) != len(tickers) or set(tickers)-set(all_tickers)):
        raise ValueError('unique tickers within publication required')
    selected = sorted(tickers) if tickers is not None else all_tickers
    output.mkdir(parents=True)
    protocol = dict(contract='cyclical-industry-delivery-v1', frozen_at=datetime.now(timezone.utc).isoformat(),
        database_sha256=before, metadata=metadata, reference_snapshot_id=store.reference_snapshot['id'],
        policy=POLICY, profiles=PROFILES, scope=selected, scope_count=len(selected),
        canonical_scope_sha256=scope_hash, canonical_scope_policy='nonfinancial-tdx-damodaran-2026-v1',
        canonical_scope_counts=dict(Counter(scopes[t] for t in selected)),
        implementation_sha256=digest(Path(__file__)), method_source=METHOD_SOURCE)
    (output/'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2)+'\n')
    rows, counts = [], Counter()
    with TestClient(app, raise_server_exceptions=False) as client, tarfile.open(output/'api-evidence.tar.gz', 'w:gz') as archive:
        def save(name, body):
            content = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            member = tarfile.TarInfo(name); member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
            return value_digest(body)
        for index, ticker in enumerate(selected):
            company = store.industry_mapper.lookup(ticker)
            industry = company.industry_group if company else None
            scope = industry_scope(industry)
            canonical = scopes[ticker]
            if canonical != 'nonfinancial_by_reference':
                scope = dict(status=canonical, profile=None, industry_screen=scope)
            row = dict(ticker=ticker, industry=industry, canonical_scope=canonical, scope=scope, variants={}, selected_scenario=None,
                profit_scope=profits.get(ticker, dict(status='not_exported_in_current_standard_scope')))
            rows.append(row)
            if not re.fullmatch(r'(?:SHSE|SZSE):[0-9]{6}', ticker):
                row['status'] = 'outside_market_scope'
            elif scope['profile'] is None:
                row['status'] = scope['status']
            else:
                response = client.post('/api/valuation/from-database', json=dict(ticker=ticker))
                body = response.json()
                row['baseline_sha256'] = save(ticker.replace(':', '-')+'-baseline.json', dict(http_status=response.status_code, response=body))
                if response.status_code != 200:
                    if response.status_code != 422:
                        raise ValueError(f'unexpected native API failure {ticker}: {response.status_code}')
                    row.update(status='blocked_original_inputs', reason=body.get('detail'))
                else:
                    try:
                        row.update(baseline_value_per_share=body['final']['value_per_share'], stock_price=body['ltm_financials']['stock_price'])
                        for component in row['profit_scope'].get('components', {}).values():
                            value = component['amount_million_cny']; ebit = body['ltm_financials']['ebit']
                            component['ratio_to_reported_EBIT'] = value/ebit if value is not None and ebit else None
                        variants, evidence = cycle_conditions(body)
                        row['evidence'] = evidence
                        row['status'] = 'conditions_evaluated'
                        for name, variant in variants.items():
                            item = {k:v for k,v in variant.items() if k != 'request'}
                            row['variants'][name] = item
                            if variant['status'] != 'prepared_explicit_condition':
                                continue
                            try:
                                inputs = CompanyValuationInput.model_validate(variant['request']['inputs'])
                                report = run_full_valuation(inputs)
                                calculated = dict(inputs=inputs.model_dump(mode='json'), **{field:getattr(report, field).model_dump(mode='json')
                                    for field in ('ltm_financials', 'adjusted', 'cost_of_capital', 'cashflow', 'dcf', 'final')},
                                    convertible_claim=report.convertible_claim, equity_bridge=report.equity_bridge)
                                item.update(status='calculated_explicit_condition', value_per_share=report.final.value_per_share,
                                    review=economic_review(calculated))
                                item['response_sha256'] = save(ticker.replace(':', '-')+'-'+name+'.json', calculated)
                            except ValueError as error:
                                item.update(status='condition_rejected', reason=str(error))
                    except ValueError as error:
                        row.update(status='condition_input_boundary', reason=str(error))
                    finally:
                        if client.delete('/api/valuation/'+body['id']).status_code != 204:
                            raise ValueError('verification session release failed')
            counts[row['status']] += 1
            if (index+1) % 100 == 0:
                print(json.dumps(dict(processed=index+1, total=len(selected), statuses=dict(counts)), ensure_ascii=False), flush=True)
    if digest(database) != before:
        raise ValueError('source SQLite changed during cycle batch; result not publishable')
    if digest(scope_file) != scope_hash:
        raise ValueError('canonical scope changed during cycle batch; result not publishable')
    summary = dict(contract=protocol['contract'], policy=POLICY, scope_count=len(rows), statuses=dict(counts),
        profiles=dict(Counter(r['scope']['profile'] for r in rows if r['scope']['profile'])),
        variant_statuses=dict(Counter(v['status'] for r in rows for v in r['variants'].values())),
        database_unchanged=True, engine_defaults_unchanged=True, economic_forecast_verified=False,
        full_cycle_verified=False, selected_scenario=None, automatic_adoption=False, results=rows)
    summary['execution'] = dict(elapsed_seconds=time.monotonic()-started,
        python_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
        archive_bytes=(output/'api-evidence.tar.gz').stat().st_size,
        archive_sha256=digest(output/'api-evidence.tar.gz'),
        measurement='serial native APIs and shared engine, warm cache, one company retained at a time')
    summary['run_id'] = value_digest(summary)
    (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    return {k:v for k,v in summary.items() if k != 'results'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--scope', type=Path, required=True, help='当前同版主库／SQLite的audit_native_coverage报告')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ticker', action='append', help='限定验收证券；省略时保留完整发布分母')
    args = parser.parse_args()
    print(json.dumps(run(args.database, args.output, args.scope, args.ticker), ensure_ascii=False, indent=2))
