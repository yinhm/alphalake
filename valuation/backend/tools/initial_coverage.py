"""初次覆盖的资料整理与只读研究交付；不选择预测参数或改写估值引擎。"""
import argparse
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
from urllib.request import Request, urlopen

from data_sources.paths import workspace_path

BUSINESS_URL = 'https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax?code='
DIMENSIONS = {'1': 'industry', '2': 'product', '3': 'region'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def business_data(raw, ticker, report_end, information_date):
    """东财经营披露是二级研究资料；原单位元，不进入TDX标准事实。"""
    if not re.fullmatch(r'(?:SHSE|SZSE):[0-9]{6}', ticker):
        raise ValueError('沪深证券标识 required')
    expected = ticker[5:]+('.SH' if ticker.startswith('SHSE:') else '.SZ')
    date.fromisoformat(report_end)
    date.fromisoformat(information_date)
    if raw[:2] == b'\x1f\x8b':
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as packed:
            raw = packed.read((8 << 20)+1)
        if len(raw) > 8 << 20:
            raise ValueError('解压后的经营响应超过8MiB限制')
    payload = json.loads(raw)
    segments, reviews, scopes = [], [], []
    identities = set()
    for key in ('zyfw', 'zygcfx', 'jyps'):
        rows = payload.get(key)
        if not isinstance(rows, list):
            raise ValueError('经营披露数组缺失：'+key)
        for i, row in enumerate(rows):
            if (not isinstance(row, dict) or row.get('SECUCODE') != expected
                    or row.get('SECURITY_CODE') != ticker[5:]):
                raise ValueError('经营披露证券身份冲突')
            identities.add(row['SECUCODE'])
            if key == 'zyfw':
                scopes.append(row.get('BUSINESS_SCOPE') or '')
                continue
            period = str(row.get('REPORT_DATE', ''))[:10]
            date.fromisoformat(period)
            if period > information_date:
                continue
            locator = f'$.{key}[{i}]'
            if key == 'jyps':
                reviews.append(dict(period=period, text=row.get('BUSINESS_REVIEW') or '', locator=locator))
            else:
                value = row.get('MAIN_BUSINESS_INCOME')
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                    raise ValueError('非法业务收入金额；未知必须保留null')
                ratios = {name:row.get(key) for name,key in (('revenue_share','MBI_RATIO'),('gross_margin','GROSS_RPOFIT_RATIO'))}
                if any(v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v)) for v in ratios.values()):
                    raise ValueError('非法业务比率')
                segments.append(dict(period=period, dimension=DIMENSIONS.get(str(row.get('MAINOP_TYPE')), 'unresolved'),
                    source_dimension=str(row.get('MAINOP_TYPE')), name=row.get('ITEM_NAME'), revenue_cny=value, locator=locator, **ratios))
    if not identities:
        raise ValueError('经营披露为空，不将空响应当作公司没有业务')
    return dict(ticker=ticker, report_end=report_end, information_date=information_date, business_scopes=scopes, segments=segments, reviews=reviews,
        source_role='secondary_company_disclosure_for_research_only', monetary_unit='CNY',
        boundary='经营结构与公司自述，不覆盖TDX财务总量；未知维度不猜，产品/行业/地区不得相加；汇编不是竞争优势的独立验证')


def collect_one(row, cache, known, report_end):
    ticker = row['ticker']
    code = ('SH' if ticker.startswith('SHSE:') else 'SZ')+ticker[5:]
    url = BUSINESS_URL+code
    try:
        previous = known.get(ticker)
        if previous:
            path = workspace_path(previous['path'])
            if (not path.resolve().is_relative_to(workspace_path().resolve()) or digest(path) != previous['sha256']):
                raise ValueError('公司经营缓存损坏或越界')
            raw, fetched_at = path.read_bytes(), previous['fetched_at']
            retrieval = 'existing_cache'
        else:
            request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer':'https://emweb.securities.eastmoney.com/'})
            with urlopen(request, timeout=20) as response:
                raw = response.read((8 << 20)+1)
            if len(raw) > 8 << 20:
                raise ValueError('经营响应超过8MiB限制')
            fetched_at = datetime.now(timezone.utc).isoformat()
            retrieval = 'online_response'
        sha = hashlib.sha256(raw).hexdigest()
        path = cache/(code+'-'+sha[:16]+('.json.gz' if raw[:2] == b'\x1f\x8b' else '.json'))
        if path.exists():
            if digest(path) != sha:
                raise ValueError('既有经营对象哈希冲突')
        else:
            path.write_bytes(raw)
        parsed = business_data(raw, ticker, report_end, fetched_at[:10])
        return dict(ticker=ticker, status='collected', path=path.relative_to(workspace_path()).as_posix(),
            sha256=sha, url=url, fetched_at=fetched_at, retrieval=retrieval,
            latest_review=max((r['period'] for r in parsed['reviews']),default=None),
            latest_segment=max((r['period'] for r in parsed['segments']),default=None), bytes=len(raw))
    except Exception as exc:
        return dict(ticker=ticker, status='source_failed', url=url, error_type=type(exc).__name__, reason=str(exc))


def collect(protocol_file):
    protocol = json.loads(protocol_file.read_text())
    cache = workspace_path('eastmoney', 'business-analysis')
    cache.mkdir(parents=True, exist_ok=True)
    index = cache/'index.json'
    known = json.loads(index.read_text())['records'] if index.exists() else {}
    results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        tasks = [executor.submit(collect_one, row, cache, known, protocol['financial_report_period']) for row in protocol['companies']]
        for task in as_completed(tasks):
            result = task.result()
            results.append(result)
            if len(results) % 20 == 0:
                print(json.dumps(dict(collected=len(results), total=len(tasks), failed=sum(r['status']!='collected' for r in results))), flush=True)
    records = {r['ticker']:r for r in results if r['status']=='collected'}
    receipt = dict(contract='initial-coverage-business-collection-v1', protocol_sha256=digest(protocol_file),
        acquired_at=datetime.now(timezone.utc).isoformat(), requested=len(tasks), collected=len(records),
        failures=[r for r in results if r['status']!='collected'], records=records,
        boundary='本批仅取得公司经营汇编，不认证全文语义、公司预测或财务事实；失败公司保留原名单')
    save(protocol_file.parent/'business-collection.json', receipt)
    save(index, dict(contract='company-business-cache-v1', records=known|records))
    return {k:v for k,v in receipt.items() if k!='records'}


def collect_facts(protocol_file):
    from decimal import Decimal
    from tools.export_alphalake_sqlite import FIELDS, record_facts
    protocol = json.loads(protocol_file.read_text())
    database, source = workspace_path('valuation.sqlite'), workspace_path('alphalake.duckdb')
    if digest(database) != protocol['sqlite_sha256'] or digest(source) != protocol['source_database_sha256']:
        raise ValueError('研究冻结库已变化，须重新冻结，不混接版本')
    tickers = [r['ticker'] for r in protocol['companies']]
    end = date.fromisoformat(protocol['financial_report_period'])
    base_year = end.year-1 if end.month != 12 else end.year
    periods = sorted({f'{year}-12-31' for year in range(base_year-4, base_year+1)} |
                     {end.replace(year=end.year-i).isoformat() for i in range(3)})
    fields = {FIELDS[k][0]: FIELDS[k][1] for k in
              ('revenues','ebit','net_income','capex','r_and_d_expense','bv_equity')}
    fields.update({'operating_cash_flow_cumulative':'ytd','inventories':'instant',
                   'accounts_receivable':'instant','accounts_payable':'instant',
                   'total_assets':'instant','total_liabilities':'instant','monetary_funds':'instant'})
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        # 当前研究事实与冻结估值输入分开；按本范围逐金额核对，不认证旧库全范围相同。
        with tempfile.TemporaryDirectory(prefix='alphalake-initial-facts-') as pending:
            pending = Path(pending)
            spec = pending/'projection.json'
            save(spec,{field:periods for field in fields})
            destination = pending/'standard'
            binary = Path(__file__).resolve().parents[3]/'alphalake'
            subprocess.run([str(binary),'export-financial-snapshot',str(source),'--output',str(destination),
                '--codes',','.join(t[5:] for t in tickers),'--fields',','.join(sorted(fields)),
                '--field-periods',str(spec),'--from',periods[0],'--period',end.isoformat(),
                '--as-of',protocol['frozen_at']],check=True,timeout=600,capture_output=True)
            definitions = [json.loads(line) for line in (destination/'fields.jsonl').read_text().splitlines()]
            conflicts = [json.loads(line) for line in (destination/'conflicts.jsonl').read_text().splitlines()]
            if conflicts:
                save(protocol_file.parent/'standard-source-conflicts.json',conflicts)
                raise ValueError('本研究范围有标准来源冲突')
            ticker_by_code = {t[5:]:t for t in tickers}
            with (destination/'records.jsonl').open() as stream:
                facts = [row|dict(ticker=ticker_by_code[row['code']]) for row in record_facts(stream,definitions)]
        grouped = {t:{p:dict(values_cny={},source_record=None) for p in periods} for t in tickers}
        for fact in facts:
            ticker, period, field = fact['ticker'], fact['period'], fact['field']
            expected = 'instant' if fields[field]=='instant' else {3:'Q1',6:'H1',9:'9M',12:'FY'}[date.fromisoformat(period).month]
            if (ticker not in grouped or period not in periods or field not in fields
                    or (fact['unit'],fact['period_type'],fact['statement_scope']) != ('CNY',expected,'provider_default')
                    or not Decimal(fact['value']).is_finite()):
                raise ValueError('不兼容的经营事实单位/期间/范围')
            entry = grouped[ticker][period]
            if field in entry['values_cny']:
                raise ValueError('重复经营事实')
            record = {k:fact[k] for k in ('instrument_id','source_record_id','artifact_sha256','available_at','announcement_source','source_observed_at')}
            if entry['source_record'] is not None and entry['source_record'] != record:
                raise ValueError('同报告期出现冲突的来源记录')
            entry['source_record'] = record
            entry['values_cny'][field] = fact['value']
        annual = {}
        marks = ','.join('?' for _ in tickers)
        for r in connection.execute(f'SELECT * FROM financials_annual WHERE ticker IN ({marks}) AND fy_offset BETWEEN 0 AND 4', tickers):
            annual.setdefault(r['ticker'],[]).append(dict(r))
        comparisons, mismatches = 0, []
        for ticker in tickers:
            for row in annual.get(ticker,[]):
                period = f"{base_year-row['fy_offset']}-12-31"
                for key in ('revenues','ebit','net_income','capex','r_and_d_expense','bv_equity'):
                    value = grouped[ticker][period]['values_cny'].get(FIELDS[key][0])
                    expected = float(Decimal(value)/Decimal(1000000)) if value is not None else None
                    if expected != row.get(key):
                        mismatches.append(dict(ticker=ticker,period=period,field=key,source=expected,sqlite=row.get(key)))
                    comparisons += 1
        if mismatches:
            save(protocol_file.parent/'financial-differences.json',mismatches)
            raise ValueError('标准来源与冻结SQLite年度输入存在差异')
    if digest(database) != protocol['sqlite_sha256'] or digest(source) != protocol['source_database_sha256']:
        raise ValueError('取证期间数据库变化')
    save(protocol_file.parent/'financial-facts.json',dict(contract='initial-coverage-standard-facts-v1',
        source_database_sha256=protocol['source_database_sha256'],sqlite_sha256=protocol['sqlite_sha256'],
        report_end=end.isoformat(),periods=periods,fields=fields,companies=grouped,annual=annual,
        source_rows=len(facts),native_comparisons=comparisons,differences=[],
        research_information_as_of=protocol['frozen_at'],valuation_information_as_of=protocol['information_as_of'],
        frozen_snapshot_global_equivalence=False,selected_annual_values_verified=True,
        boundary='当前标准源研究查询，单位元；与冻结SQLite按本范围年度重叠金额核对，不认证旧快照全范围相同。额外现金/周转事实是本次研究资料，不回写冻结估值；共享血缘按报告期保存，未知仍为空。'))
    return dict(status='verified',companies=len(tickers),facts=len(facts),native_comparisons=comparisons)


def report_folder(ticker, protocol):
    return workspace_path('research', 'initial-'+('sh-' if ticker.startswith('SHSE:') else 'sz-')+ticker[5:]+'-'+protocol['frozen_at'][:10].replace('-',''))


def calculate(protocol_file, tickers=None):
    """重放事先冻结的完整请求；风险敏感性只修改现有的一个显式入口。"""
    os.environ['US_CN_HK_DB_PATH'] = str(workspace_path('valuation.sqlite'))
    from fastapi.testclient import TestClient
    from api.main import app
    from tools.evaluate_native_policy import calculate_request
    from tools.check_native_sqlite import value_digest
    from tools.publish_native_valuation import runtime_identity
    protocol = json.loads(protocol_file.read_text())
    selected = {r['ticker'] for r in protocol['companies']}
    if tickers:
        if not set(tickers).issubset(selected):
            raise ValueError('试运行证券必须在冻结名单内')
        selected = set(tickers)
    source = workspace_path(protocol['calculation_archive'])
    if digest(source) != protocol['calculation_archive_sha256'] or digest(workspace_path('valuation.sqlite')) != protocol['sqlite_sha256']:
        raise ValueError('冻结请求或SQLite变化')
    with sqlite3.connect(workspace_path('valuation.sqlite').as_uri()+'?mode=ro',uri=True) as c:
        metadata = dict(c.execute("SELECT key,value FROM metadata WHERE key NOT LIKE 'input_context:%'"))
    runtime = runtime_identity(workspace_path())
    counts, failures, used = {}, [], set()
    with gzip.open(source,'rt',encoding='utf-8') as rows, TestClient(app,raise_server_exceptions=False) as client:
        for line in rows:
            original = json.loads(line)
            ticker = original['ticker']
            if ticker not in selected:
                continue
            if ticker in used:
                raise ValueError('冻结请求有重复证券')
            used.add(ticker)
            folder = report_folder(ticker,protocol)
            output = folder/'valuation.json'
            if output.exists():
                previous = json.loads(output.read_text())
                if (previous['protocol_sha256']!=digest(protocol_file) or previous['request']!=original['request']
                        or previous['response']['dcf']!=original['response']['dcf']
                        or previous['response']['final']!=original['response']['final']):
                    raise ValueError('已有公司计算与冻结输入冲突')
                counts['reused_verified'] = counts.get('reused_verified',0)+1
                continue
            request = original['request']
            body, review = calculate_request(client,request,metadata,'http://127.0.0.1:8080',source_context=original['response'])
            if body is None:
                failures.append(dict(ticker=ticker,phase='baseline',detail=review))
                continue
            if (body['dcf']!=original['response']['dcf'] or body['final']!=original['response']['final']
                    or review['economic_checks']['cashflow_bridge']['status']!='arithmetic_reconciled'):
                raise ValueError('原冻结请求重放值或现金流闭合发生变化：'+ticker)
            sensitivities = []
            for shift in (-100,100):
                alternative = deepcopy(request)
                choices = alternative['inputs']['methodology_choices']
                before = choices.get('wacc_level_shift_bps') or 0.0
                choices['wacc_level_shift_bps'] = before+shift
                alt, checked = calculate_request(client,alternative,metadata,'http://127.0.0.1:8080',source_context=body)
                sensitivities.append(dict(shift_bps=shift,input_path='inputs.methodology_choices.wacc_level_shift_bps',
                    request=alternative,status='calculated' if alt is not None else 'rejected',response=alt,
                    review=checked,terminal_input_policy='终值及显式融资输入不另改；实际初始/终值WACC在报告中并列，不猜测全路径平移'))
            folder.mkdir(parents=True,exist_ok=True)
            result = dict(contract='initial-coverage-calculation-v1',protocol_sha256=digest(protocol_file),
                ticker=ticker,mode=original['mode'],request=request,response=body,review=review,
                source_archive=protocol['calculation_archive'],source_archive_sha256=protocol['calculation_archive_sha256'],
                runtime=runtime,sensitivities=sensitivities,live_verified=True,
                financial_input_snapshot=protocol['sqlite_sha256'],model_parameters_changed=False)
            result['run_id'] = value_digest(dict(ticker=ticker,request=request,runtime=runtime,protocol_sha256=result['protocol_sha256']))
            save(output,result)
            counts[original['mode']] = counts.get(original['mode'],0)+1
            if len(used)%20==0:
                print(json.dumps(dict(calculated_or_verified=len(used),total=len(selected),failures=len(failures))),flush=True)
    if used != selected:
        raise ValueError('冻结请求归档缺证券')
    if digest(workspace_path('valuation.sqlite')) != protocol['sqlite_sha256']:
        raise ValueError('计算时SQLite变化')
    summary = dict(contract='initial-coverage-calculation-summary-v1',requested=len(selected),processed=len(used),
        modes=counts,failures=failures,all_live_verified=not failures,model_changed=False,new_defaults=False)
    save(protocol_file.parent/('pilot-calculation.json' if tickers else 'calculation-summary.json'),summary)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command', required=True)
    source=sub.add_parser('collect')
    source.add_argument('--protocol', type=Path, required=True)
    facts=sub.add_parser('facts')
    facts.add_argument('--protocol', type=Path, required=True)
    calculation=sub.add_parser('calculate')
    calculation.add_argument('--protocol', type=Path, required=True)
    calculation.add_argument('--ticker', action='append')
    delivery=sub.add_parser('build')
    delivery.add_argument('--protocol', type=Path, required=True)
    delivery.add_argument('--ticker', action='append')
    delivery.add_argument('--revision')
    overview=sub.add_parser('catalogue')
    overview.add_argument('--protocol', type=Path, required=True)
    overview.add_argument('--revision')
    args=parser.parse_args()
    if args.command=='collect':
        print(json.dumps(collect(args.protocol), ensure_ascii=False))
    elif args.command=='facts':
        print(json.dumps(collect_facts(args.protocol), ensure_ascii=False))
    elif args.command=='calculate':
        print(json.dumps(calculate(args.protocol,args.ticker), ensure_ascii=False))
    elif args.command=='build':
        from tools.initial_coverage_report import build
        print(json.dumps(build(args.protocol,args.ticker,args.revision), ensure_ascii=False))
    elif args.command=='catalogue':
        from tools.initial_coverage_report import catalogue
        print(json.dumps(catalogue(args.protocol,args.revision), ensure_ascii=False))


if __name__=='__main__':
    main()
