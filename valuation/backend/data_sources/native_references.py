"""DuckDB发布 → SQLite参考快照 → 原生模型；不读取workspace散落工作簿。"""
import hashlib
import json
import re
from pathlib import Path
from .damodaran_store import DamodaranStore
from .industry_mapper import IndustryMapper, CompanyInfo

CONTRACT = 'alphalake-native-references-v1'
COUNTRIES = {'CN': 'China', 'HK': 'Hong Kong', 'US': 'United States'}
METRICS = {
 'beta_unlevered': 'beta_u', 'beta_unlevered_cash_adjusted': 'beta_u_corrected_for_cash',
 'debt_equity_ratio': 'd_e_ratio', 'effective_tax_rate': 'effective_tax_rate',
 'cost_of_equity': 'cost_of_equity', 'cost_of_debt_pretax': 'cost_of_debt_pretax',
 'weighted_average_cost_of_capital': 'wacc', 'equity_return_standard_deviation': 'std_dev_stock',
 'pretax_operating_margin': 'pretax_operating_margin', 'aftertax_operating_margin': 'aftertax_operating_margin',
 'profitable_firms_effective_tax_rate': 'effective_tax_rate_avg', 'sales_to_invested_capital_ltm': 'sales_to_capital',
 'expected_ebit_growth': 'expected_ebit_growth', 'return_on_invested_capital': 'roic',
 'debt_capital_ratio': 'debt_capital_ratio', 'enterprise_value_ebitda_multiple': 'ev_ebitda',
 'current_price_earnings_multiple': 'pe_ratio', 'price_book_multiple': 'pbv_ratio', 'enterprise_value_sales_multiple': 'ev_sales',
}
TABLES = ('reference_release', 'reference_value', 'reference_company')


def policy_hashes():
    # 原引擎的可选合成评级、区域ERP和分位数基准仍属冻结模型参考，单独标版本。
    root = Path(__file__).parent
    return {name: hashlib.sha256((root/name).read_bytes()).hexdigest()
            for name in ('cost_of_capital_reference.json', 'industry_stats.json')}


def snapshot_hash(connection, header):
    h = hashlib.sha256(json.dumps(header, sort_keys=True, separators=(',', ':')).encode())
    for table in TABLES:
        h.update(table.encode())
        for row in connection.execute(f'SELECT * FROM {table} ORDER BY 1,2,3'):
            h.update(json.dumps(list(row), ensure_ascii=False, separators=(',', ':')).encode())
            h.update(b'\n')
    return h.hexdigest()


def write_snapshot(connection, packet):
    if packet['contract'] != CONTRACT:
        raise ValueError('unsupported native reference contract')
    connection.executescript('''
 CREATE TABLE reference_release(release_id INTEGER PRIMARY KEY,dataset TEXT NOT NULL,source_version TEXT,available_at TEXT NOT NULL,first_seen_at TEXT NOT NULL,source_locator TEXT NOT NULL,sha256 TEXT NOT NULL,content_key TEXT NOT NULL,parser_version TEXT NOT NULL,normalization_version TEXT NOT NULL);
 CREATE TABLE reference_value(release_id INTEGER NOT NULL,subject TEXT NOT NULL,metric TEXT NOT NULL,region TEXT NOT NULL,value TEXT,status TEXT NOT NULL,unit TEXT NOT NULL,source_locator TEXT NOT NULL,raw_value TEXT NOT NULL,sample_count INTEGER,method_code TEXT NOT NULL,observation_date TEXT NOT NULL,PRIMARY KEY(release_id,subject,metric,region));
 CREATE TABLE reference_company(release_id INTEGER NOT NULL,ticker TEXT PRIMARY KEY,name TEXT NOT NULL,industry TEXT NOT NULL,country TEXT NOT NULL,sector TEXT,sic_code TEXT,broad_group TEXT,sub_group TEXT,source_locator TEXT NOT NULL);
 ''')
    for r in packet['releases']:
        connection.execute('INSERT INTO reference_release VALUES(?,?,?,?,?,?,?,?,?,?)', tuple(r[k] for k in (
            'release_id','dataset','source_version','available_at','first_seen_at','source_locator','sha256','content_key','parser_version','normalization_version')))
    for group in ('industry_stats','country_tax','country_risk'):
        for r in packet[group]:
            connection.execute('INSERT INTO reference_value VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
                r['release_id'],r.get('industry',r.get('subject_code')),r['metric_code'],r.get('sample_region',''),
                None if r['value'] is None else str(r['value']),r['value_status'],r['raw_unit'],r['source_locator'],r['raw_value'],r.get('sample_count'),r['method_code'],r['observation_date']))
    for r in packet['companies']:
        co=json.loads(r['raw_payload'])
        connection.execute('INSERT INTO reference_company VALUES(?,?,?,?,?,?,?,?,?,?)', (
            r['release_id'],co['ticker'],co['name'],co['industry'],co['country'],co['sector'],str(co['sic_code']),co['broad_group'],co['sub_group'],r['source_locator']))
    header = dict(contract=CONTRACT, information_as_of=packet['information_as_of'], model_reference_hashes=policy_hashes(),
        country_scope=['CN','HK','US'], industry_regions=['US','Global'])
    connection.executemany('INSERT INTO metadata VALUES(?,?)', [
        ('reference_header',json.dumps(header,sort_keys=True)),('reference_snapshot_id',snapshot_hash(connection,header))])
    load_snapshot(connection)  # refuse incomplete snapshots before publication


def load_snapshot(connection):
    metadata = dict(connection.execute("SELECT key,value FROM metadata WHERE key IN ('reference_header','reference_snapshot_id')"))
    header=json.loads(metadata['reference_header'])
    if header['contract'] != CONTRACT or header['model_reference_hashes'] != policy_hashes():
        raise ValueError('reference/model version mismatch; rebuild SQLite explicitly')
    identity=snapshot_hash(connection,header)
    if identity!=metadata['reference_snapshot_id']:
        raise ValueError('native reference snapshot hash mismatch')
    store=DamodaranStore()
    store.reference_snapshot=dict(id=identity,**header)
    releases=[dict(zip(('release_id','dataset','source_version','available_at','first_seen_at','source_locator','sha256','content_key','parser_version','normalization_version'),r)) for r in connection.execute('SELECT * FROM reference_release ORDER BY release_id')]
    if len(releases)!=25 or any(not re.fullmatch('[0-9a-f]{64}',r['sha256']) or not re.fullmatch('[0-9a-f]{64}',r['content_key']) for r in releases):
        raise ValueError('incomplete native reference releases')
    store.reference_snapshot['releases']=releases
    for release,subject,metric,region,value,status,unit,locator,raw,count,method,observation_date in connection.execute('SELECT * FROM reference_value ORDER BY release_id,subject,metric,region'):
        number=float(value) if status=='reported' and value is not None else None
        if region:
            key=({'us':'US','global':'Global'}[region],subject)
            values=store._industry_data.setdefault(key,{})
            field=METRICS[metric]
            if field in values:
                raise ValueError('duplicate industry metric across reference releases')
            values[field]=number
        elif metric=='corporate_marginal_tax_rate':
            store._country_tax[COUNTRIES[subject]]=dict(corporate_tax_rate=number, value_status=status)
        elif metric=='mature_market_erp':
            store._country_risk['__mature_market_erp__']=dict(equity_risk_premium=number)
        else:
            field='default_spread' if metric=='sovereign_default_spread' else metric
            store._country_risk.setdefault(COUNTRIES[subject],{})[field]=number
    for region in ('US','Global'):
        names=store.list_industries(region)
        if len(names)!=94:
            raise ValueError('incomplete industry reference scope: '+region)
        for name in names:
            values=store._industry_data[(region,name)]
            if set(values)!=set(METRICS.values()):
                raise ValueError('missing industry reference metrics: '+name)
            if any(values[f] is None for f in ('beta_u','beta_u_corrected_for_cash','cost_of_debt_pretax')):
                raise ValueError('missing required native WACC reference: '+name)
    for name in COUNTRIES.values():
        risk=store._country_risk.get(name,{})
        if any(risk.get(f) is None for f in ('total_equity_risk_premium','country_risk_premium','default_spread')) or name not in store._country_tax:
            raise ValueError('missing country ERP/tax reference: '+name)
    mapper=IndustryMapper()
    for release,ticker,name,industry,country,sector,sic,broad,sub,locator in connection.execute('SELECT * FROM reference_company ORDER BY ticker'):
        mapper._index_company(CompanyInfo(name,ticker,industry,sector,sic,country,broad,sub))
    if mapper.total_companies==0:
        raise ValueError('missing published company classifications')
    store.industry_mapper=mapper
    store._industry_stats=json.loads((Path(__file__).parent/'industry_stats.json').read_text())['industries']
    store.industries_loaded={r:len(store.list_industries(r)) for r in ('US','Global')}
    store.countries_loaded=3
    return store


def reference_gaps(store, ticker, industry_override=None, country_override=None):
    """默认行业/国家选择的准入；显式行业选择是会话假设，不改公司分类。"""
    company=store.industry_mapper.lookup(ticker)
    industry=industry_override or (company.industry_group if company else None)
    country=country_override or (company.country if company else None)
    gaps=[]
    if not industry or store.lookup_industry(industry,region='US') is None:
        gaps.append(dict(field='industry',status='missing_company_industry_reference',reason='缺少已审核US行业选择；可使用原有industry_override显式选择，不套占位行业'))
    try:
        macro=store.lookup_country(country) if country else None
        if macro is None:
            raise ValueError('Country ERP/tax reference unavailable')
    except ValueError as error:
        gaps.append(dict(field='country_erp_and_tax',status='missing_or_ambiguous_reference',reason=str(error)))
    return gaps


def attach_reference_diagnostic(diagnostic, store, ticker, industry_override=None):
    result=dict(diagnostic,financial_status=diagnostic['status'],reference_snapshot_id=store.reference_snapshot.get('id'))
    result['reference_missing']=reference_gaps(store,ticker,industry_override)
    if result['status']=='ready' and result['reference_missing']:
        result['status']='blocked_reference_inputs'
        result['blockers']=[r['reason'] for r in result['reference_missing']]
    return result
