"""政策桥接由共享编排器应用，最终值继续进入 M6，避免 API 外二次拼接。"""
import math
from .data_dictionary import ConvertibleDebt, DCFResult, EquityBridgeInputs


class InvalidConvertibleInput(ValueError):
    pass


def value_convertible(policy: ConvertibleDebt, comparable_yield: float | None = None) -> dict:
    """达摩达兰含权市值拆分；市场残差已经包含权利时间价值及条款净影响。"""
    rate = policy.discount_rate if policy.discount_rate is not None else comparable_yield
    if rate is None or not math.isfinite(rate) or rate <= -1:
        raise InvalidConvertibleInput('convertible requires an explicit comparable straight-debt yield')
    if policy.book_value is None:
        raise InvalidConvertibleInput('convertible requires the known carrying amount included in book debt')
    if policy.market_value <= 0 or policy.face_value <= 0:
        raise InvalidConvertibleInput('convertible requires positive face and total dirty market value')
    flows = [(r.time_years, r.amount) for r in policy.cashflows]
    if flows:
        if sum(amount for _,amount in flows) < policy.face_value:
            raise InvalidConvertibleInput('contractual cashflows do not cover remaining face value')
        schedule_basis = 'explicit_contractual_cashflows'
    else:
        periods = policy.maturity_years * policy.coupon_frequency
        if policy.annual_coupon is None or periods <= 0 or not math.isclose(periods, round(periods), abs_tol=1e-9):
            raise InvalidConvertibleInput('supply exact cashflows or an explicit regular coupon with whole remaining periods')
        # 等额付息且恰在付息日的条件；阶梯票息/非完整期必须提供精确现金流。
        flows = [(i/policy.coupon_frequency, policy.annual_coupon/policy.coupon_frequency)
                 for i in range(1, round(periods)+1)]
        flows[-1] = (flows[-1][0], flows[-1][1]+policy.face_value)
        schedule_basis = 'regular_coupon_on_payment_date_condition'
    try:
        debt = math.fsum(amount/(1+rate)**time for time, amount in flows)
    except (OverflowError, ZeroDivisionError) as error:
        raise InvalidConvertibleInput('convertible cashflow discounting is not finite') from error
    if not math.isfinite(debt) or debt <= 0 or debt > policy.market_value:
        raise InvalidConvertibleInput('straight-debt PV must be positive and cannot exceed convertible market value; review quote and terms')
    return dict(method='straight_debt_plus_market_residual', status='explicit_market_claim_condition',
        book_value=policy.book_value, straight_debt_value=debt,
        conversion_rights_value=policy.market_value-debt, total_market_value=policy.market_value,
        discount_rate=rate, yield_basis='explicit' if policy.discount_rate is not None else 'detailed_WACC_debt_cost_condition',
        cashflow_basis=schedule_basis, cashflows=[dict(time_years=t, amount=a) for t,a in flows],
        potential_conversion_shares=policy.conversion_shares,
        share_treatment='deduct_rights_once_divide_by_existing_shares',
        limitation='Market residual includes net effects of conversion, call, put and reset terms; not a standalone conversion-option price')


def apply_equity_bridge(dcf: DCFResult, policy: EquityBridgeInputs) -> dict:
    ev = dcf.value_of_operating_assets
    if ev is None or not math.isfinite(ev):
        raise ValueError('equity bridge requires finite operating value')
    equity = ev * policy.operating_ownership + sum(policy.components.values())
    plain = equity / policy.shares
    dcf.value_of_equity = equity
    dcf.equity_bridge_components = dict(policy.components)
    dcf.equity_bridge_components['operating_ownership_adjustment'] = ev * (policy.operating_ownership - 1)
    dcf.value_per_share_pre_options = plain
    return dict(policy_id=policy.policy_id, status='illustrative_policy_not_reported_fact',
                operating_value=ev, operating_ownership=policy.operating_ownership,
                components=policy.components, equity_before_convertible_revaluation=equity,
                shares=policy.shares,
                per_share=dcf.value_per_share_pre_options)
