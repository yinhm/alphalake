"""TDX简化研究源适配；不发布标准事实，不扩大生产字段有效期。"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import math
import struct

# 语义对应已审核的生产映射；历史研究外推边界保持原协议。
FIELDS = {
    'revenue': ('FN230', 'quarter'),
    'operating_cash_flow': ('FN234', 'quarter'),
    'operating_profit_cumulative': ('FN86', 'ytd'),
    'interest_expense': ('FN305', 'ytd'),
    'interest_income': ('FN306', 'ytd'),
    'investment_income': ('FN83', 'ytd'),
    'fair_value_change_income': ('FN82', 'ytd'),
    'asset_disposal_income': ('FN301', 'ytd'),
    'financial_business_interest_income': ('FN506', 'ytd'),
    'financial_business_interest_expense': ('FN509', 'ytd'),
    'financial_business_fee_expense': ('FN510', 'ytd'),
    'deposits_and_interbank_placements': ('FN413', 'instant'),
    'capital_expenditure_cash': ('FN114', 'ytd'),
    'construction_in_progress': ('FN28', 'instant'),
}
VALUE_MULTIPLIERS = {
    'financial_business_interest_income': 10000,
    'financial_business_interest_expense': 10000,
    'financial_business_fee_expense': 10000,
    'deposits_and_interbank_placements': 10000,
}
TIME_BOUNDARY = '简化TDX历史回溯，FN314日期精度，无CNINFO逐公司核验；可能包含后续修订，不是严格PIT或前瞻检验'


def at(value):
    result=datetime.fromisoformat(value)
    if result.utcoffset() is None:raise ValueError('timezone required')
    return result


def source_value(row,field):
    bits=row['bits'][field]
    if isinstance(bits,bool) or not isinstance(bits,int) or not 0<=bits<=0xffffffff:
        raise ValueError('invalid float32 bits: '+field)
    n=struct.unpack('<f',struct.pack('<I',bits))[0]
    if not math.isfinite(n):raise ValueError('nonfinite source: '+field)
    return Decimal.from_float(n)


def available(row,artifact):
    n=source_value(row,'FN314')
    if n!=int(n) or not 10000<=n<=991231:raise ValueError('missing/invalid FN314')
    day=date.fromisoformat(f'{2000+int(n)//10000:04d}-{int(n)//100%100:02d}-{int(n)%100:02d}')
    if day<=date.fromisoformat(row['period']) or day>at(artifact['fetched_at']).astimezone(timezone(timedelta(hours=8))).date():
        raise ValueError('FN314 outside report/fetch dates')
    return datetime.combine(day+timedelta(days=1),datetime.min.time(),timezone(timedelta(hours=8)))



def financial_value(row, field):
    """只接收通用字段并归一化为元；源值、源位另行保留，未知名称拒绝。"""
    return source_value(row, FIELDS[field][0]) * VALUE_MULTIPLIERS.get(field, 1)


def period_basis(field):
    return FIELDS[field][1]


def source_field(field):
    """用于源证据序列化及冻结协议校验，不供经营公式选字段。"""
    return FIELDS[field][0]


def source_components(parts):
    return {source_field(field): amount for field, amount in parts.items()}
