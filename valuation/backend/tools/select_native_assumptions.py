"""依证据选择原生条件假设；公司事实、行业代理与未验证预测分别留痕。"""
from datetime import date, datetime
import math

from engine.data_dictionary import CompanyValuationInput
from tools.review_native_policy import review_report


def select(baseline, discount_inputs, reference_rows, reference_cutoff, policy):
    expected = dict(version='native-assumption-selection-v2', growth='latest_comparable_revenue_growth',
        contraction='conditional_recovery_with_implied_capital_release',
        high_growth_years=1, projection_years=10, margin='hold_positive_adjusted_ttm',
        capital_region='global', minimum_sample_count=30, max_reference_age_days=370,
        terminal_growth_cap=.02, terminal_roic='wacc')
    if {k:v for k,v in policy.items() if k != 'boundary'} != expected or not policy.get('boundary'):
        raise ValueError('unsupported assumption selection policy')
    inputs = CompanyValuationInput.model_validate(discount_inputs).model_copy(deep=True)
    if inputs.ticker != baseline['inputs']['ticker']:
        raise ValueError('selection company mismatch')
    original = CompanyValuationInput.model_validate(baseline['inputs']).model_dump(mode='json')
    candidate = inputs.model_dump(mode='json')
    # 候选允许显式情景变化，但不能从另一套财务/调整范围借用增长与利润率。
    for value in (original, candidate):
        for key in ('macro_inputs','valuation_assumptions','company_metrics'):
            value.pop(key)
        for key in ('cost_of_capital_approach','reference_capital_inputs'):
            value['methodology_choices'].pop(key)
    if original != candidate:
        raise ValueError('selection financial or adjustment basis differs from baseline; rebuild with tools.select_native_assumptions --rebuild')
    reviewed = review_report(baseline)
    cutoff = datetime.fromisoformat(reference_cutoff)
    if cutoff.utcoffset() is None:
        raise ValueError('aware reference cutoff required')
    decisions = {}; issues = []
    recent = reviewed['model_input_recent_window']
    growth = recent.get('revenue_growth')
    growth_ok = growth is not None and math.isfinite(growth) and -1 < growth <= 1
    decisions['growth'] = dict(status='selected_conditional' if growth_ok else 'rejected',
        value=growth, basis='latest_comparable_window_then_engine_fade', evidence=recent,
        path=('contraction_then_recovery_to_stable_growth' if growth_ok and growth < 0
              else 'nonnegative_growth_to_stable_growth' if growth_ok else None))
    if not growth_ok:
        issues.append('growth_missing_or_outside_positive_revenue_path_or_extreme_growth_policy')
    margin = reviewed['margin_bridge']['adjusted_margin']
    margin_ok = margin is not None and math.isfinite(margin) and 0 < margin <= 1
    decisions['margin'] = dict(status='selected_conditional' if margin_ok else 'rejected',
        value=margin, basis='hold_positive_adjusted_ttm_not_industry_margin', evidence=reviewed['margin_bridge'])
    if not margin_ok:
        issues.append('margin_missing_or_requires_explicit_turnaround_policy')
    candidates = [r for r in reference_rows if r['subject'] == inputs.industry_data.industry_name
        and r['metric'] == 'sales_to_invested_capital_ltm' and r['region'] == policy['capital_region']]
    ratio = None
    try:
        if len(candidates) != 1:
            raise ValueError('unique matching Global capital reference required')
        row = candidates[0]
        observed = date.fromisoformat(row['observation_date'])
        available = datetime.fromisoformat(row['available_at'])
        if (row['status'], row['unit'], row['method_code']) != ('reported', 'dimensionless', 'provider_reported'):
            raise ValueError('unsupported capital reference semantics')
        if available.utcoffset() is None or available > cutoff or not 0 <= (cutoff.date()-observed).days <= policy['max_reference_age_days']:
            raise ValueError('capital reference stale or unavailable at cutoff')
        if row['sample_count'] is None or row['sample_count'] < policy['minimum_sample_count']:
            raise ValueError('capital reference sample too small')
        ratio = float(row['value'])
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError('positive capital reference required')
    except (ValueError, TypeError) as error:
        issues.append(str(error)); ratio = None
    decisions['capital'] = dict(status='selected_conditional' if ratio is not None else 'rejected',
        value=ratio, basis='global_industry_historical_proxy_not_company_marginal_efficiency', evidence=candidates)
    reference = inputs.methodology_choices.reference_capital_inputs
    if reference is None or inputs.methodology_choices.cost_of_capital_approach != 'reference_snapshot':
        raise ValueError('explicit reference WACC required')
    wacc = inputs.valuation_assumptions.cost_of_capital_stable_override
    terminal = min(policy['terminal_growth_cap'], reference.risk_free_rate)
    terminal_ok = wacc is not None and math.isfinite(wacc) and wacc > terminal >= 0
    decisions['terminal'] = dict(status='selected_conditional' if terminal_ok else 'rejected',
        growth=terminal, roic=wacc, basis='explicit_growth_cap_and_no_terminal_excess_returns')
    if not terminal_ok:
        issues.append('invalid_terminal_boundary')
    assumptions = inputs.valuation_assumptions
    effective_tax = assumptions.effective_tax_rate_override_years_1_5
    if effective_tax is None:
        effective_tax = inputs.macro_inputs.tax_rate_effective
    if (assumptions.override_tax_convergence and effective_tax is not None
            and effective_tax != inputs.macro_inputs.tax_rate_marginal):
        issues.append('perpetual_effective_tax_requires_explicit_marginal_tax_basis')
    audit = dict(policy=policy, decisions=decisions, issues=issues, automatic_selection=True,
        automatic_adoption=False, predictive_validation='not_established',
        economic_basis=dict(status='not_established',
            review='docs/valuation/native-assumption-selection.md#达摩达兰方法核验',
            unresolved=[
                'company_growth_runway_and_fade_duration',
                'sustainable_adjusted_operating_margin',
                'industry_capital_proxy_accounting_and_marginal_return',
                'reinvestment_lag_and_tax_transition_timing',
                'operating_country_risk_exposure',
                'company_credit_capital_structure_and_usable_tax_shield',
                'terminal_risk_and_no_excess_return_scenario',
                'inherited_adjustment_and_equity_bridge_policies']),
        status='rejected' if issues else 'selected_conditional',
        wacc_boundary='inherits_explicit_reference_scenario_not_company_market_WACC')
    if growth_ok and growth < 0:
        audit['economic_basis']['unresolved'].extend([
            'contraction_recovery_timing_and_survival',
            'capital_release_recoverability_not_implied_by_revenue_decline'])
    if issues:
        return None, audit
    inputs.macro_inputs.risk_free_rate = reference.risk_free_rate
    inputs.macro_inputs.equity_risk_premium = reference.mature_market_erp
    a = inputs.valuation_assumptions
    a.annual_forecast = None
    a.annual_sales_to_capital = None
    a.projection_years = policy['projection_years']; a.high_growth_years = policy['high_growth_years']
    a.revenue_growth_next_year = a.revenue_growth_years_2_5 = growth
    a.operating_margin_next_year = a.target_operating_margin = margin
    a.sales_to_capital_high = a.sales_to_capital_stable = ratio
    a.override_growth_perpetuity = False; a.growth_perpetuity_rate = None
    a.override_riskfree = False; a.riskfree_after_yr10 = None
    a.stable_growth_rate = terminal; a.roic_stable_override = wacc
    payload = dict(inputs=CompanyValuationInput.model_validate(inputs.model_dump()).model_dump(mode='json'))
    audit['selected_assumptions'] = payload['inputs']['valuation_assumptions']
    return payload, audit


# 固定方法参数，不根据公司代码、估值输出或价格调参；是条件压力，不是教材指定值。
SCENARIO_RULES = dict(version='native-evidence-scenarios-v1', fade_years=[3, 5, 10],
    margin_targets=['hold_adjusted_ttm', 'three_year_revenue_weighted_adjusted'],
    margin_convergence_year=5, history_years=3, selected_scenario=None,
    predictive_validation='not_established')


def scenario_payloads(baseline, selected):
    """复用已选参考和同口径财务；统一生成场景，不将历史均值认证为可持续目标。"""
    from engine.data_dictionary import ForecastYear
    from engine.module_4_dcf import _tax_path
    from datetime import date
    reviewed = review_report(baseline)
    inputs = CompanyValuationInput.model_validate(selected['inputs'])
    a = inputs.valuation_assumptions
    if a.projection_years != 10 or a.high_growth_years != 1 or a.annual_forecast is not None:
        raise ValueError('scenario generation requires selected ten-year conditional basis')
    current = reviewed['margin_bridge']['adjusted_margin']
    targets = {'hold': current}
    missing = []
    year = date.fromisoformat(inputs.period_date_10k[:10]).year
    required = list(range(year-2, year+1))
    evidence = [r for r in reviewed['sustainability_evidence']['annual_rows'] if r['year'] in required]
    history = {r.fiscal_year:r for r in inputs.raw_financials}
    from data_sources.us_cn_hk_db import standard_cumulative_ttm
    if inputs.adjustment_inputs.has_operating_leases or (inputs.prepared_ttm is not None and not standard_cumulative_ttm(inputs)):
        missing.append('historical_margin_requires_matching_lease_or_prepared_ttm_adjustments')
    elif (sorted(r['year'] for r in evidence) != required or any(
            r['research_adjusted_margin'] is None or r['unavailable_research_cohort_years']
            or history[r['year']].revenues is None or history[r['year']].revenues <= 0 for r in evidence)):
        missing.append('three_consecutive_adjusted_margin_years_unavailable')
    else:
        target = sum(r['research_adjusted_margin']*history[r['year']].revenues for r in evidence) / sum(history[y].revenues for y in required)
        if not math.isfinite(target) or not 0 < target <= 1:
            missing.append('historical_margin_requires_explicit_loss_or_extreme_margin_path')
        else:
            targets['history'] = target
    effective = a.effective_tax_rate_override_years_1_5
    if effective is None:
        effective = inputs.macro_inputs.tax_rate_effective
    if effective is None:
        effective = inputs.macro_inputs.tax_rate_marginal
    taxes, terminal_tax = _tax_path(effective, inputs.macro_inputs.tax_rate_marginal,
        a.override_tax_convergence, a.high_growth_years, a.projection_years)
    if not math.isclose(taxes[-1], terminal_tax, rel_tol=1e-12, abs_tol=1e-14):
        raise ValueError('annual scenario cannot change terminal tax')
    taxes[-1] = terminal_tax  # 消除逐步相加舍入，使终值严格采用同一边际税率。
    payloads = {}
    for horizon in SCENARIO_RULES['fade_years']:
        growth = [a.revenue_growth_next_year + (a.stable_growth_rate-a.revenue_growth_next_year)
                  * min(i/(horizon-1), 1) for i in range(10)]
        for name, target in targets.items():
            changed = inputs.model_copy(deep=True)
            changed.valuation_assumptions.annual_forecast = [ForecastYear(growth=g,
                margin=current+(target-current)*min(i/4, 1), tax=taxes[i]) for i,g in enumerate(growth)]
            payloads[f'scenario_fade{horizon}_{name}'] = dict(inputs=changed.model_dump(mode='json'))
    return payloads, dict(rules=SCENARIO_RULES, margin_targets=targets, required_history_years=required,
        historical_margin_evidence=evidence, missing=missing, selected_scenario=None,
        boundary='历史收入加权利润率仅为均值回归情景；3/5/10年为固定压力持续期，不是公司预测或达摩达兰指定值；税、WACC、资本代理及股权桥接保持所选口径',
        input_classes=dict(company_observations=['comparable_revenue_growth','adjusted_ttm_margin','historical_adjusted_margins'],
            reference_proxies=['industry_sales_to_capital','reference_wacc'],
            analyst_rules=['growth_fade_duration','margin_target_and_transition','terminal_growth_and_return']),
        automatic_adoption=False)


def prediction_check(baseline):
    """最近两个完整年度的固定一步预测诊断；不以评分选择规则或筛公司。"""
    from datetime import date
    reviewed = review_report(baseline)
    inputs = CompanyValuationInput.model_validate(baseline['inputs'])
    end = date.fromisoformat(inputs.period_date_10k[:10]).year
    raw = {r.fiscal_year:r for r in inputs.raw_financials}
    margins = {r['year']:r['research_adjusted_margin'] for r in reviewed['sustainability_evidence']['annual_rows']}
    from data_sources.us_cn_hk_db import standard_cumulative_ttm
    margin_comparable = not inputs.adjustment_inputs.has_operating_leases and (inputs.prepared_ttm is None or standard_cumulative_ttm(inputs))
    duplicate = reviewed['sustainability_evidence']['duplicate_annual_years']
    def revenue(y):
        value = raw[y].revenues if y in raw and y not in duplicate else None
        return value if value is not None and math.isfinite(value) and value > 0 else None
    results = []
    for target in (end-1, end):
        origin = target-1
        rev = revenue(origin)
        observed = revenue(target)
        growth = {}
        if rev is not None and revenue(origin-1) is not None:
            growth['latest'] = rev/revenue(origin-1)-1
        if all(revenue(y) is not None for y in range(origin-3, origin+1)):
            growth['cagr3'] = (rev/revenue(origin-3))**(1/3)-1
        targets = {}
        if margin_comparable and origin not in duplicate and margins.get(origin) is not None:
            targets['hold'] = margins[origin]
        years = range(origin-2, origin+1)
        if margin_comparable and all(revenue(y) is not None and margins.get(y) is not None for y in years):
            targets['history'] = sum(margins[y]*revenue(y) for y in years)/sum(revenue(y) for y in years)
        actual_margin = margins.get(target) if margin_comparable and target not in duplicate else None
        forecasts = []
        for g in ('latest','cagr3'):
            for m in ('hold','history'):
                predicted_revenue = rev*(1+growth[g]) if g in growth else None
                predicted_ebit = predicted_revenue*targets[m] if predicted_revenue is not None and m in targets else None
                actual_ebit = observed*actual_margin if observed is not None and actual_margin is not None else None
                forecasts.append(dict(rule=g+'_'+m, predicted_revenue=predicted_revenue,
                    predicted_adjusted_ebit=predicted_ebit,
                    revenue_absolute_error_scaled=(abs(predicted_revenue-observed)/observed
                        if predicted_revenue is not None and observed is not None else None),
                    ebit_absolute_error_scaled_by_revenue=(abs(predicted_ebit-actual_ebit)/observed
                        if predicted_ebit is not None and actual_ebit is not None else None),
                    missing=([*(['growth_history'] if g not in growth else []),
                        *(['comparable_margin_history'] if m not in targets else []),
                        *(['actual_revenue'] if observed is None else []),
                        *(['actual_adjusted_margin'] if actual_margin is None else [])])))
        results.append(dict(origin_year=origin, target_year=target, growth_inputs=growth, margin_inputs=targets,
            actual_revenue=observed, actual_adjusted_margin=actual_margin, forecasts=forecasts))
    return dict(version='native-one-year-diagnostic-v1', ticker=inputs.ticker, results=results,
        selected_rule=None, predictive_validation='not_established',
        boundary='当前版本历史输入的简化回溯，后下载修订可能前视；非独立留出，不校准持续期，不认证完整DCF；同公司多期不是独立样本，缺项保留')


def prediction_summary(rows):
    """所有规则用同一可评分交集，同时公开完整公司/期间分母。"""
    from statistics import mean
    metrics = {}
    for metric in ('revenue_absolute_error_scaled','ebit_absolute_error_scaled_by_revenue'):
        paired = []
        for company in rows:
            for point in company.get('prediction_check', {}).get('results', []):
                if all(f[metric] is not None for f in point['forecasts']):
                    paired.append((company['ticker'], point['forecasts']))
        rules = {}
        for rule in ('latest_hold','latest_history','cagr3_hold','cagr3_history'):
            companies = {}
            for ticker, forecasts in paired:
                companies.setdefault(ticker, []).append(next(f[metric] for f in forecasts if f['rule'] == rule))
            rules[rule] = dict(mean_company_error=mean(mean(v) for v in companies.values()) if companies else None,
                company_errors={k:mean(v) for k,v in companies.items()})
        metrics[metric] = dict(total_company_periods=2*len(rows), paired_periods=len(paired),
            missing_periods=2*len(rows)-len(paired), companies=len(set(t for t,_ in paired)), rules=rules)
    return dict(metrics=metrics, selected_rule=None, boundary='同一指标同一交集，先公司内平均再公司间平均；仅描述，非独立样本显著性或DCF准确率')


def rebuild(request):
    """显式新输入重建；旧报告留痕，不借用其利润率或把新结果当成已获批事实。"""
    from pathlib import Path
    import hashlib
    from engine.orchestrator import run_full_valuation
    from tools.compare_valuations import changes
    from tools.check_native_sqlite import value_digest
    from api.alphalake import ENGINE_REVISION

    if not isinstance(request.get('reason'), str) or not request['reason'].strip() or not request.get('evidence_files'):
        raise ValueError('rebuild reason and evidence files required')
    evidence = []
    for name in request['evidence_files']:
        path = Path(name).resolve(strict=True)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        evidence.append(dict(path=str(path), sha256=digest))
    old = request['baseline']
    inputs = CompanyValuationInput.model_validate(request['inputs'])
    if inputs.ticker != old['inputs']['ticker']:
        raise ValueError('rebuild company mismatch')

    def calculate(values):
        model = CompanyValuationInput.model_validate(values)
        report = run_full_valuation(model)
        return dict(inputs=model.model_dump(mode='json'), **{key:getattr(report,key).model_dump(mode='json')
            for key in ('ltm_financials','adjusted','cost_of_capital','cashflow','dcf','final')})

    baseline = calculate(inputs.model_dump(mode='json'))
    old_current, replay_error = None, None
    try:
        old_current = calculate(old['inputs'])
    except (ValueError, TypeError) as error:
        replay_error = str(error)
    reproduced = old_current is not None and all(old.get(key) == old_current[key] for key in old_current if key != 'inputs')
    payload, audit = select(baseline, baseline['inputs'], request['reference_rows'],
                            request['reference_cutoff'], request['policy'])
    candidate = calculate(payload['inputs']) if payload is not None else None
    result = dict(contract='native-assumption-rebuild-v1', engine_revision=ENGINE_REVISION,
        implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        status='calculated_conditional' if candidate else 'selection_rejected',
        reason=request['reason'], evidence_files=evidence,
        economic_approval='not_inferred_from_rebuild_or_evidence_hashes',
        prior_report=old, prior_report_sha256=value_digest(old),
        prior_replay_status=('unavailable_under_current_engine' if old_current is None else
                            'reproduced' if reproduced else 'differs_under_current_engine_not_verified'),
        prior_replay_error=replay_error,
        prior_inputs_current_engine=old_current, rebuilt_baseline=baseline,
        selection=audit, candidate=candidate,
        input_changes=list(changes(old_current['inputs'] if old_current else old['inputs'],baseline['inputs'])),
        baseline_result_changes=list(changes(old_current['final'],baseline['final'])) if old_current else None,
        selection_result_changes=list(changes(baseline['final'],candidate['final'])) if candidate else [])
    result['run_id'] = value_digest(result)
    return result


if __name__ == '__main__':
    import argparse
    import json
    from pathlib import Path
    from data_sources.paths import workspace_path
    parser = argparse.ArgumentParser(description='依据显式输入变更重建基线并重算条件估值')
    parser.add_argument('--rebuild', required=True, type=Path, help='含旧报告、新输入、依据文件及选择政策的JSON')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(workspace_path('derived').resolve()) or output.exists():
        raise ValueError('new output under workspace/derived required')
    result = rebuild(json.loads(args.rebuild.read_bytes()))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(dict(run_id=result['run_id'],status=result['status'],output=str(output)),ensure_ascii=False))
