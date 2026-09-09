"""标准类别股本、同日未复权报价和汇率到市场权益金额；估计政策另列。"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class MarketPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    max_price_age_days: int = Field(ge=0,le=31)
    max_share_age_days: int = Field(ge=0,le=366)
    max_financial_age_days: int = Field(ge=0,le=366)
    share_carry_reason: str = Field(min_length=1)
    fx_policy: Literal['same_day_safe_central_parity']
    fx_reason: str = Field(min_length=1)
    debt_value_basis: Literal['reviewed_book_proxy','contractual_cashflow_pv_upper_bound']
    debt_value_reason: str = Field(min_length=1)
    operating_equity_basis: Literal['invert_reviewed_equity_bridge']
    scope_reason: str = Field(min_length=1)


class MarketSnapshot(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    contract_version: Literal['alphalake-market-capital-v1']
    code: Literal['300866','600519']
    market_date: date
    information_as_of: datetime
    currency: Literal['CNY']
    releases: list[dict]
    share_counts: list[dict]
    a_quote: dict
    h_quote: dict | None = None
    fx: dict | None = None


def moment(value):
    t=datetime.fromisoformat(value)
    if t.utcoffset() is None: raise ValueError('aware market timestamps required')
    return t


def number(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d+(?:\.\d+)?',value):
        raise ValueError('market canonical decimal string required')
    n=Decimal(value)
    if not n.is_finite(): raise ValueError('non-finite market value')
    return n


def equity_market_value(s: MarketSnapshot, p: MarketPolicy, code, asof):
    """仅得出股票权益市值；不把流通市值当全部类别市值。"""
    try:
        return _equity(s,p,code,asof)
    except (KeyError,TypeError,ArithmeticError) as e:
        raise ValueError('malformed market capital packet: '+str(e)) from e


def _equity(s,p,code,asof):
    if s.code!=code or s.information_as_of!=asof or asof.utcoffset() is None:
        raise ValueError('market/company/cutoff mismatch')
    china=timezone(timedelta(hours=8))
    boundary=datetime.combine(s.market_date+timedelta(days=1),datetime.min.time(),china)
    if asof<boundary or not 0<=(asof.astimezone(china).date()-s.market_date).days<=p.max_price_age_days:
        raise ValueError('future/stale/unclosed market date')
    expected={'issuer_disclosure':'reviewed-share-classes-v1/'+code}
    if code=='300866': expected|={'hkex':'anker-unadjusted-close-v1','safe':'hkd-cny-central-parity-v1'}
    releases={}
    for r in s.releases:
        if r['source'] not in expected or r['dataset']!=expected.pop(r['source']):
            raise ValueError('duplicate/unreviewed market release')
        if type(r['release_id']) is not int or r['release_id']<=0 or r['release_id'] in releases or type(r['artifact_id']) is not int or r['artifact_id']<=0:
            raise ValueError('invalid market evidence IDs')
        if any(not re.fullmatch('[0-9a-f]{64}',r[k]) for k in ['content_key','artifact_sha256']):
            raise ValueError('invalid market evidence hash')
        if moment(r['available_at'])>asof or moment(r['recorded_at'])>asof:
            raise ValueError('unavailable market release')
        releases[r['release_id']]=r
    if expected: raise ValueError('missing market releases')
    def lineage(row,source):
        r=releases.get(row['release_id'])
        if not r or r['source']!=source or row['artifact_id']!=r['artifact_id'] or not row['source_locator']:
            raise ValueError('market row provenance mismatch')
    classes={}
    companies=set()
    observed=set()
    for row in s.share_counts:
        lineage(row,'issuer_disclosure')
        if type(row['observation_id']) is not int or row['observation_id']<=0 or row['observation_id'] in observed: raise ValueError('duplicate/invalid share observation ID')
        observed.add(row['observation_id'])
        if type(row['instrument_id']) is not int or row['instrument_id']<=0 or type(row['company_id']) is not int or row['company_id']<=0:
            raise ValueError('invalid issuer/class identity')
        companies.add(row['company_id'])
        age=(s.market_date-date.fromisoformat(row['effective_date'])).days
        if not 0<=age<=p.max_share_age_days or date.fromisoformat(row['listing_valid_from'])>s.market_date or moment(row['identity_recorded_at'])>asof:
            raise ValueError('stale/future share or listing identity')
        key=row['instrument_id']
        c=classes.setdefault(key,{'rows':{},'identity':{k:row[k] for k in ['listing_id','exchange_mic','trading_currency','symbol','provider','effective_date']}})
        if any(row[k]!=v for k,v in c['identity'].items()) or row['share_basis'] in c['rows']:
            raise ValueError('duplicate/ambiguous class or dual-listing count')
        value=number(row['value'])
        if value!=value.to_integral_value(): raise ValueError('fractional issued shares unsupported')
        c['rows'][row['share_basis']]=value
    if len(companies)!=1 or len(classes)!=(2 if code=='300866' else 1):
        raise ValueError('incomplete issuer class scope')
    quote=s.a_quote
    symbol='sz300866' if code=='300866' else 'sh600519'
    if quote['contract_version']!='alphalake-valuation-quote-v1' or quote['symbol']!=symbol or quote['currency']!='CNY' or quote['adjustment']!='unadjusted' or moment(quote['information_as_of'])!=asof:
        raise ValueError('A quote scope/adjustment mismatch')
    a=quote['quote']
    if a['trade_date']!=s.market_date.isoformat() or a['source']!='tdx' or moment(a['acquisition_started_at'])<boundary or moment(a['recorded_at'])>asof or moment(a['run_finished_at'])>asof:
        raise ValueError('A quote date/acquisition mismatch')
    fx=Decimal(1)
    if code=='300866':
        if s.h_quote is None or s.fx is None: raise ValueError('missing H quote/FX')
        h,f=s.h_quote,s.fx
        lineage(h,'hkex');lineage(f,'safe')
        if h['trade_date']!=s.market_date.isoformat() or h['currency']!='HKD' or h['adjustment']!='unadjusted' or number(h['raw_value'])!=number(h['close']):
            raise ValueError('H quote date/unit mismatch')
        if (f['base_currency'],f['quote_currency'],f['time_precision'],f['source_timezone'],f['fixing_code'],f['rate_type'],f['raw_unit'])!=('HKD','CNY','date','Asia/Shanghai','cfets_central_parity_safe','midpoint','CNY_per_100_HKD'):
            raise ValueError('FX direction/method mismatch')
        if moment(f['observed_at']).astimezone(china).date()!=s.market_date:
            raise ValueError('FX and quote market dates differ')
        fx=number(f['value'])
        if fx<=0 or fx!=number(f['raw_value'])/100: raise ValueError('FX conversion mismatch')
    elif s.h_quote is not None or s.fx is not None: raise ValueError('unexpected extra listing')
    total=Decimal(0);audit=[];seen=set()
    for key,c in classes.items():
        r,i=c['rows'],c['identity']
        if set(r)!={'issued','treasury','outstanding'} or r['issued']-r['treasury']!=r['outstanding'] or r['outstanding']<=0:
            raise ValueError('issued minus treasury must equal outstanding')
        if i['trading_currency']=='CNY':
            if i['symbol']!=symbol or i['provider']!='tdx' or i['exchange_mic']!=quote['exchange_mic'] or key!=a['instrument_id']:
                raise ValueError('A quote and share class mismatch')
            close=number(a['close']);rate=Decimal(1)
        elif code=='300866' and i['trading_currency']=='HKD':
            if i['symbol']!='00668' or i['provider']!='hkex' or i['exchange_mic']!='XHKG' or key!=s.h_quote['instrument_id'] or i['listing_id']!=s.h_quote['listing_id']:
                raise ValueError('H quote and share class mismatch')
            close=number(s.h_quote['close']);rate=fx
        else: raise ValueError('unreviewed class currency')
        if i['trading_currency'] in seen or close<=0: raise ValueError('duplicate/missing class price')
        seen.add(i['trading_currency'])
        value=r['outstanding']*close*rate;total+=value
        audit.append(dict(instrument_id=key,symbol=i['symbol'],shares=str(r['outstanding']),close=str(close),fx=str(rate),value_cny=str(value),share_age_days=(s.market_date-date.fromisoformat(i['effective_date'])).days))
    return total/Decimal(1000000),dict(status='market_prices_with_disclosed_share_carry',market_date=s.market_date.isoformat(),classes=audit,common_equity_million_cny=str(total/Decimal(1000000))),s.model_dump(mode='json')


def contractual_debt_value(note, book_debt, rate):
    """六月末合同组合按选定 Kd 重估：区间上界入模，下界仅作敏感性边界。"""
    if not 0 <= rate < 1:
        raise ValueError('nonnegative debt discount rate required for maturity bounds')
    rows=[]
    for key in ('short','long','lease','bond'):
        values=[Decimal(str(note('debt_cf_'+key+'_'+c))) for c in ('0_1','1_2','2_5','5_plus','total','book')]
        if any(v<0 or not v.is_finite() for v in values) or abs(sum(values[:4])-values[4])>Decimal('0.00000001'):
            raise ValueError('invalid contractual debt cashflow reconciliation: '+key)
        rows.append((key,values))
    book=sum(v[5] for _,v in rows)
    # TDX float32 源精度界限，PDF 不覆盖标准余额；不是会计金额精确相等。
    if abs(book-Decimal(str(book_debt)))>book*Decimal(2)**-23:
        raise ValueError('contractual debt book scope differs from standard debt')
    discount=Decimal(1)+Decimal(str(rate))
    upper=lower=Decimal(0)
    for _,v in rows:
        upper+=sum(v[i]/discount**t for i,t in enumerate((0,1,2,5)))
        lower+=sum(v[i]/discount**t for i,t in enumerate((1,2,5)))
        # 开放的 >5 年期限没有可证明的最迟还款日；正利率下 PV 下确界为零。
        if rate==0: lower+=v[3]
    return float(upper),dict(status='contractual_cashflow_bound_not_observed_market_price',
        financial_book_million_cny=str(book),standard_book_million_cny=book_debt,
        lower_bound_million_cny=str(lower),upper_bound_million_cny=str(upper),
        selected_bound='upper',discount_rate=rate,
        timing_origin='financial_report_date_not_market_date',
        components={k:[str(x) for x in v] for k,v in rows},
        boundaries=['June-end contractual portfolio repriced at selected Kd; intervening repayments and new borrowing unavailable',
            'bucket boundaries bound payment timing; floating coupons held at disclosed report-date rates',
            'convertible contractual cashflows exclude the conversion option; book option adjustment remains a separate proxy',
            'one reference Kd for all debt currencies and tenors is a policy estimate',
            'upper debt PV is not necessarily an upper or lower WACC or equity valuation'])
