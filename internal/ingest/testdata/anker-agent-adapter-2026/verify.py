"""安克对接实验：实际目标模块、缺失反例、预测政策差异；不是生产 API 适配器。"""
import argparse
import ast
import csv
import hashlib
import inspect
import json
import math
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[3]
CHAIN = ROOT.parent / 'valuation-chain-2026'
TARGET = REPO / 'workspace/Investment_Valuation_Agent'


def rows(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def near(a, b):
    assert math.isclose(a, b, rel_tol=1e-11, abs_tol=1e-7), (a, b)


def require_fields(raw, names):
    missing = [name for name in names if getattr(raw, name) is None]
    if missing:
        raise ValueError('缺少输入：' + ','.join(missing))


def main(write=False):
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=TARGET, text=True).strip() == 'c4b86e5782371ada63e03972ec13e0fd32be937b'
    assert not subprocess.check_output(['git', 'diff', 'HEAD', '--', 'backend/engine'], cwd=TARGET)
    subprocess.run([sys.executable, str(CHAIN / 'verify.py')], check=True)
    sys.path.insert(0, str(TARGET / 'backend'))
    from engine.data_dictionary import (RawFinancials, AdjustmentInputs, MacroInputs,
        IndustryData, MethodologyChoices, ValuationAssumptions, CompanyValuationInput)
    from engine.ltm_calculator import compute_ltm_financials
    from engine.module_1_adjustments import compute_adjustments
    from engine.module_2_risk import compute_cost_of_capital
    from engine.module_3_cashflow import compute_cashflow_and_growth
    from engine.module_4_dcf import compute_dcf
    from engine.orchestrator import run_full_valuation

    i = {r['item']: float(r['value']) / 1e6 for r in rows(CHAIN / 'inputs.csv') if r['code'] == '300866'}
    # 每股转股价不缩放，金额和股数同时缩放。
    i['conversion_price'] *= 1e6
    facts = {(r['period'], r['field']): float(r['value']) / 1e6
             for r in rows(CHAIN / 'facts.csv') if r['code'] == '300866'}
    windows = {r['field']: float(r['value']) / 1e6
               for r in rows(CHAIN / 'windows.csv') if r['code'] == '300866'}
    periods = sorted({p for p, f in facts}, reverse=True)
    quarterly = [RawFinancials(fiscal_year=int(p[:4]), revenues=facts[p, 'FN230'],
                              ebit=facts[p, 'FN231']) for p in periods]
    annual = RawFinancials(fiscal_year=2025,
        revenues=sum(facts[p, 'FN230'] for p in periods if p.startswith('2025')),
        ebit=facts['2025-12-31', 'FN86'], capex=facts['2025-12-31', 'FN114'])
    ltm = compute_ltm_financials(annual, quarterly, 2)
    near(ltm.revenues, i['revenue_ttm'])
    # 源季度 EBIT 与累计营业利润的浮点编码容差由上游校验负责；这里不混入政策 EBIT。
    near(ltm.capex, annual.capex)
    assert not math.isclose(ltm.capex, windows['FN114'], rel_tol=1e-6)
    short = compute_ltm_financials(annual, quarterly[:2], 2)
    near(short.revenues, annual.revenues)
    assert not math.isclose(short.revenues, i['revenue_ttm'], rel_tol=1e-6)

    macro = MacroInputs(risk_free_rate=.03, equity_risk_premium=.06,
                        tax_rate_marginal=.25, tax_rate_effective=.20)
    industry = IndustryData(industry_name='unused_direct_wacc', beta_u=0)
    method = MethodologyChoices(cost_of_capital_approach='direct', wacc_direct_input=.09)
    adjustment = AdjustmentInputs(has_r_and_d=False, has_operating_leases=False)
    # 真实年度数据跑完整入口仅用于复现缺失处理，所有结果隔离，不发布为估值。
    unsafe = run_full_valuation(CompanyValuationInput(ticker='300866', raw_financials=[annual],
        macro_inputs=macro, industry_data=industry, methodology_choices=method))
    assert annual.d_a is None and annual.change_in_noncash_wc is None
    near(unsafe.cashflow.fcff, annual.ebit * .8 - annual.capex)

    raw = RawFinancials(fiscal_year=2026, revenues=i['revenue_ttm'], ebit=i['ebit_ttm'],
        capex=windows['FN114'], d_a=i['da_ttm'], bv_equity=facts['2026-06-30', 'FN72'],
        bv_debt=i['debt'], cash_and_marketable_securities=i['cash_equivalents'],
        minority_interests=i['minority'], shares_outstanding=i['shares'])
    adjusted = compute_adjustments(raw, adjustment, .09)
    near(adjusted.adjusted_ebit, raw.ebit)
    near(adjusted.adjusted_mv_debt, raw.bv_debt)
    cost = compute_cost_of_capital(adjusted, macro, industry, 0, method)
    # 缺失不能归零：只调用一次 M3 记录反例，其结果不得进入预测或导出历史 FCFF。
    unsafe_cf = compute_cashflow_and_growth(adjusted, raw, adjustment, cost, macro=macro)
    near(unsafe_cf.fcff, raw.ebit * .8 - raw.capex + raw.d_a)
    missing = [f for f in ('change_in_noncash_wc', 'net_debt_issued') if getattr(raw, f) is None]
    assert missing == ['change_in_noncash_wc', 'net_debt_issued']
    try:
        require_fields(raw, ('capex', 'd_a', 'change_in_noncash_wc'))
    except ValueError as error:
        assert 'change_in_noncash_wc' in str(error)
    else:
        raise AssertionError('不完整历史 FCFF 未阻断')
    required = ('revenues', 'ebit', 'bv_debt', 'bv_equity', 'cash_and_marketable_securities',
                'minority_interests', 'shares_outstanding')
    require_fields(raw, required)
    for name in required:
        try:
            require_fields(raw.model_copy(update={name: None}), required)
        except ValueError:
            pass
        else:
            raise AssertionError(('预测缺项未阻断', name))

    # 当前 M4 完全不读取 cf_metrics；锁定这一事实，允许实验传 None，绝不伪造历史 FCFF 对象。
    tree = ast.parse(inspect.getsource(compute_dcf))
    assert not any(isinstance(n, ast.Name) and n.id == 'cf_metrics' for n in ast.walk(tree))
    base_margin = raw.ebit / raw.revenues
    assumptions = ValuationAssumptions(projection_years=10, high_growth_years=5,
        revenue_growth_next_year=.12, revenue_growth_years_2_5=.12,
        operating_margin_next_year=base_margin, target_operating_margin=.10,
        margin_convergence_year=5, sales_to_capital_high=3, sales_to_capital_stable=3,
        override_reinvestment_lag=True, reinvestment_lag_years=0,
        cost_of_capital_stable_override=.09, roic_stable_override=.12,
        override_growth_perpetuity=True, growth_perpetuity_rate=.03)
    result = compute_dcf(None, cost, adjusted, raw, assumptions, macro)
    # 独立复算目标实际路径，未调用其路径辅助函数。
    rev = raw.revenues
    forecast = []
    for year in range(1, 11):
        growth = .12 if year <= 5 else .12 - .09 * (year - 5) / 5
        next_rev = rev * (1 + growth)
        margin = base_margin if year == 1 else base_margin + (.10 - base_margin) * min(year / 5, 1)
        tax = .20 if year <= 5 else .20 + .01 * (year - 5)
        reinvestment = (next_rev - rev) / 3
        fcff = next_rev * margin * (1 - tax) - reinvestment
        for actual, expected in [(result.revenue_projections[year-1], next_rev),
                (result.ebit_projections[year-1], next_rev * margin),
                (result.reinvestment_projections[year-1], reinvestment),
                (result.fcff_projections[year-1], fcff)]:
            near(actual, expected)
        forecast.append(dict(year=year, revenue=next_rev, margin=margin, tax=tax,
                             reinvestment=reinvestment, fcff=fcff))
        rev = next_rev
    tv = rev * 1.03 * .10 * .75 * (1 - .03 / .12) / (.09 - .03)
    enterprise = sum(r['fcff'] / 1.09**r['year'] for r in forecast) + tv / 1.09**10
    near(result.value_of_operating_assets, enterprise)
    # 仅消费 M4 企业价值；它的简化股权桥接和投入资本诊断不作为本实验结论。
    bridge = {r['item']: float(r['value']) / 1e6 for r in rows(CHAIN / '300866-equity-bridge.csv') if r['scenario'] == 'central'}
    offset = sum(v for k, v in bridge.items() if k != 'operating_enterprise_value')
    def price(ev):
        equity = ev + offset
        shares = i['shares'] * 1.01
        return min(equity / shares, (equity + i['convertible_debt'] + i['convertible_equity_book']) /
                   (shares + i['convertible_face'] / i['conversion_price']))
    baseline = next(float(r['per_share']) for r in rows(CHAIN / 'valuation.csv')
                    if r['code'] == '300866' and r['scenario'] == 'central')
    assert abs(price(bridge['operating_enterprise_value']) - baseline) < .00000051
    # 同量纲变换不改变每股值；只错缩股数会错一百万倍。
    near((enterprise * 1e6) / (i['shares'] * 1e6), enterprise / i['shares'])
    assert not math.isclose(enterprise / (i['shares'] * 1e6), enterprise / i['shares'])
    original = [r for r in rows(CHAIN / '300866-forecast.csv') if r['scenario'] == 'central']
    for f, old in zip(forecast, original):
        f.update(alphalake_revenue=float(old['revenue']) / 1e6,
                 alphalake_tax=float(old['tax_rate']),
                 alphalake_margin=float(old['ebit_margin']),
                 alphalake_reinvestment=float(old['reinvestment']) / 1e6)
    output = dict(target_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=TARGET, text=True).strip(),
        status='forecast_module_experiment_not_api_acceptance_not_market_target',
        contract=dict(code='300866', currency='CNY', money_unit='million_CNY', shares_unit='million_shares',
            flow_period='2025-07-01/2026-06-30', balance_date='2026-06-30',
            information_as_of='2026-09-06T00:00:00+00:00', statement_scope='provider_default',
            entry='prepared TTM directly to M1/M2/M4; never FY or LTM rotation',
            wacc='explicit 9% assumption; industry beta and market value unused',
            adjustments='RD expensed; leases already accounted; no repeated capitalization'),
        source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [CHAIN / n for n in ('facts.csv', 'windows.csv', 'inputs.csv', 'input-audit.csv')]},
        prepared_base=raw.model_dump(), assumptions=assumptions.model_dump(),
        historical_fcff=None, historical_fcfe=None, missing=missing,
        probes=dict(ltm_revenue=ltm.revenues, incomplete_capex_returned=ltm.capex,
            correct_ttm_capex=windows['FN114'], short_quarters_revenue=short.revenues,
            unsafe_full_engine_fcff=unsafe.cashflow.fcff, unsafe_ttm_fcff=unsafe_cf.fcff),
        target_operating_value=enterprise, alphalake_operating_value=bridge['operating_enterprise_value'],
        target_forecast_with_alphalake_bridge_per_share=price(enterprise),
        alphalake_per_share=baseline, forecast=forecast)
    expected = json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if write:
        (ROOT / 'result.json').write_text(expected)
    else:
        assert (ROOT / 'result.json').read_text() == expected, '实验结果改变，先审核差异'
    print('通过：实际 Pydantic/LTM/完整引擎缺失反例、M1/M2/M4、十年独立复算与原桥接对照。')
    print(json.dumps({k: output[k] for k in ('target_forecast_with_alphalake_bridge_per_share', 'alphalake_per_share', 'probes')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    main(parser.parse_args().write)
