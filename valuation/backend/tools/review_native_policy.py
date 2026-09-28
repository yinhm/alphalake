"""只读审阅原生候选的增长、利润和资本需求；不批准预测或修改默认值。"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

from engine.data_dictionary import CompanyValuationInput
from engine.orchestrator import run_full_valuation


def review_report(body):
    inputs = CompanyValuationInput.model_validate(body['inputs'])
    if (inputs.reporting_currency != 'CNY' or inputs.stock_price_currency != 'CNY'
            or not re.fullmatch(r'(?:SHSE|SZSE):[0-9]{6}', inputs.ticker)):
        raise ValueError('CNY SH/SZ report required')
    # 复用引擎核对保存输入与结果，不能把被改写的现金流当作待审政策。
    replay = run_full_valuation(inputs)
    for field in ('ltm_financials', 'adjusted', 'cost_of_capital', 'cashflow', 'dcf', 'final'):
        if getattr(replay, field).model_dump(mode='json') != body[field]:
            raise ValueError('saved report does not replay: ' + field)
    raw, adjusted, dcf = replay.ltm_financials, replay.adjusted, replay.dcf
    a = inputs.valuation_assumptions
    previous_revenue = raw.revenues
    rows = []
    for i, (revenue, ebit, reinvestment, fcff, discount) in enumerate(zip(
            dcf.revenue_projections, dcf.ebit_projections, dcf.reinvestment_projections,
            dcf.fcff_projections, dcf.discount_factors, strict=True)):
        nopat = fcff + reinvestment
        rows.append(dict(year=i+1, revenue_million_cny=revenue,
            revenue_multiple_of_base=revenue/raw.revenues if raw.revenues and raw.revenues > 0 else None,
            growth=revenue/previous_revenue-1 if previous_revenue else None,
            adjusted_operating_margin=ebit/revenue if revenue else None,
            nopat_million_cny=nopat, reinvestment_million_cny=reinvestment,
            reinvestment_rate=reinvestment/nopat if nopat > 0 else None,
            fcff_million_cny=fcff, pv_fcff_million_cny=fcff*discount,
            implied_book_roic=dcf.implied_roic_projections[i]))
        previous_revenue = revenue
    history = sorted(inputs.raw_financials, key=lambda f: f.fiscal_year)
    # 复用引擎逐年研发队列结果；不拿当前研发资产或TTM利润率填历史。
    counts = Counter(f.fiscal_year for f in inputs.raw_financials)
    annual_evidence = []
    for index, f in enumerate(inputs.raw_financials[:10]):
        cohort = range(f.fiscal_year-inputs.adjustment_inputs.amortization_period_n, f.fiscal_year+1)
        unavailable = [year for year in cohort if counts[year] != 1 or not any(
            r.fiscal_year == year and r.r_and_d_expense is not None
            and math.isfinite(r.r_and_d_expense) and r.r_and_d_expense >= 0
            for r in inputs.raw_financials)] if inputs.adjustment_inputs.has_r_and_d else []
        margin = replay.cashflow.historical_margin_by_year[index]
        ratio = replay.cashflow.historical_s_c_by_year[index]
        annual_evidence.append(dict(year=f.fiscal_year,
            research_adjusted_margin=margin, research_adjusted_sales_to_capital=ratio,
            unavailable_research_cohort_years=unavailable,
            missing_balance_inputs=[name for name in ('bv_equity','bv_debt','cash_and_marketable_securities')
                if getattr(f,name) is None],
            boundary='原生历史诊断仅按逐年研发调整；租赁及现金/投资代理口径未因此闭合'))
    peers = []
    for peer in (inputs.industry_data, inputs.industry_data_global):
        if peer is not None:
            peers.append(dict(industry=peer.industry_name, region=peer.region,
                pretax_unadjusted_operating_margin=peer.pretax_operating_margin,
                historical_sales_to_capital=peer.sales_to_capital,
                comparable_for_adjusted_target_selection=False,
                reason='unadjusted_industry_margin_not_company_research_and_lease_adjusted_margin'))
    historical = []
    for i, f in enumerate(history):
        prior = history[i-1] if i and history[i-1].fiscal_year == f.fiscal_year-1 else None
        historical.append(dict(year=f.fiscal_year, revenue_million_cny=f.revenues,
            growth=f.revenues/prior.revenues-1 if prior and prior.revenues and prior.revenues > 0 else None,
            reported_ebit_margin=f.ebit/f.revenues if f.revenues else None))
    k = inputs.quarters_since_10k
    recent = dict(status='missing_comparable_window', current=None, prior=None)
    if k and len(inputs.quarterly_financials) >= k+4:
        # 原生季序契约：当前YTD=前k季，上年同窗=偏移4后的k季；不读占位fiscal_year。
        windows = [inputs.quarterly_financials[:k], inputs.quarterly_financials[4:4+k]]
        comparable = []
        for window in windows:
            comparable.append({field:sum(getattr(f,field) for f in window)
                if all(getattr(f,field) is not None for f in window) else None
                for field in ('revenues','ebit')})
        recent.update(status='reported_ytd_comparison', current=comparable[0], prior=comparable[1],
            period=inputs.period_date_10q, quarters=k)
    elif not k and len(history) >= 2 and history[-1].fiscal_year == history[-2].fiscal_year+1:
        recent.update(status='reported_annual_comparison',
            current={f:getattr(history[-1],f) for f in ('revenues','ebit')},
            prior={f:getattr(history[-2],f) for f in ('revenues','ebit')}, period=inputs.period_date_10k)
    if recent['current'] is not None:
        current, prior = recent['current'], recent['prior']
        recent['revenue_growth'] = (current['revenues']/prior['revenues']-1
            if current['revenues'] is not None and prior['revenues'] is not None and prior['revenues'] > 0 else None)
        for window in (current,prior):
            window['reported_ebit_margin'] = (window['ebit']/window['revenues']
                if window['ebit'] is not None and window['revenues'] else None)
    macro = inputs.macro_inputs
    terminal_wacc = a.cost_of_capital_stable_override
    if terminal_wacc is None:
        rf = a.riskfree_after_yr10 if a.override_riskfree and a.riskfree_after_yr10 is not None else macro.risk_free_rate
        terminal_wacc = rf + macro.equity_risk_premium
    terminal_roic = a.roic_stable_override if a.roic_stable_override is not None else terminal_wacc
    g = macro.risk_free_rate
    if a.override_growth_perpetuity and a.growth_perpetuity_rate is not None:
        g = a.growth_perpetuity_rate
    elif a.override_riskfree and a.riskfree_after_yr10 is not None:
        g = a.riskfree_after_yr10
    if not a.override_growth_perpetuity:
        g = min(a.stable_growth_rate if a.stable_growth_rate is not None else g, macro.risk_free_rate)
    sc_high = a.sales_to_capital_high if a.sales_to_capital_high is not None else 2.5
    sc_stable = a.sales_to_capital_stable if a.sales_to_capital_stable is not None else sc_high
    lag = max(0, min(3, a.reinvestment_lag_years)) if a.override_reinvestment_lag else 1
    extended_revenue = [raw.revenues or 0.0, *dcf.revenue_projections]
    for _ in range(3):
        extended_revenue.append(extended_revenue[-1]*(1+g))
    for row in rows:
        year = row['year']
        funded_year = year+lag
        ratio = sc_high if year <= (a.high_growth_years or 5) else sc_stable
        delta = extended_revenue[funded_year]-extended_revenue[funded_year-1]
        expected = delta/ratio
        if not math.isclose(expected, row['reinvestment_million_cny'], rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError('forecast reinvestment does not match sales-to-capital funding')
        row['capital_funding'] = dict(revenue_year=funded_year,
            revenue_extension='terminal_growth_padding' if funded_year > len(rows) else 'forecast',
            incremental_revenue_million_cny=delta, sales_to_capital=ratio,
            recomputed_reinvestment_million_cny=expected)
        bridge = dict(status='outside_comparable_forecast', funded_year=funded_year,
            incremental_nopat_million_cny=None, revenue_contribution_million_cny=None,
            nopat_margin_contribution_million_cny=None,
            incremental_nopat_per_reinvestment=None, constant_nopat_margin_incremental_return=None)
        # 不以历史不同税/研发口径补第0年，也不把终值延长收入伪装成已预测利润。
        if 2 <= funded_year <= len(rows):
            prior, current = rows[funded_year-2:funded_year]
            if prior['revenue_million_cny'] > 0 and current['revenue_million_cny'] > 0:
                prior_margin = prior['nopat_million_cny']/prior['revenue_million_cny']
                current_margin = current['nopat_million_cny']/current['revenue_million_cny']
                revenue_part = delta*prior_margin
                margin_part = current['revenue_million_cny']*(current_margin-prior_margin)
                nopat_delta = current['nopat_million_cny']-prior['nopat_million_cny']
                if not math.isclose(revenue_part+margin_part, nopat_delta, rel_tol=1e-10, abs_tol=1e-8):
                    raise ValueError('incremental NOPAT bridge does not reconcile')
                investment = row['reinvestment_million_cny']
                bridge.update(status='computed' if investment > 0 else 'nonpositive_reinvestment',
                    incremental_nopat_million_cny=nopat_delta,
                    revenue_contribution_million_cny=revenue_part,
                    nopat_margin_contribution_million_cny=margin_part,
                    incremental_nopat_per_reinvestment=nopat_delta/investment if investment > 0 else None,
                    constant_nopat_margin_incremental_return=revenue_part/investment if investment > 0 else None)
        row['incremental_return_bridge'] = bridge
    terminal_fcff = dcf.terminal_value_firm * (terminal_wacc-g)
    terminal_share = dcf.pv_terminal_value/dcf.value_of_operating_assets if dcf.value_of_operating_assets else None
    missing = [name for name in ('capex', 'd_a', 'change_in_noncash_wc') if getattr(raw, name) is None]
    return dict(status='requires_analyst_judgment', automatic_adoption=False,
        unit='million_CNY', ticker=inputs.ticker,
        historical_reported_rows=historical,
        sustainability_evidence=dict(status='requires_company_and_accounting_basis',
            annual_rows=annual_evidence,
            supplied_annual_years=sorted(counts),
            absent_annual_years=[y for y in range(min(counts),max(counts)+1) if y not in counts] if counts else [],
            duplicate_annual_years=[y for y,n in sorted(counts.items()) if n>1],
            research_adjusted_margin_years=sum(r['research_adjusted_margin'] is not None for r in annual_evidence),
            research_adjusted_capital_years=sum(r['research_adjusted_sales_to_capital'] is not None for r in annual_evidence),
            industry_references=peers,
            boundary='缺项只说明本次估值输入不足，不证明标准库或上游无数据；历史与行业参考均不自动证明未来持续性'),
        recent_reported_window=recent,
        margin_bridge=dict(reported_ebit=raw.ebit, research_expense=raw.r_and_d_expense,
            research_amortization=adjusted.amortization_r_and_d,
            lease_adjustment=adjusted.lease_adjustment_to_ebit,
            adjusted_ebit=adjusted.adjusted_ebit,
            reported_margin=raw.ebit/raw.revenues if raw.revenues else None,
            adjusted_margin=adjusted.adjusted_ebit/raw.revenues if raw.revenues else None),
        forecast=rows,
        capital=dict(sales_to_capital_high=sc_high,
            high_ratio_basis='explicit_assumption' if a.sales_to_capital_high is not None else 'model_default',
            sales_to_capital_stable=sc_stable,
            stable_ratio_basis='explicit_assumption' if a.sales_to_capital_stable is not None else 'inherits_high_ratio',
            reinvestment_lag_years=lag,
            forecast_method='incremental_revenue_divided_by_sales_to_capital',
            forecast_scope='aggregate_net_reinvestment; do_not_add_working_capital_or_RD_again',
            historical_cashflow_is_forecast_prerequisite=False,
            historical_cashflow_role='classification_and_capital_efficiency_validation_not_automatic_forecast',
            first_five_years_reinvestment_million_cny=sum(r['reinvestment_million_cny'] for r in rows[:5]),
            historical_fcff=replay.cashflow.fcff, missing_historical_cashflow_inputs=missing,
            historical_adjusted_roic=replay.cashflow.historical_roic_by_year,
            marginal_company_capital_efficiency=None,
            incremental_return_basis='forecast_diagnostic_not_observed_company_or_project_ROIC',
            incremental_return_boundary='税后利润率变动含经营利润率、税率及亏损抵扣影响；增量利润不全归因于新增投资，恒定利润率分量亦非因果估计',
            review_required=['资本倍率须区分模型默认、行业存量代理与公司新增资本证据',
                '研发资本化、租赁及现金/投资代理须按相同范围比较',
                '历史报表行可能因缺EBIT而省略，不能据此判定标准收入缺失']),
        terminal=dict(growth=g, wacc=terminal_wacc, assumed_marginal_roic=terminal_roic,
            reinvestment_rate=g/terminal_roic if g > 0 else 0,
            fcff_million_cny=terminal_fcff, value_share=terminal_share,
            implied_book_roic=dcf.implied_roic_terminal,
            boundary='终值新增投资回报假设与存量资本隐含回报不同；不强制二者相等'),
        decision=dict(growth='requires_forward_growth_evidence',
            margin='requires_company_margin_sustainability_review',
            capital='requires_marginal_capital_evidence',
            wacc='sensitivity_only_explicit_reference_policy'),
        boundary='同引擎重放只验证计算一致性，非独立语义证据或预测有效性；亏损和负再投资保留，不以阈值批准。')


def review_directory(directory):
    from tools.publish_native_valuation import runtime_identity
    files = {}
    def read(name):
        raw = (directory/name).read_bytes()
        files[name] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)
    protocol, summary = read('protocol.json'), read('summary.json')
    if (not protocol['tickers'] or len(set(protocol['tickers'])) != len(protocol['tickers'])
            or [r['ticker'] for r in summary['results']] != protocol['tickers']
            or summary['companies'] != len(protocol['tickers'])):
        raise ValueError('fixed company denominator differs')
    results = []
    for row in summary['results']:
        ticker = row['ticker']
        if not re.fullmatch(r'(?:SHSE|SZSE):[0-9]{6}', ticker):
            raise ValueError('invalid saved ticker')
        item = dict(ticker=ticker, source_status=row['status'], variants={})
        results.append(item)
        if row['status'] != 'evaluated':
            item['reason'] = row.get('reason')
            continue
        key = ticker.replace(':', '-')
        audit = read(key+'-audit.json')
        item['historical_standard_revenue'] = audit['forecast'].get('annual_revenue', [])
        history = item['historical_standard_revenue']
        item['historical_standard_revenue_growth'] = [dict(year=current['year'],
            growth=current['revenue']/prior['revenue']-1)
            for prior, current in zip(history, history[1:])
            if current['year'] == prior['year']+1 and prior['revenue'] > 0]
        item['forecast_missing'] = audit['forecast_missing']
        item['assumption_selection'] = audit.get('selection')
        for variant, saved in row['variants'].items():
            if variant not in ('discount_only', 'joint_candidate', 'evidence_selected'):
                raise ValueError('unknown candidate')
            if saved['status'] != 'calculated':
                item['variants'][variant] = saved
                continue
            report = read(key+'-'+variant+'-result.json')
            if report['inputs']['ticker'] != ticker or report['reference_snapshot']['id'] != protocol['metadata']['reference_snapshot_id']:
                raise ValueError('saved identity/reference mismatch')
            if report['final']['value_per_share'] != saved['value_per_share']:
                raise ValueError('saved summary/result mismatch')
            if variant == 'evidence_selected':
                from tools.select_native_assumptions import select
                selection = audit['selection']
                expected_payload, expected_selection = select(read(key+'-baseline.json'),
                    read(key+'-discount_only-request.json')['inputs'],
                    selection['decisions']['capital']['evidence'], audit['reference_cutoff'],
                    protocol['selection_policy'])
                if (expected_payload is None or selection != expected_selection
                        or read(key+'-evidence_selected-request.json') != expected_payload):
                    raise ValueError('saved selection audit/input mismatch')
                # The native API refreshes this derived comparison metric after WACC.
                expected_inputs = expected_payload['inputs']
                if expected_inputs.get('company_metrics') is not None:
                    expected_inputs['company_metrics']['cost_of_capital'] = report['cost_of_capital']['wacc']
                if report['inputs'] != expected_inputs:
                    raise ValueError('saved selection API/input mismatch')
            if variant == 'joint_candidate':
                forecast, assumptions = audit['forecast'], report['inputs']['valuation_assumptions']
                expected = dict(revenue_growth_next_year=forecast['growth'], revenue_growth_years_2_5=forecast['growth'],
                    operating_margin_next_year=forecast['adjusted_ttm_margin'], target_operating_margin=forecast['adjusted_ttm_margin'],
                    sales_to_capital_high=forecast['sales_to_capital'], sales_to_capital_stable=forecast['sales_to_capital'],
                    stable_growth_rate=forecast['terminal_growth'], roic_stable_override=forecast['terminal_roic'])
                if any(assumptions[key] != value for key,value in expected.items()):
                    raise ValueError('saved forecast audit/input mismatch')
            item['variants'][variant] = review_report(report)
    return dict(contract='native-policy-review-v1', companies=len(results), results=results,
        source_statuses=dict(Counter(r['source_status'] for r in results)),
        approved_forecasts=0, source_protocol=protocol, source_files=files,
        review_runtime=runtime_identity(directory),
        reviewer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = review_directory(args.directory)
    with args.output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
