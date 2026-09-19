"""完整目录驱动研究取数，源观察与标准事实准入保持分层。"""
from decimal import Decimal
import struct
import pytest
from tools import tdx_research_source as source


def test_new_statement_fields_need_no_parser_branches():
    for name, provider, multiplier, basis in (
        ('total_shares', 'FN238', 1, 'instant'),
        ('largest_shareholder_shares', 'FN243', 1, 'instant'),
        ('development_costs', 'FN34', 1, 'instant'),
        ('contract_assets', 'FN435', 10000, 'instant'),
        ('right_of_use_assets', 'FN438', 10000, 'instant'),
        ('financial_fee_cash_paid', 'FN583', 10000, 'ytd'),
        ('bond_issuance_cash_paid', 'FN584', 10000, 'ytd'),
    ):
        row = {'bits': {provider: struct.unpack('<I', struct.pack('<f', 1.25))[0]}}
        assert source.source_field(name) == provider
        assert source.period_basis(name) == basis
        assert source.financial_value(row, name) == Decimal('1.25') * multiplier
    # 保留所有来源变体，不为同名但未区分的两项贷款任选一个。
    for name in ('financial_loans_and_advances', 'reported_ttm_revenue',
                 'forecast_revenue_lower', 'financial_report_announcement_date',
                 'reported_current_ratio', 'FN435'):
        with pytest.raises(KeyError):
            source.source_field(name)
