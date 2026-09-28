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
from engine.module_1_adjustments import capitalize_r_and_d
from datetime import date


def load_annual_evidence(connection, tickers):
    """一次批量读取独立年度宽行；缺EBIT不删除研发和余额。"""
    result = {t:dict(rows=[], cells=[]) for t in tickers}
    marks = ','.join('?' for _ in tickers)
    for row in connection.execute(f"SELECT a.*,c.period_date_annual FROM financials_annual a JOIN companies c USING(ticker) WHERE a.ticker IN ({marks}) ORDER BY a.ticker,a.fy_offset", tickers):
        row = dict(row)
        base = date.fromisoformat(row.pop('period_date_annual')[:10])
        if (base.month,base.day) != (12,31) or row['fy_offset'] < 0:
            raise ValueError('annual evidence period mismatch')
        row['fiscal_year'] = base.year-row['fy_offset']
        result[row['ticker']]['rows'].append(row)
    for row in connection.execute(f"SELECT * FROM export_cells WHERE ticker IN ({marks}) AND series='annual' ORDER BY ticker,period,field", tickers):
        result[row['ticker']]['cells'].append(dict(row))
    return result


def annual_capital_evidence(rows, research_life):
    """只读诊断，金额沿用SQLite百万单位；缺队列不补零，租赁代理另审。"""
    history = {r['fiscal_year']:r for r in rows}
    if len(history) != len(rows) or (research_life is not None and research_life <= 0):
        raise ValueError('duplicate year or invalid research life')
    def valid(value): return value is not None and math.isfinite(value)
    result = []
    for year, row in sorted(history.items(), reverse=True):
        cohort = [history.get(y,{}).get('r_and_d_expense') for y in range(year,year-(research_life or 0)-1,-1)]
        missing = [year-i for i,v in enumerate(cohort) if not valid(v) or v < 0]
        asset = amortization = None
        if research_life is None:
            missing = []; asset = amortization = 0.0
        if research_life is not None and not any(y in missing for y in range(year,year-research_life,-1)):
            _, _, asset = capitalize_r_and_d(cohort[0],cohort[1:research_life],research_life)
        if research_life is not None and not missing:
            _, amortization, _ = capitalize_r_and_d(cohort[0],cohort[1:],research_life)
        margin = None
        if amortization is not None and valid(row['ebit']) and valid(row['revenues']) and row['revenues'] > 0:
            margin = (row['ebit']+(cohort[0] if research_life is not None else 0)-amortization)/row['revenues']
        parts = ('bv_equity','bv_debt','cash_and_marketable_securities')
        absent = [key for key in parts if not valid(row[key])]
        capital = None if absent or asset is None else row['bv_equity']+row['bv_debt']-row['cash_and_marketable_securities']+asset
        minority, investments = row.get('minority_interests'), row.get('cross_holdings')
        consolidated = capital+minority if capital is not None and valid(minority) else None
        operating_proxy = consolidated-investments if consolidated is not None and valid(investments) else None
        scope = dict(status='scope_sensitivity_not_valuation_approved', automatic_adoption=False,
            input_capital_million_cny=capital,
            minority_book_equity_million_cny=minority if valid(minority) else None,
            long_term_investment_proxy_million_cny=investments if valid(investments) else None,
            including_minority_capital_million_cny=consolidated,
            excluding_investment_proxy_capital_million_cny=operating_proxy,
            missing_inputs=([*absent]+(['research_asset'] if asset is None else [])
                +[key for key,value in (('minority_interests',minority),('cross_holdings',investments)) if not valid(value)]),
            boundary='合并利润对应资本应含少数股东权益；长期投资代理不等于已核验非经营资产，亦非市值；EBIT投资收益、现金重叠及租赁范围须另审，不能据此批准ROIC或预测倍率')
        result.append(dict(year=year,research_asset_million_cny=asset,research_amortization_million_cny=amortization,
            research_adjusted_margin=margin,research_adjusted_invested_capital_million_cny=capital,
            research_adjusted_sales_to_capital=row['revenues']/capital if capital is not None and capital > 0 and valid(row['revenues']) else None,
            unavailable_research_cohort_years=missing,missing_balance_inputs=absent,capital_scope_bridge=scope))
    annual = {r['year']: r for r in result}
    for row in result:
        year = row['year']
        prior = annual.get(year-1)
        bridge = dict(from_year=year-1, to_year=year, status='missing_previous_year',
            components=[], capital_change_million_cny=None, revenue_change_million_cny=None,
            revenue_change_per_capital_change=None, automatic_adoption=False,
            boundary='账面资本变动不等于净再投资；归母权益、现金及债务代理范围未闭合，减值、并购、汇兑等影响未分离；同期收入差额比不是项目回报或获批预测倍率')
        row['capital_change_bridge'] = bridge
        if prior is None:
            continue
        for field, sign in (('bv_equity',1), ('bv_debt',1), ('cash_and_marketable_securities',-1), ('research_asset_million_cny',1)):
            before, after = ((prior.get(field),row.get(field)) if field == 'research_asset_million_cny'
                else (history[year-1].get(field),history[year].get(field)))
            bridge['components'].append(dict(field=field, coefficient=sign, opening=before, closing=after,
                contribution_million_cny=sign*(after-before) if valid(before) and valid(after) else None))
        before, after = history[year-1].get('revenues'), history[year].get('revenues')
        if valid(before) and valid(after):
            bridge['revenue_change_million_cny'] = after-before
        bridge['status'] = 'missing_capital_components'
        if any(p['contribution_million_cny'] is None for p in bridge['components']):
            continue
        change = sum(p['contribution_million_cny'] for p in bridge['components'])
        if not math.isclose(change, row['research_adjusted_invested_capital_million_cny']-prior['research_adjusted_invested_capital_million_cny'], rel_tol=1e-10, abs_tol=1e-8):
            raise ValueError('historical capital change does not reconcile')
        bridge['capital_change_million_cny'] = change
        bridge['status'] = 'nonpositive_capital_change' if change <= 0 else 'missing_revenue'
        if change > 0 and bridge['revenue_change_million_cny'] is not None:
            bridge['status'] = 'arithmetic_complete_not_valuation_approved'
            bridge['revenue_change_per_capital_change'] = bridge['revenue_change_million_cny']/change
    return result


def review_report(body, annual_evidence=None):
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
    research = dict(inputs.historical_research_expenses)
    research.update({f.fiscal_year:f.r_and_d_expense for f in inputs.raw_financials if counts[f.fiscal_year] == 1})
    model_annual_evidence = []
    for index, f in enumerate(inputs.raw_financials[:10]):
        cohort = range(f.fiscal_year-inputs.adjustment_inputs.amortization_period_n, f.fiscal_year+1)
        unavailable = [year for year in cohort if research.get(year) is None
            or not math.isfinite(research[year]) or research[year] < 0] if inputs.adjustment_inputs.has_r_and_d else []
        margin = replay.cashflow.historical_margin_by_year[index]
        ratio = replay.cashflow.historical_s_c_by_year[index]
        model_annual_evidence.append(dict(year=f.fiscal_year,
            research_adjusted_margin=margin, research_adjusted_sales_to_capital=ratio,
            unavailable_research_cohort_years=unavailable,
            missing_balance_inputs=[name for name in ('bv_equity','bv_debt','cash_and_marketable_securities')
                if getattr(f,name) is None],
            boundary='原生历史诊断仅按逐年研发调整；租赁及现金/投资代理口径未因此闭合'))
    if annual_evidence is not None:
        base_year = date.fromisoformat(inputs.period_date_10k[:10]).year
        if (any(r['ticker'] != inputs.ticker or r['fiscal_year'] != base_year-r['fy_offset']
                or r['fy_offset'] < 0 for r in annual_evidence['rows'])
                or any(c['ticker'] != inputs.ticker or c['series'] != 'annual' for c in annual_evidence['cells'])):
            raise ValueError('independent annual evidence identity/period mismatch')
        annual_evidence_rows = annual_capital_evidence(annual_evidence['rows'], inputs.adjustment_inputs.amortization_period_n if inputs.adjustment_inputs.has_r_and_d else None)
    else:
        annual_evidence_rows = None
    basis_comparison = []
    if annual_evidence is not None:
        independent = {r['fiscal_year']:r for r in annual_evidence['rows']}
        for year in sorted(set(independent) | set(counts), reverse=True):
            model_rows = [r for r in inputs.raw_financials if r.fiscal_year == year]
            row = independent.get(year)
            differences = []
            status = 'compared_values_not_semantic_equivalence'
            if len(model_rows) != 1 or row is None:
                status = 'missing_or_ambiguous_model_year' if len(model_rows) != 1 else 'missing_independent_year'
            else:
                for field in ('revenues','ebit','r_and_d_expense','bv_equity','bv_debt',
                              'cash_and_marketable_securities','minority_interests','cross_holdings'):
                    model_value, independent_value = getattr(model_rows[0],field), row.get(field)
                    if model_value != independent_value:
                        differences.append(dict(field=field, model_input=model_value, sqlite_input=independent_value))
            basis_comparison.append(dict(year=year,status=status,differences=differences))
    peers = []
    for peer in (inputs.industry_data, inputs.industry_data_global):
        if peer is not None:
            peers.append(dict(industry=peer.industry_name, region=peer.region,
                pretax_unadjusted_operating_margin=peer.pretax_operating_margin,
                pretax_lease_research_adjusted_operating_margin=peer.pretax_lease_research_adjusted_operating_margin,
                aftertax_lease_research_adjusted_operating_margin=peer.aftertax_lease_research_adjusted_operating_margin,
                historical_sales_to_capital=peer.sales_to_capital,
                comparable_for_adjusted_target_selection=False,
                reason='company_lease_scope_research_life_and_industry_fit_require_review'))
    historical = []
    for i, f in enumerate(history):
        prior = history[i-1] if i and history[i-1].fiscal_year == f.fiscal_year-1 else None
        historical.append(dict(year=f.fiscal_year, revenue_million_cny=f.revenues,
            growth=f.revenues/prior.revenues-1 if prior and prior.revenues and prior.revenues > 0 else None,
            model_input_ebit_margin=f.ebit/f.revenues if f.revenues else None))
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
            window['model_input_ebit_margin'] = (window['ebit']/window['revenues']
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
    # 对齐共享引擎的终值利润/税口径，显式解释资本规则切换；不依赖更老财务。
    margin = a.target_operating_margin
    if margin is None:
        margin = a.operating_margin_next_year
    if margin is None:
        margin = adjusted.adjusted_ebit/raw.revenues if raw.revenues else 0.0
    effective = a.effective_tax_rate_override_years_1_5
    if effective is None:
        effective = macro.tax_rate_effective if macro.tax_rate_effective is not None else macro.tax_rate_marginal
    terminal_tax = effective if a.override_tax_convergence else macro.tax_rate_marginal
    if a.annual_forecast is not None:
        margin, terminal_tax = a.annual_forecast[-1].margin, a.annual_forecast[-1].tax
    terminal_revenue = rows[-1]['revenue_million_cny']*(1+g)
    terminal_nopat = terminal_revenue*margin*(1-terminal_tax)
    terminal_investment = terminal_nopat*g/terminal_roic if g > 0 else 0.0
    if not math.isclose(terminal_nopat-terminal_investment, terminal_fcff, rel_tol=1e-10, abs_tol=1e-8):
        raise ValueError('terminal economic bridge differs from engine')
    # 沿用原投入滞后，把同一个终值年度与继续沿用资本倍率的反事实相比。
    funded_delta = rows[-1]['revenue_million_cny']*(1+g)**lag*g
    continued_investment = funded_delta/sc_stable
    equivalent_ratio = funded_delta/terminal_investment if terminal_investment > 0 and funded_delta > 0 else None
    spreads = [dict(investment_year=r['year'], funded_year=r['capital_funding']['revenue_year'],
        constant_margin_return=r['incremental_return_bridge']['constant_nopat_margin_incremental_return'],
        initial_wacc=replay.cost_of_capital.wacc,
        spread=r['incremental_return_bridge']['constant_nopat_margin_incremental_return']-replay.cost_of_capital.wacc)
        for r in rows if r['incremental_return_bridge']['constant_nopat_margin_incremental_return'] is not None]
    economic_checks = dict(version='native-economic-consistency-v1', automatic_approval=False,
        marginal_return_screen=dict(years=spreads, below_initial_wacc_years=[r['investment_year'] for r in spreads if r['spread'] < 0],
            boundary='恒定税后利润率分量相对初始WACC的筛查；不是项目IRR，不认证时间变化风险或投资因果'),
        terminal_transition=dict(nopat_million_cny=terminal_nopat,
            reinvestment_million_cny=terminal_investment, continued_capital_ratio_reinvestment_million_cny=continued_investment,
            reinvestment_rule_change_million_cny=terminal_investment-continued_investment,
            fcff_rule_change_million_cny=continued_investment-terminal_investment,
            equivalent_sales_to_capital=equivalent_ratio, forecast_sales_to_capital=sc_stable,
            nopat_change_from_last_year_million_cny=terminal_nopat-rows[-1]['nopat_million_cny'],
            reinvestment_change_from_last_year_million_cny=terminal_investment-rows[-1]['reinvestment_million_cny'],
            fcff_change_from_last_year_million_cny=terminal_fcff-rows[-1]['fcff_million_cny'],
            requires_capital_transition_basis=not math.isclose(terminal_investment, continued_investment, rel_tol=1e-10, abs_tol=1e-8),
            boundary='同一终值年度、同一滞后下比较两条再投资规则；等价倍率是代数诊断，不自动替换行业代理或终值ROIC'))
    missing = [name for name in ('capex', 'd_a', 'change_in_noncash_wc') if getattr(raw, name) is None]
    return dict(status='requires_analyst_judgment', automatic_adoption=False,
        unit='million_CNY', ticker=inputs.ticker, economic_checks=economic_checks,
        model_input_history=historical,
        sustainability_evidence=dict(status='requires_company_and_accounting_basis',
            model_basis='saved_model_inputs_after_any_policy_or_user_override',
            independent_basis='sqlite_export_before_native_policy_or_user_override',
            cross_basis_merge_allowed=False,
            input_basis_comparison=basis_comparison,
            annual_rows=model_annual_evidence,
            independent_annual_rows=annual_evidence_rows,
            independent_source='sqlite_annual_rows_and_export_cells' if annual_evidence_rows is not None else None,
            supplied_annual_years=sorted(counts),
            supplementary_research_years=sorted(inputs.historical_research_expenses),
            absent_annual_years=[y for y in range(min(counts),max(counts)+1) if y not in counts] if counts else [],
            duplicate_annual_years=[y for y,n in sorted(counts.items()) if n>1],
            research_adjusted_margin_years=sum(r['research_adjusted_margin'] is not None for r in model_annual_evidence),
            research_adjusted_capital_years=sum(r['research_adjusted_sales_to_capital'] is not None for r in model_annual_evidence),
            industry_references=peers,
            boundary='缺项只说明本次估值输入不足，不证明标准库或上游无数据；历史与行业参考均不自动证明未来持续性'),
        model_input_recent_window=recent,
        margin_bridge=dict(model_input_ebit=raw.ebit, research_expense=raw.r_and_d_expense,
            research_amortization=adjusted.amortization_r_and_d,
            lease_adjustment=adjusted.lease_adjustment_to_ebit,
            adjusted_ebit=adjusted.adjusted_ebit,
            model_input_margin=raw.ebit/raw.revenues if raw.revenues else None,
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


def growth_duration_effects(variants, reviews):
    """只对已按统一规则核验的同利润率场景比较，不按价格挑场景。"""
    results = []
    for margin in ('hold', 'history'):
        short, long = f'scenario_fade3_{margin}', f'scenario_fade10_{margin}'
        if any(variants.get(name, {}).get('status') != 'calculated' for name in (short,long)):
            continue
        revenue_delta = reviews[long]['forecast'][-1]['revenue_million_cny']-reviews[short]['forecast'][-1]['revenue_million_cny']
        value_delta = variants[long]['value_per_share']-variants[short]['value_per_share']
        results.append(dict(from_scenario=short, to_scenario=long,
            revenue_year10_change_million_cny=revenue_delta, value_per_share_change=value_delta,
            first5_reinvestment_change_million_cny=reviews[long]['capital']['first_five_years_reinvestment_million_cny']-reviews[short]['capital']['first_five_years_reinvestment_million_cny'],
            higher_revenue_lower_value=revenue_delta > 0 and value_delta < 0,
            selected_scenario=None,
            boundary='已核验同一利润率/税/WACC/资本代理和股权口径的增长路径比较；更高价值不自动证明路径更合理'))
    return results


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
    from tools.select_native_assumptions import prediction_summary
    if summary.get('prediction_summary') != prediction_summary(summary['results']):
        raise ValueError('saved prediction summary mismatch')
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
        from tools.select_native_assumptions import prediction_check
        expected_check=prediction_check(read(key+'-baseline.json'))
        if read(key+'-prediction-check.json') != expected_check or row.get('prediction_check') != expected_check:
            raise ValueError('saved prediction diagnostic mismatch')
        item['historical_standard_revenue'] = audit['forecast'].get('annual_revenue', [])
        history = item['historical_standard_revenue']
        item['historical_standard_revenue_growth'] = [dict(year=current['year'],
            growth=current['revenue']/prior['revenue']-1)
            for prior, current in zip(history, history[1:])
            if current['year'] == prior['year']+1 and prior['revenue'] > 0]
        item['forecast_missing'] = audit['forecast_missing']
        item['assumption_selection'] = audit.get('selection')
        expected_scenarios = {}
        has_selected = audit.get('selection', {}).get('status') == 'selected_conditional'
        if has_selected and ('evidence_selected' not in row['variants'] or audit.get('scenario_generation') is None):
            raise ValueError('saved selected scenario family missing')
        if audit.get('scenario_generation') is not None:
            from tools.select_native_assumptions import scenario_payloads, SCENARIO_RULES
            if protocol.get('scenario_rules') != SCENARIO_RULES:
                raise ValueError('saved scenario rules mismatch')
            expected_scenarios, expected_audit = scenario_payloads(read(key+'-baseline.json'),
                read(key+'-evidence_selected-request.json'))
            if expected_audit != audit['scenario_generation'] or expected_audit != row.get('scenario_generation'):
                raise ValueError('saved scenario audit mismatch')
            if set(name for name in row['variants'] if name.startswith('scenario_')) != set(expected_scenarios):
                raise ValueError('saved scenario set mismatch')
        for variant, saved in row['variants'].items():
            if variant not in ('discount_only', 'joint_candidate', 'evidence_selected') and variant not in expected_scenarios:
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
            if variant in expected_scenarios:
                expected = expected_scenarios[variant]
                if read(key+'-'+variant+'-request.json') != expected:
                    raise ValueError('saved scenario request mismatch')
                if expected['inputs'].get('company_metrics') is not None:
                    expected['inputs']['company_metrics']['cost_of_capital'] = report['cost_of_capital']['wacc']
                if report['inputs'] != expected['inputs']:
                    raise ValueError('saved scenario API/input mismatch')
            if variant == 'joint_candidate':
                forecast, assumptions = audit['forecast'], report['inputs']['valuation_assumptions']
                expected = dict(revenue_growth_next_year=forecast['growth'], revenue_growth_years_2_5=forecast['growth'],
                    operating_margin_next_year=forecast['adjusted_ttm_margin'], target_operating_margin=forecast['adjusted_ttm_margin'],
                    sales_to_capital_high=forecast['sales_to_capital'], sales_to_capital_stable=forecast['sales_to_capital'],
                    stable_growth_rate=forecast['terminal_growth'], roic_stable_override=forecast['terminal_roic'])
                if any(assumptions[key] != value for key,value in expected.items()):
                    raise ValueError('saved forecast audit/input mismatch')
            item['variants'][variant] = review_report(report, read(key+'-annual-evidence.json'))
        item['growth_duration_effects'] = growth_duration_effects(row['variants'],item['variants'])
    return dict(contract='native-policy-review-v2', companies=len(results), results=results,
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
