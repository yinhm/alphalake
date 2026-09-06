"""茅台标准链 → 显式 TTM → 目标编排器；行业政策仅在独立研究层对照。"""
import argparse
import csv
import hashlib
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
REVISION = '7ce156a7c6d568d41f480919d84b998bee599d54'


def rows(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def near(a, b):
    assert math.isclose(a, b, rel_tol=1e-11, abs_tol=1e-7), (a, b)


def main(write=False):
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=TARGET, text=True).strip() == REVISION
    assert not subprocess.check_output(['git', 'diff', 'HEAD', '--', 'backend/engine'], cwd=TARGET)
    subprocess.run([sys.executable, str(CHAIN / 'verify.py')], check=True)
    sys.path.insert(0, str(TARGET / 'backend'))
    from engine.data_dictionary import (RawFinancials, PreparedTTM, CompanyValuationInput,
        MacroInputs, IndustryData, MethodologyChoices, ValuationAssumptions)
    from engine.ltm_calculator import compute_ltm_financials
    from engine.orchestrator import run_full_valuation

    # 该字段为无量纲比例，其余为元或股。不可对整张表盲目除以百万。
    i = {r['item']: float(r['value']) / (1 if r['item'] == 'operating_minority_fraction_proxy' else 1e6)
         for r in rows(CHAIN / 'inputs.csv') if r['code'] == '600519'}
    facts = {(r['period'], r['field']): float(r['value']) / 1e6
             for r in rows(CHAIN / 'facts.csv') if r['code'] == '600519'}
    windows = {r['field']: float(r['value']) / 1e6
               for r in rows(CHAIN / 'windows.csv') if r['code'] == '600519'}
    periods = sorted({p for p, _ in facts}, reverse=True)
    quarters = [RawFinancials(fiscal_year=int(p[:4]), revenues=facts[p, 'FN230'],
                              ebit=facts[p, 'FN231']) for p in periods]
    annual = RawFinancials(fiscal_year=2025, revenues=sum(facts[p, 'FN230'] for p in periods if p.startswith('2025')),
                          ebit=facts['2025-12-31', 'FN86'], capex=facts['2025-12-31', 'FN114'])
    rotated = compute_ltm_financials(annual, quarters, 2)
    near(rotated.revenues, i['revenue_ttm'])
    assert rotated.capex is None
    try:
        compute_ltm_financials(annual, quarters[:2], 2)
    except ValueError:
        pass
    else:
        raise AssertionError('缺季不应返回全年流量')
    # EBIT 为酒业政策代理，不将合并 DA/资本开支/现金冒充精确酒业拆分。
    raw = RawFinancials(fiscal_year=2026, revenues=i['revenue_ttm'], ebit=i['ebit_ttm'], shares_outstanding=i['shares'])
    provenance = {n: hashlib.sha256((CHAIN / n).read_bytes()).hexdigest() for n in
                  ('facts.csv', 'windows.csv', 'inputs.csv', 'input-audit.csv', '600519-equity-bridge.csv')}
    prepared = PreparedTTM(financials=raw, period_start='2025-07-01', period_end='2026-06-30',
        information_as_of='2026-09-06T00:00:00+00:00', currency='CNY',
        money_unit='million_reporting_currency', shares_unit='million_shares', provenance=provenance)
    scenarios = json.loads((ROOT.parent / 'moutai-valuation-2026/assumptions.json').read_text())['scenarios']
    bridges = rows(CHAIN / '600519-equity-bridge.csv')
    old_forecast = rows(CHAIN / '600519-forecast.csv')
    old_prices = {r['scenario']: float(r['per_share']) for r in rows(CHAIN / 'valuation.csv') if r['code'] == '600519'}
    requests, results = [], []
    for scenario in scenarios:
        name = scenario['name']
        s = {k: float(v) for k, v in scenario.items() if k != 'name'}
        growth = (1 + s['volume_growth']) * (1 + s['price_mix_growth']) - 1
        # 明示统一资本倍率的实验代理，不声称等价于五年储酒＋两年长期投入。
        capital_ratio = sum(s[k] for k in ('inventory_ratio', 'long_capital_ratio', 'other_wc_ratio', 'operating_cash_ratio'))
        assumptions = ValuationAssumptions(projection_years=10, high_growth_years=5,
            revenue_growth_next_year=growth, revenue_growth_years_2_5=growth,
            operating_margin_next_year=raw.ebit / raw.revenues, target_operating_margin=s['margin'],
            margin_convergence_year=5, sales_to_capital_high=1 / capital_ratio, sales_to_capital_stable=1 / capital_ratio,
            override_reinvestment_lag=True, reinvestment_lag_years=0,
            cost_of_capital_stable_override=s['wacc'], roic_stable_override=s['terminal_roic'],
            override_growth_perpetuity=True, growth_perpetuity_rate=s['terminal_growth'])
        payload = CompanyValuationInput(ticker='600519', reporting_currency='CNY', prepared_ttm=prepared,
            macro_inputs=MacroInputs(risk_free_rate=.03, equity_risk_premium=.05,
                tax_rate_marginal=s['tax_rate'], tax_rate_effective=s['tax_rate']),
            industry_data=IndustryData(industry_name='unused_direct_wacc', beta_u=0),
            methodology_choices=MethodologyChoices(cost_of_capital_approach='direct', wacc_direct_input=s['wacc']),
            valuation_assumptions=assumptions)
        encoded = payload.model_dump_json()
        report = run_full_valuation(CompanyValuationInput.model_validate_json(encoded))
        near(report.ltm_financials.revenues, raw.revenues)
        near(report.adjusted.adjusted_ebit, raw.ebit)
        assert report.adjusted.pv_of_operating_leases == 0
        assert report.cashflow.fcff is None and report.cashflow.fcfe is None
        assert report.multiples.pe_ratio_intrinsic is None and report.multiples.ev_sales_intrinsic is None
        assert report.dcf.value_of_equity is None and report.final.value_per_share is None
        # 独立复算目标三组十年路径，不调用其辅助函数。
        rev, forecast = raw.revenues, []
        original = [r for r in old_forecast if r['scenario'] == name]
        for year in range(1, 11):
            g = growth if year <= 5 else growth + (s['terminal_growth'] - growth) * (year - 5) / 5
            revenue = rev * (1 + g)
            margin0 = raw.ebit / raw.revenues
            margin = margin0 if year == 1 else margin0 + (s['margin'] - margin0) * min(year / 5, 1)
            reinvestment = (revenue - rev) * capital_ratio
            fcff = revenue * margin * (1 - s['tax_rate']) - reinvestment
            for a, b in [(report.dcf.revenue_projections[year-1], revenue),
                (report.dcf.ebit_projections[year-1], revenue * margin),
                (report.dcf.reinvestment_projections[year-1], reinvestment),
                (report.dcf.fcff_projections[year-1], fcff)]:
                near(a, b)
            near(revenue, float(original[year-1]['revenue']) / 1e6)
            forecast.append(dict(year=year, revenue=revenue, margin=margin, reinvestment=reinvestment, fcff=fcff,
                original_margin=float(original[year-1]['ebit_margin']),
                original_reinvestment=float(original[year-1]['reinvestment']) / 1e6))
            rev = revenue
        terminal = rev * (1 + s['terminal_growth']) * s['margin'] * (1 - s['tax_rate'])
        tv = terminal * (1 - s['terminal_growth'] / s['terminal_roic']) / (s['wacc'] - s['terminal_growth'])
        enterprise = sum(r['fcff'] / (1 + s['wacc'])**r['year'] for r in forecast) + tv / (1 + s['wacc'])**10
        near(enterprise, report.dcf.value_of_operating_assets)
        bridge = {r['item']: float(r['value']) / 1e6 for r in bridges if r['scenario'] == name}
        # 原桥接少数股权项随经营价值变化，不能复制旧桥接常数来套新 EV。
        minority = -bridge['operating_minority_proxy'] / bridge['liquor_enterprise_proxy']
        assert abs(minority / s['minority_scale'] - i['operating_minority_fraction_proxy']) < .00000051
        fixed = sum(v for k, v in bridge.items() if k not in ('liquor_enterprise_proxy', 'operating_minority_proxy'))
        price = lambda ev: (ev * (1 - minority) + fixed) / i['shares']
        assert abs(price(bridge['liquor_enterprise_proxy']) - old_prices[name]) < .00000051
        near(bridge['owned_finance_equity_value'], i['finance_book_equity'] * .51 * s['finance_pb'])
        near(bridge['full_finance_book_equity_removed'], -i['finance_book_equity'])
        # 从标准链模型输入独立复算金融池，不复制旧桥接的加总结果。
        pool = (i['financial_gross'] * s['financial_asset_recovery'] + i['risk_assets'] * s['risk_asset_recovery']
                - i['external_deposits'] - i['finance_book_equity'] - raw.revenues * s['operating_cash_ratio']
                - i['income_tax_payable'] - i['lease_debt'])
        expected_fixed = pool * (1 - minority) + i['finance_book_equity'] * .51 * s['finance_pb']
        near(fixed, expected_fixed)
        # 故意漏掉整块财务公司权益扣除，桥接重建必须拒绝。
        try:
            near(fixed + i['finance_book_equity'], expected_fixed)
        except AssertionError:
            pass
        else:
            raise AssertionError('财务公司重复加回未被捕获')
        if name == 'central':
            lag_results = []
            for lag in (3, 5):
                alternative = CompanyValuationInput.model_validate_json(encoded)
                alternative.valuation_assumptions.reinvestment_lag_years = lag
                lag_results.append(run_full_valuation(alternative).dcf.reinvestment_projections)
            assert lag_results[0] == lag_results[1], '目标 lag 限制改变，需重新审核行业政策'
            expected_five = (forecast[5]['revenue'] - forecast[4]['revenue']) * capital_ratio
            assert not math.isclose(lag_results[1][0], expected_five, rel_tol=1e-6)
            lag_probe = dict(requested_lag_5_first_reinvestment=lag_results[1][0],
                             actual_same_as_lag_3=True, true_lag_5_first_reinvestment=expected_five)
        requests.append(dict(scenario=name, request={'inputs': json.loads(encoded)}))
        results.append(dict(scenario=name, target_enterprise=enterprise,
            original_enterprise=bridge['liquor_enterprise_proxy'], target_forecast_with_original_policy_bridge=price(enterprise),
            original_per_share=old_prices[name], operating_minority_fraction=minority,
            historical_fcff=report.cashflow.fcff, final_engine_per_share=report.final.value_per_share,
            warnings=report.warnings, forecast=forecast))
    output = dict(target_commit=REVISION, status='shared_orchestrator_verified_industry_policy_not_equivalent_not_market_target',
        statement_scope='provider_default; EBIT is liquor policy proxy, not exact deconsolidation',
        source_sha256=provenance, consolidated_reference_not_liquor_inputs=dict(capex_ttm=windows['FN114'], da_ttm=i['da_ttm']),
        ltm_revenue=rotated.revenues, incomplete_quarter_capex=rotated.capex,
        lag_probe=lag_probe, scenarios=results)
    for filename, obj in [('requests.json', requests), ('result.json', output)]:
        content = json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        if write:
            (ROOT / filename).write_text(content)
        else:
            assert (ROOT / filename).read_text() == content, filename + ' differs'
    print('通过：茅台标准链/PDF、真实 JSON 编排器、三情景独立复算、缺项与储酒期限反例。')
    print(json.dumps([{k: r[k] for k in ('scenario', 'target_forecast_with_original_policy_bridge', 'original_per_share')} for r in results]))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    main(parser.parse_args().write)
