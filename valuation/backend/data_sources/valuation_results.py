"""已发布研究结果的紧凑只读索引；不访问财务库或计算引擎。"""
from collections import Counter
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .paths import workspace_path
from .research_reports import IDENTIFIER, ResearchError

CONTRACT = 'alphalake-valuation-results-v1'
ADMISSIONS = {'ready': '已计算', 'blocked_required_inputs': '缺财务输入',
              'requires_scope_review': '待范围审核', 'outside_product_scope': '产品范围外'}
FLAGS = {'nonpositive_equity_condition': '条件权益非正',
         'capital_release_requires_recoverability_basis': '资本释放缺回收依据',
         'capitalized_research_without_explicit_future_tax_path': '未供未来研发税务路径',
         'forecast_margin_outside_plus_minus_100_percent': '预测利润率超出±100%'}
COMPARISONS = {'capital_date_proxy': '资本基期代理',
               'unreconciled_post_period_capital_change': '期后资本变化未衔接',
               'not_calculated': '未计算／未比较', 'missing_price': '无合格报价',
               'not_compared': '未提供价差', 'unsupported': '不支持的比较状态'}
ASSUMPTIONS = ('initial_wacc_rate', 'first_year_growth_rate', 'first_year_adjusted_margin_rate',
               'first_year_funding_sales_to_capital', 'terminal_growth_rate',
               'terminal_wacc_rate', 'terminal_pv_share_rate')
SORTS = {'ticker': 'ticker', 'value': 'condition_value_cny_per_share', 'close': 'close_cny',
         'difference': 'descriptive_difference_pct', **{key: key for key in ASSUMPTIONS}}
RANGES = {'difference': ('descriptive_difference_pct', 'pct'),
          'wacc': ('initial_wacc_rate', 'rate'), 'growth': ('first_year_growth_rate', 'rate'),
          'margin': ('first_year_adjusted_margin_rate', 'rate'),
          'capital_ratio': ('first_year_funding_sales_to_capital', '')}


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def result_path(*parts):
    root = workspace_path().resolve()
    path = root
    for part in ('derived', 'valuation-results', *parts):
        if part not in ('current.json', 'index.json', 'publish.lock') and not re.fullmatch(IDENTIFIER, part):
            raise ResearchError(404, 'dataset_missing', '估值结果批次不存在')
        path = path / part
        if path.is_symlink():
            raise ResearchError(503, 'invalid_results', '估值结果路径不能使用别名')
    return path


def read_json(path, maximum=64 * 1024 * 1024):
    try:
        if not path.is_file() or path.stat().st_size > maximum:
            raise ValueError('文件过大')
        data = path.read_bytes()
        if len(data) > maximum:
            raise ValueError('文件过大')
        body = json.loads(data, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        return body, hashlib.sha256(data).hexdigest()
    except (OSError, ValueError) as exc:
        raise ResearchError(503, 'invalid_results', '已发布估值结果无法读取或内容无效') from exc


def registry():
    path = result_path('current.json')
    if not path.exists():
        return dict(contract=CONTRACT, current=None, datasets={})
    data, _ = read_json(path, 1024 * 1024)
    if (not isinstance(data, dict) or data.get('contract') != CONTRACT
            or not isinstance(data.get('datasets'), dict)
            or (data.get('current') is not None and (not isinstance(data['current'], str)
                or data['current'] not in data['datasets']))):
        raise ResearchError(503, 'invalid_results', '估值结果登记无效')
    for key, entry in data['datasets'].items():
        if (not re.fullmatch(IDENTIFIER, key) or not isinstance(entry, dict)
                or not isinstance(entry.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', entry['sha256'])
                or not isinstance(entry.get('dataset'), dict) or entry['dataset'].get('id') != key
                or type(entry.get('universe_count')) is not int or entry['universe_count'] < 0):
            raise ResearchError(503, 'invalid_results', '估值结果登记无效')
    return data


@lru_cache(maxsize=2)
def _load(path, signature, expected_hash):
    data, digest = read_json(Path(path))
    if (digest != expected_hash or not isinstance(data, dict) or data.get('contract') != CONTRACT
            or not isinstance(data.get('rows'), list)
            or data.get('universe_count') != len(data['rows'])):
        raise ResearchError(503, 'invalid_results', '估值结果摘要哈希或契约不匹配')
    search = tuple(normalized(row['ticker'] + ' ' + row['name']) for row in data['rows'])
    return data, search


def load_dataset(identifier):
    records = registry()
    if identifier not in records['datasets']:
        raise ResearchError(404, 'dataset_missing', '估值结果批次不存在')
    entry = records['datasets'][identifier]
    path = result_path(identifier, 'index.json')
    try:
        info = path.stat()
        signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    except OSError as exc:
        raise ResearchError(503, 'invalid_results', '已发布估值结果摘要缺失') from exc
    data, search = _load(str(path), signature, entry['sha256'])
    if data['dataset'] != entry['dataset']:
        raise ResearchError(503, 'invalid_results', '估值结果摘要与登记不一致')
    return data, search, entry['sha256']


def datasets():
    records = registry()
    items = []
    for identifier, entry in records['datasets'].items():
        if not result_path(identifier, 'index.json').is_file():
            raise ResearchError(503, 'invalid_results', '已发布估值结果摘要缺失')
        items.append(dict(**deepcopy(entry['dataset']), content_hash=entry['sha256'], universe_count=entry['universe_count']))
    items.sort(key=lambda item: (item['research_date'] or '', item['id']), reverse=True)
    return dict(contract=CONTRACT, current=records['current'], datasets=items)


class ResultQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    q: str = Field(default='', max_length=160)
    admission: list[str] = Field(default_factory=list)
    industry: list[str] = Field(default_factory=list)
    flag: list[str] = Field(default_factory=list)
    comparison: list[str] = Field(default_factory=list)
    sort: str = 'ticker'
    direction: str = 'asc'
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)
    difference_min_pct: float | None = None
    difference_max_pct: float | None = None
    wacc_min_rate: float | None = None
    wacc_max_rate: float | None = None
    growth_min_rate: float | None = None
    growth_max_rate: float | None = None
    margin_min_rate: float | None = None
    margin_max_rate: float | None = None
    capital_ratio_min: float | None = None
    capital_ratio_max: float | None = None

    @model_validator(mode='after')
    def valid(self):
        for key, allowed in (('admission', ADMISSIONS), ('flag', FLAGS), ('comparison', COMPARISONS)):
            if set(getattr(self, key)) - set(allowed):
                raise ValueError('未知筛选条件：' + key)
        if self.sort not in SORTS or self.direction not in ('asc', 'desc'):
            raise ValueError('未知排序条件')
        for low, high in self.ranges().values():
            if low is not None and high is not None and low > high:
                raise ValueError('数值范围下限不能大于上限')
        return self

    def ranges(self):
        result = {}
        for name, (field, suffix) in RANGES.items():
            tail = '_' + suffix if suffix else ''
            low, high = getattr(self, name + '_min' + tail), getattr(self, name + '_max' + tail)
            if low is not None or high is not None:
                result[name] = (low, high)
        return result


def field_value(row, key):
    return row['assumptions'].get(key) if key in ASSUMPTIONS else row.get(key)


def query_results(identifier, query):
    data, search, digest = load_dataset(identifier)
    known_industries = {row['reference_industry'] or '' for row in data['rows']}
    if set(query.industry) - known_industries:
        raise ResearchError(422, 'invalid_filter', '未知估值参考行业')
    needle, ranges = normalized(query.q), query.ranges()
    choices = {key: set(getattr(query, key)) for key in ('admission', 'industry', 'flag', 'comparison')}
    facets = dict(admission=Counter(dict.fromkeys(ADMISSIONS, 0)), industry=Counter(dict.fromkeys(known_industries, 0)),
                  flag=Counter(dict.fromkeys(FLAGS, 0)), comparison=Counter(dict.fromkeys(COMPARISONS, 0)))
    unknowns = Counter({key: 0 for key in ranges})
    matched = []
    for row, text in zip(data['rows'], search):
        if needle and needle not in text:
            continue
        values = dict(admission=[row['admission']], industry=[row['reference_industry'] or ''],
                      flag=row['review_flags'], comparison=[row['comparison_display_status']])
        checks = {key: not selected or bool(selected.intersection(values[key])) for key, selected in choices.items()}
        for key, (low, high) in ranges.items():
            value = field_value(row, RANGES[key][0])
            checks[key] = value is not None and (low is None or value >= low) and (high is None or value <= high)
        failures = {key for key, passed in checks.items() if not passed}
        for key in facets:
            if not failures - {key}:
                facets[key].update(values[key])
        for key in ranges:
            if not failures - {key} and field_value(row, RANGES[key][0]) is None:
                unknowns[key] += 1
        if not failures:
            matched.append(row)
    key = SORTS[query.sort]
    matched.sort(key=lambda row: row['ticker'])
    known = [row for row in matched if field_value(row, key) is not None]
    unknown = [row for row in matched if field_value(row, key) is None]
    known.sort(key=lambda row: field_value(row, key), reverse=query.direction == 'desc')
    ordered = known + unknown
    offset = min(query.offset, ((len(ordered) - 1) // query.limit) * query.limit) if ordered else 0
    return dict(contract=CONTRACT, dataset=dict(**deepcopy(data['dataset']), content_hash=digest),
                universe_count=data['universe_count'], filtered_count=len(ordered),
                admission_counts=deepcopy(data['admission_counts']),
                labels=dict(admission=ADMISSIONS.copy(), flag=FLAGS.copy(), comparison=COMPARISONS.copy()),
                facets={key: dict(value) for key, value in facets.items()}, range_unknown_counts=dict(unknowns),
                offset=offset, limit=query.limit, rows=deepcopy(ordered[offset:offset + query.limit]),
                issues=deepcopy(data['issues']))
