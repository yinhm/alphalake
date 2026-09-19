"""TDX简化研究源适配；不发布标准事实，不扩大生产字段有效期。"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import math
import struct

# 与Go解析/数据库共用完整目录；此接口始终是源研究层，不批准标准事实。
# 日期、预告、供应商TTM/比例和未明确期间的量不能自动进入经营窗口。
import csv
from collections import Counter
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[3] / 'internal/source/tdx/financial/catalog.csv'
with CATALOG_PATH.open() as _file:
    _catalog = list(csv.DictReader(_file))
_names = Counter(row['name'] for row in _catalog if row['name'])
_rows = [row for row in _catalog if row['name'] and _names[row['name']] == 1
         and row['value_kind'] in ('monetary', 'shares')
         and row['unit'] in ('CNY', 'share') and row['multiplier']
         and row['period_basis'] in ('instant', 'ytd', 'quarter')
         and row['category'] not in ('forecast', 'preliminary', 'provider_ratio')]
FIELDS = {row['name']: ('FN' + row['index'], row['period_basis']) for row in _rows}
VALUE_MULTIPLIERS = {row['name']: int(row['multiplier']) for row in _rows}
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
    """只接收目录通用字段并换算为目录单位；源观察不冒充标准事实。"""
    value = source_value(row, FIELDS[field][0])
    multiplier = VALUE_MULTIPLIERS.get(field, 1)
    return value if multiplier == 1 else value * multiplier


def period_basis(field):
    return FIELDS[field][1]


def source_field(field):
    """用于源证据序列化及冻结协议校验，不供经营公式选字段。"""
    return FIELDS[field][0]


def source_components(parts):
    return {source_field(field): amount for field, amount in parts.items()}


def evidence_value(evidence, field):
    """按通用字段解码冻结单字段证据；编码及倍率仍由源适配负责。"""
    return financial_value({"bits": {source_field(field): evidence["bits"]}}, field)


def source_evidence(row, field):
    """归档源位及编码值；应用金额另由financial_value交付。"""
    provider = source_field(field)
    return dict(field=provider, bits=row['bits'][provider], source_value=str(source_value(row, provider)),
                multiplier=VALUE_MULTIPLIERS.get(field, 1))


def canonical_components(parts):
    """读取冻结源分量字典；未知编号拒绝，返回通用键。"""
    names = {provider: field for field, (provider, _) in FIELDS.items()}
    return {names[provider]: amount for provider, amount in parts.items()}


def source_signal_archive(parts, inputs):
    """旧信号归档使用源编码单位；仅序列化，禁止回流经营计算。"""
    return dict(signals={source_field(field): str(amount / VALUE_MULTIPLIERS.get(field, 1)) for field, amount in parts.items()},
                source_inputs=[r | dict(multiplier=1) if 'multiplier' in r else r for r in inputs])


def canonical_field(provider):
    """将冻结源引用转换为通用名称；未知字段拒绝。"""
    return {key: field for field, (key, _) in FIELDS.items()}[provider]
