"""AlphaLake 导出包的正式估值入口，原生返回桥接结果并保留可重放输入。"""
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import sys
import json
import os
from pathlib import Path
import tempfile

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from data_sources.alphalake import AlphaLakeRequest, MissingInputs, build_inputs, content_hash
from engine.orchestrator import run_full_valuation

router = APIRouter(prefix='/api/valuation')


def runtime_versions():
    return {'python':sys.version.split()[0],**{name:version(name) for name in ('pydantic','numpy','scipy','fastapi')}}


def engine_revision():
    root = Path(__file__).resolve().parents[1]
    paths = sorted((root/'engine').glob('*.py'))+[root/'data_sources/alphalake.py',root/'data_sources/alphalake_wacc.py',root/'data_sources/alphalake_capital.py',root/'data_sources/alphalake_calibration.py',root/'data_sources/alphalake_market.py',Path(__file__).resolve()]
    digest = hashlib.sha256(json.dumps(runtime_versions(),sort_keys=True).encode())
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
    return digest.hexdigest()


# 服务启动时固定实际加载版本；部署更新后应重启进程。
ENGINE_REVISION = engine_revision()


def terminal_return_sensitivity(inputs, report):
    """通用账面DCF的终值单因素对照；复用引擎，不替换主结果。"""
    assumptions = inputs.valuation_assumptions
    roic = assumptions.roic_stable_override
    wacc = assumptions.cost_of_capital_stable_override
    scenario = inputs.model_copy(deep=True)
    scenario.valuation_assumptions.roic_stable_override = wacc
    counter = report if roic == wacc else run_full_valuation(scenario)
    value = counter.final.value_per_share
    return dict(
        status='illustrative_terminal_only_sensitivity',
        method='terminal_roic_equals_terminal_wacc',
        terminal_growth=assumptions.growth_perpetuity_rate,
        terminal_roic=roic, terminal_wacc=wacc,
        terminal_pv_share=(report.dcf.pv_terminal_value / report.dcf.value_of_operating_assets
                           if report.dcf.value_of_operating_assets else None),
        baseline_value_per_share=report.final.value_per_share,
        counterfactual_value_per_share=value,
        delta_per_share=value-report.final.value_per_share,
        counterfactual_equity_status='positive_equity_residual' if value > 0 else 'nonpositive_equity_residual_requires_distress_model',
        currency='CNY', unit='CNY/share',
        source='https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/termvalueexreturns.htm',
        boundary='仅终值ROIC改为终值WACC；显式期现金流、折现与股权桥接不变。无持续超额回报是假设对照，不是公司回报估计、预测验证或推荐估值。')


def growth_path_sensitivity(inputs, report):
    """历史增长政策的显式对照；保留利润率、资本效率及全部桥接输入。"""
    scenario = inputs.model_copy(deep=True)
    assumptions = scenario.valuation_assumptions
    original = inputs.valuation_assumptions.annual_forecast
    terminal_growth = assumptions.growth_perpetuity_rate
    growth = [0.0 if year <= 5 else terminal_growth*(year-5)/5 for year in range(1, 11)]
    assumptions.annual_forecast = [row.model_copy(update={'growth': rate}) for row, rate in zip(original, growth)]
    counter = run_full_valuation(scenario)
    value = counter.final.value_per_share
    return dict(
        status='illustrative_growth_path_sensitivity',
        method='zero_first_five_then_fade_to_unchanged_terminal_growth',
        baseline_growth=[row.growth for row in original], counterfactual_growth=growth,
        baseline_value_per_share=report.final.value_per_share,
        counterfactual_value_per_share=value, delta_per_share=value-report.final.value_per_share,
        counterfactual_equity_status='positive_equity_residual' if value > 0 else 'nonpositive_equity_residual_requires_distress_model',
        currency='CNY', unit='CNY/share',
        baseline_fcff_million_cny=report.dcf.fcff_projections,
        counterfactual_fcff_million_cny=counter.dcf.fcff_projections,
        evidence=dict(
            medium_horizon='two_disjoint_120_company_cohorts_show_average_improvement_in_common_two_and_three_year_windows',
            counterevidence='one_year_validation_failed_and_some_medium_horizon_windows_worsened',
            adoption='not_adopted_as_default_or_company_specific_forecast',
            review='docs/zero-growth-common-windows-20260911.md',
            receipt=dict(path='docs/acceptance/zero-growth-common-windows-20260911.json',
                         sha256='1aa85158546b71ab986a0be0a31c32f02448be1c3dc455d9105523204faa75ca')),
        boundary='同一财务与信息时点，仅改十年收入增长路径；利润率、税率、资本效率、WACC、终值增长/ROIC及股权股本桥接输入不变。收入变化同时影响利润、再投资和终值金额；零收入增量再投资不等于零现金资本开支。中期样本改善不证明一年、第四年至终值、当前公司或完整DCF有效；不是价格区间、概率界限或推荐值。')


def growth_capital_consistency(inputs, report, audit):
    """收入增量法的经济输入诊断；不把代数分解称为公司实际ROIC。"""
    a = inputs.valuation_assumptions
    raw = inputs.prepared_ttm.financials
    previous_revenue = raw.revenues
    previous_margin = raw.ebit/raw.revenues*(1-inputs.macro_inputs.tax_rate_effective)
    previous_nopat = previous_revenue*previous_margin
    years = []
    for i, (forecast, revenue, ebit, reinvestment, fcff) in enumerate(zip(
            a.annual_forecast, report.dcf.revenue_projections, report.dcf.ebit_projections,
            report.dcf.reinvestment_projections, report.dcf.fcff_projections, strict=True), 1):
        nopat = fcff+reinvestment
        margin = nopat/revenue
        revenue_change = revenue-previous_revenue
        # 精确代数分解，交叉项归入收入贡献；不声称独立经济因果识别。
        growth_part = revenue_change*margin
        profitability_part = previous_revenue*(margin-previous_margin)
        marginal_return = growth_part/reinvestment if reinvestment > 0 and revenue_change > 0 else None
        flags = []
        if reinvestment < 0:
            flags.append('capital_release_requires_recoverability_evidence')
        if fcff < 0:
            flags.append('negative_fcff_requires_funding_plan_not_automatic_rejection')
        if marginal_return is not None and marginal_return < report.cost_of_capital.wacc:
            flags.append('revenue_growth_return_proxy_below_wacc')
        years.append(dict(year=i, revenue_growth=forecast.growth, operating_margin=forecast.margin,
            tax_policy=forecast.tax, revenue_million_cny=revenue, ebit_million_cny=ebit,
            nopat_million_cny=nopat, net_reinvestment_million_cny=reinvestment, fcff_million_cny=fcff,
            nopat_change_million_cny=nopat-previous_nopat,
            revenue_contribution_million_cny=growth_part,
            margin_tax_contribution_million_cny=profitability_part,
            reinvestment_rate=reinvestment/nopat if nopat > 0 else None,
            revenue_linked_incremental_return_proxy=marginal_return,
            flags=flags))
        previous_revenue, previous_margin, previous_nopat = revenue, margin, nopat
    return dict(status='requires_company_economic_review',
        method='revenue_change_over_marginal_sales_to_capital_zero_lag',
        capital_efficiency=a.sales_to_capital_high,
        capital_basis='historical_industry_average_as_marginal_proxy' if 'capital_reference' in audit else 'explicit_marginal_policy',
        company_capital_efficiency_verified=False,
        company_evidence=audit.get('company_capital_evidence'),
        accounting_scope='rd_expensed_reported_leases_industry_capital_scope_not_reconciled',
        baseline_nopat_policy_proxy=raw.ebit*(1-inputs.macro_inputs.tax_rate_effective),
        years=years,
        review_actions=['justify_joint_growth_margin_and_marginal_capital_policy',
                        'reconcile_rd_lease_and_operating_capital_scope_before_company_ratio_adoption'],
        method_source='https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/growth.htm',
        boundary='利润变化是模型代数分解；增量回报代理仅在正收入增量和正投入时定义，不是实际或完整隐含ROIC。负再投资保留但须核验可回收资本；负FCFF不是错误。研发费用不直接换算增长，行业平均不自动成为公司边际效率。')


def method_assessment(inputs, audit, report):
    """当前通用账面FCFF的实际接入范围；不以计算成功声称公司估值完备。"""
    if audit.get('valuation_scope') != 'report_date_book_equity_scenario':
        return None
    a = inputs.valuation_assumptions
    last_nopat = report.dcf.fcff_projections[-1] + report.dcf.reinvestment_projections[-1]
    return dict(
        status='conditional_fcff_not_full_company_valuation',
        scope='nonfinancial_book_fcff',
        facts=dict(source='alphalake_standard_facts', lineage='audit.consumed_inputs',
                   monetary_unit='million_CNY', share_unit='million_shares'),
        operating_profit=dict(basis='FN86+FN305-FN306-FN83-FN82-FN301',
                              rd='expensed_not_capitalized',
                              leases='reported_interest_scope_not_fully_reconciled'),
        forecast=dict(basis='historical_quarter_yoy_policy' if 'forecast_rule_evidence' in audit else 'explicit_annual_policy',
                      company_evidence_status='not_established_by_calculation',
                      policy_location='audit.assumptions'),
        growth_capital_consistency=growth_capital_consistency(inputs, report, audit),
        reinvestment=dict(basis='net_reinvestment_equals_revenue_change_over_sales_to_capital',
                          capital_efficiency_source='industry_reference_with_policy' if 'capital_reference' in audit else 'explicit_policy',
                          capital_efficiency=a.sales_to_capital_high,
                          historical_fcff_status=audit['historical_fcff_status'],
                          implied_roic_status='missing_opening_capital' if all(v is None for v in report.dcf.implied_roic_projections) else 'model_implied_not_reported_fact',
                          capital_release_years=[i+1 for i,v in enumerate(report.dcf.reinvestment_projections) if v < 0]),
        wacc=dict(basis=report.cost_of_capital.capital_structure_basis,
                  source='reference_snapshot_with_policy' if 'wacc_reference' in audit else 'explicit_policy',
                  reference_location='audit.wacc_reference' if 'wacc_reference' in audit else None,
                  forecast_wacc=report.cost_of_capital.wacc, terminal_wacc=a.cost_of_capital_stable_override,
                  terminal_risk_basis='constant_wacc_policy'),
        terminal=dict(growth=a.growth_perpetuity_rate, roic=a.roic_stable_override,
                      reinvestment_rate=a.growth_perpetuity_rate/a.roic_stable_override,
                      last_explicit_reinvestment_rate=report.dcf.reinvestment_projections[-1]/last_nopat if last_nopat else None,
                      pv_share=report.dcf.pv_terminal_value/report.dcf.value_of_operating_assets if report.dcf.value_of_operating_assets else None,
                      company_evidence_status='growth_return_and_risk_require_joint_review'),
        equity_bridge=dict(basis='report_date_book_claims_no_conversion',
                           detail_location='report.equity_bridge',
                           financial_investments='reviewed_selected_standard_assets' if audit.get('reviewed_assets') else 'no_credit_pending_classification',
                           reviewed_assets=audit.get('reviewed_assets', []),
                           options='not_priced_not_asserted_absent'),
        unresolved=[
            dict(item='growth_margin_and_capital_efficiency', treatment='explicit_policy', reason='增长、利润率与资本效率的公司依据尚未闭合'),
            dict(item='rd_and_leases', treatment='reported_basis', reason='研发保留费用化；租赁利息范围待核验，不重复资本化已入账租赁'),
            dict(item='historical_operating_capital', treatment='missing_not_zero', reason='历史经营资本及FCFF分类未闭合，不用预测投入冒充历史资本'),
            dict(item='terminal_growth_return_and_risk', treatment='explicit_policy', reason='稳定增长、回报及固定WACC须联合论证；公式成立不代表参数合理'),
            dict(item='equity_claims_and_assets', treatment='book_scenario', reason='未审核金融投资不计值、已审核投资按显式账面代理；到期债务未拆分、少数股权账面代理及未定价期权均限制结论'),
        ],
        boundary='条件模型可以复算；以上缺口没有被归零或认定无影响。未提供当前目标价、预测准确性认证或完整公司估值认证。')


def evaluate(request: AlphaLakeRequest):
    inputs, audit = build_inputs(request)
    revision = ENGINE_REVISION
    request_data = request.model_dump(mode='json')
    run_id = content_hash(dict(request=request_data,engine_revision=revision))
    report = run_full_valuation(inputs)
    if audit.get('valuation_scope')=='report_date_book_equity_scenario' and (report.final.value_per_share is None or report.final.value_per_share<=0):
        raise ValueError('nonpositive equity residual requires distress/option model')
    result = dict(run_id=run_id,engine_revision=revision,runtime_versions=runtime_versions(),status=('illustrative_enterprise_value_only' if audit.get('valuation_scope')=='operating_enterprise_value_only_no_equity_bridge' else 'illustrative_book_equity_scenario' if audit.get('valuation_scope')=='report_date_book_equity_scenario' else 'illustrative_valuation_completed'),
        request=request_data,inputs=inputs.model_dump(mode='json'),audit=audit,
        report=jsonable_encoder(asdict(report)),
        method_assessment=method_assessment(inputs,audit,report),
        terminal_sensitivity=(terminal_return_sensitivity(inputs,report)
                              if audit.get('valuation_scope')=='report_date_book_equity_scenario' else None),
        growth_sensitivity=(growth_path_sensitivity(inputs,report)
                            if request.policy.policy_id in ('nonfinancial-history-fcff-v1', 'nonfinancial-history-fcff-calibrated-v1', 'nonfinancial-reviewed-history-fcff-v1') else None))
    # 保存输入/政策/引擎版本与输出。同内容重放不覆盖；失败不产生成功记录。
    root = Path(os.environ.get('ALPHALAKE_VALUATION_RUN_DIR',str(Path(__file__).resolve().parents[1]/'data/alphalake_runs')))
    root.mkdir(parents=True,exist_ok=True)
    target = root/(run_id+'.json')
    payload = json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n'
    if target.exists():
        if target.read_text() != payload:
            raise ValueError('stored valuation differs for identical inputs and engine version')
    else:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=root,delete=False) as f:
            temporary = Path(f.name)
            try:
                f.write(payload);f.flush();os.fsync(f.fileno())
                # 原子创建：并发同内容运行不会部分覆盖文件。
                try:
                    os.link(temporary,target)
                except FileExistsError:
                    if target.read_text() != payload:
                        raise ValueError('concurrent valuation differs')
            finally:
                temporary.unlink(missing_ok=True)
    return result


@router.post('/from-alphalake')
def from_alphalake(request: AlphaLakeRequest):
    try:
        return evaluate(request)
    except MissingInputs as error:
        raise HTTPException(status_code=422,detail=dict(status='blocked_missing_inputs',missing=error.items)) from error
    except (ValueError,KeyError,TypeError,ArithmeticError) as error:
        raise HTTPException(status_code=422,detail=dict(status='rejected_input_or_policy',reason=str(error))) from error
