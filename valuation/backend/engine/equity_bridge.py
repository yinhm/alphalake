"""政策桥接由共享编排器应用，最终值继续进入 M6，避免 API 外二次拼接。"""
import math
from .data_dictionary import DCFResult, EquityBridgeInputs


def apply_equity_bridge(dcf: DCFResult, policy: EquityBridgeInputs) -> dict:
    ev = dcf.value_of_operating_assets
    if ev is None or not math.isfinite(ev):
        raise ValueError('equity bridge requires finite operating value')
    equity = ev * policy.operating_ownership + sum(policy.components.values())
    plain = equity / policy.shares
    converted = (equity + policy.conversion_release) / (policy.shares + policy.conversion_shares)
    use_conversion = converted < plain
    dcf.value_of_equity = equity + (policy.conversion_release if use_conversion else 0)
    dcf.value_per_share_pre_options = min(plain, converted)
    return dict(policy_id=policy.policy_id, status='illustrative_policy_not_reported_fact',
                operating_value=ev, operating_ownership=policy.operating_ownership,
                components=policy.components, equity_no_conversion=equity,
                equity_if_converted=equity + policy.conversion_release,
                shares_no_conversion=policy.shares, shares_if_converted=policy.shares + policy.conversion_shares,
                per_share_no_conversion=plain, per_share_if_converted=converted,
                selected_case='converted' if use_conversion else 'not_converted',
                per_share=dcf.value_per_share_pre_options)
