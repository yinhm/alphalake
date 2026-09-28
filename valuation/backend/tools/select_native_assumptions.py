"""依证据选择原生条件假设；公司事实、行业代理与未验证预测分别留痕。"""
from datetime import date, datetime
import math

from engine.data_dictionary import CompanyValuationInput
from tools.review_native_policy import review_report


def select(baseline, discount_inputs, reference_rows, reference_cutoff, policy):
    expected = dict(version='native-assumption-selection-v1', growth='latest_comparable_revenue_growth',
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
    growth_ok = growth is not None and math.isfinite(growth) and 0 <= growth <= 1
    decisions['growth'] = dict(status='selected_conditional' if growth_ok else 'rejected',
        value=growth, basis='latest_comparable_window_then_engine_fade', evidence=recent)
    if not growth_ok:
        issues.append('growth_missing_or_requires_explicit_recovery_or_extreme_growth_policy')
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
            review='docs/native-assumption-selection.md#达摩达兰方法核验',
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
    if issues:
        return None, audit
    inputs.macro_inputs.risk_free_rate = reference.risk_free_rate
    inputs.macro_inputs.equity_risk_premium = reference.mature_market_erp
    a = inputs.valuation_assumptions
    a.annual_forecast = None
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
