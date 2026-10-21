"""初次覆盖的资料整理与只读研究交付；不选择预测参数或改写估值引擎。"""
import argparse
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
import tarfile
import tempfile
from urllib.request import Request, urlopen

from data_sources.paths import workspace_path

BUSINESS_URL = 'https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax?code='
DIMENSIONS = {'1': 'industry', '2': 'product', '3': 'region'}
PROTOCOL = 'initial-coverage-protocol-v2'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n')


def workspace_file(path):
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_relative_to(workspace_path().resolve()):
        raise ValueError('研究输入必须位于workspace内')
    return resolved.relative_to(workspace_path().resolve()).as_posix()


def load_protocol(path):
    protocol = json.loads(Path(path).read_text())
    if protocol.get('contract') != PROTOCOL:
        raise ValueError('须按当前契约重新冻结研究协议')
    return protocol


def verify_inputs(protocol, *, runtime=False):
    if Path(str(workspace_path('alphalake.duckdb'))+'.wal').exists():
        raise ValueError('主库仍有写入WAL，须关闭写入后冻结研究')
    for name, expected in protocol['protected'].items():
        if digest(workspace_path(name)) != expected:
            raise ValueError('冻结研究输入已变化：'+name)
    if runtime:
        from tools.publish_native_valuation import runtime_identity
        current = runtime_identity(workspace_path())
        for stage in ('calculation', 'review'):
            if current['stages'][stage] != protocol['runtime']['stages'][stage]:
                raise ValueError('冻结计算或审阅依赖已变化：'+stage)
        return current


def archive_rows(protocol):
    """逐成员验证后读取，不解包全市场响应或把它们常驻内存。"""
    path = workspace_path(protocol['calculation_archive'])
    manifest = json.loads(workspace_path(protocol['calculation_archive_manifest']).read_text())
    if digest(path) != protocol['calculation_archive_sha256'] or manifest['sha256'] != protocol['calculation_archive_sha256']:
        raise ValueError('冻结计算归档变化')
    expected = manifest['files']
    companies = {row['ticker']: row for row in protocol['companies']}
    members, tickers = set(), set()
    with tarfile.open(path, 'r|gz') as packed:
        for member in packed:
            if (not member.isfile() or member.name not in expected or member.name in members
                    or Path(member.name).name != member.name):
                raise ValueError('计算归档成员越界、重复或不完整')
            members.add(member.name)
            with packed.extractfile(member) as stream:
                raw = stream.read()
            if hashlib.sha256(raw).hexdigest() != expected[member.name]:
                raise ValueError('计算归档成员哈希不一致：'+member.name)
            row = json.loads(raw)
            if member.name == 'summary.json':
                if row['sha256'] != protocol['sqlite_sha256'] or row['companies'] != len(companies):
                    raise ValueError('计算归档不是冻结发布快照')
                continue
            ticker = row['ticker']
            if ticker not in companies or ticker in tickers or member.name != ticker.replace(':', '-')+'.json':
                raise ValueError('计算归档证券范围冲突')
            frozen = companies[ticker]
            from tools.check_native_sqlite import value_digest
            if (row['http_status'] not in (200,422)
                    or row['admission'] != frozen['admission'] or row['http_status'] != frozen['http_status']
                    or row['financial_admission'] != frozen['financial_admission']
                    or value_digest(row['diagnostic']) != frozen['diagnostic_sha256']):
                raise ValueError('冻结证券准入状态变化：'+ticker)
            tickers.add(ticker)
            if row['http_status'] == 200:
                body = row['response']
                if (body.get('ticker') != ticker or body['inputs'].get('ticker') != ticker
                        or body.get('reference_snapshot', {}).get('id') != protocol['reference_snapshot_id']
                        or value_digest(body['inputs']) != frozen['inputs_sha256']):
                    raise ValueError('冻结证券输入、身份或参考冲突：'+ticker)
            yield row
    if members != set(expected) or tickers != set(companies):
        raise ValueError('计算归档缺少冻结证券或证据成员')


def freeze(protocol_file, profile_inventory, questions_file, filing_inventory, archive_file=None, publication_file=None,
           research_context=None):
    """绑定已发布全范围及已验收原生条件；不选择或更新预测参数。"""
    from data_sources.us_cn_hk_db import input_contexts, publication_scope, snapshot_metadata
    from tools.publish_native_valuation import runtime_identity
    protocol_file = Path(protocol_file)
    if protocol_file.exists():
        raise ValueError('冻结协议已存在，不覆盖；新研究须使用新目录')
    database = workspace_path('valuation.sqlite')
    source = workspace_path('alphalake.duckdb')
    publication_file = Path(publication_file) if publication_file else workspace_path('derived/system-delivery/valuation.sqlite.delivery.json')
    receipt_hash = digest(publication_file)
    publication = json.loads(publication_file.read_text())
    sqlite_hash, source_hash = digest(database), digest(source)
    if publication.get('contract') != 'alphalake-native-delivery-v2' or publication['published_sha256'] != sqlite_hash:
        raise ValueError('当前SQLite未绑定合格发布收据')
    runtime = runtime_identity(workspace_path())
    for stage in ('calculation', 'review'):
        if runtime['stages'][stage] != publication['runtime_identity']['stages'][stage]:
            raise ValueError('发布后的计算或审阅依赖已变化，须先验收新版本')
    checked = {row['ticker']: {key: row[key] for key in ('admission', 'financial_admission', 'http_status',
               'missing_fields', 'diagnostic_sha256', 'inputs_sha256')} for row in publication['check']['results']}
    if len(checked) != publication['check']['companies'] or publication['check']['sha256'] != sqlite_hash:
        raise ValueError('发布验收范围重复或快照不一致')
    del publication
    if archive_file is None:
        for candidate in sorted(workspace_path('derived/system-delivery').glob('run-*/candidate-check.archive.json'), reverse=True):
            run = candidate.parent/'run.json'
            if run.exists():
                record = json.loads(run.read_text())
                if record.get('published_sha256') == sqlite_hash and record.get('candidate', {}).get('sha256') == sqlite_hash:
                    archive_file = candidate.with_name('candidate-check.tar.gz')
                    break
        if archive_file is None:
            raise ValueError('未找到当前快照验收归档，请显式指定--archive')
    archive_file = Path(archive_file)
    manifest_file = archive_file.with_name(archive_file.name.removesuffix('.tar.gz')+'.archive.json')
    manifest = json.loads(manifest_file.read_text())
    if digest(archive_file) != manifest['sha256']:
        raise ValueError('发布验收归档哈希不一致')
    previous = {}
    for path in workspace_path('research').glob('*/research.json'):
        report = json.loads(path.read_text())
        if report.get('kind') == 'initial_coverage' and len(report.get('tickers', [])) == 1:
            ticker = report['tickers'][0]
            order = lambda item:(item.get('research_date') or '',item['published_at'],item['id'])
            if ticker not in previous or order(report) > order(previous[ticker]):
                previous[ticker] = report
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as connection:
        metadata = snapshot_metadata(connection)
        scope = publication_scope(connection)
        if metadata['source_database_sha256'] != source_hash:
            from tools.export_alphalake_sqlite import query_standard_inputs
            query_standard_inputs(connection, {'revenue_cumulative':[metadata['report_period']]}, source,
                                  tickers=[scope[0]['ticker']])
        contexts = {row['ticker']: row for row in input_contexts(connection)}
        prices = dict(connection.execute('SELECT ticker,stock_price_listing FROM companies'))
        if set(checked) != {row['ticker'] for row in scope}:
            raise ValueError('发布验收没有保留完整规范证券范围')
        companies = []
        for row in scope:
            ticker = row['ticker']
            context, result = contexts[ticker], checked[ticker]
            companies.append(row | result | dict(industry=context['model_scope'].get('industry'),
                quote_date=(context['market'].get('quote') or {}).get('trade_date'),
                stock_price_listing=prices[ticker],
                reason=result['admission'], previous_id=previous.get(ticker, {}).get('id')))
    now = datetime.now(timezone.utc).isoformat()
    sources = {name:workspace_file(path) for name, path in (
        ('profile_inventory', profile_inventory), ('questions_file', questions_file), ('filing_inventory', filing_inventory))}
    protected = {workspace_file(path):digest(path) for path in (database, source, publication_file, manifest_file,
        Path(profile_inventory), Path(questions_file), Path(filing_inventory))}
    guidance_file = protocol_file.parent/'guidance.json'
    if guidance_file.exists():
        sources['guidance_file'] = workspace_file(guidance_file)
        protected[sources['guidance_file']] = digest(guidance_file)
    context = {}
    if research_context is not None:
        context_path = Path(research_context)
        context = json.loads(context_path.read_text())
        if (not isinstance(context,dict) or context.get('contract') != 'initial-coverage-research-context-v1'
                or set(context)-{'contract','boundary','extra_source_candidates','company_context'}
                or not isinstance(context.get('extra_source_candidates',[]),list)
                or not isinstance(context.get('company_context',{}),dict)):
            raise ValueError('研究补充上下文只允许额外源候选与逐公司解释，不接受估值输入')
        protected[workspace_file(context_path)] = digest(context_path)
        context = {key:context[key] for key in ('extra_source_candidates','company_context') if key in context}
        for candidate in context.get('extra_source_candidates', []):
            if (not isinstance(candidate,dict) or set(candidate) != {'ticker','name','instrument_id','report_id','status','reason','evidence'}
                    or candidate['ticker'] in checked or not re.fullmatch(r'(?:SHSE|SZSE):[0-9]{6}',candidate['ticker'])
                    or type(candidate['instrument_id']) is not int or candidate['instrument_id'] <= 0
                    or candidate['status'] != 'pending_snapshot_publication' or not candidate['evidence']):
                raise ValueError('额外源候选只作已解析未发布说明，不得加入估值范围或夹带输入')
            for evidence in candidate['evidence']:
                path = workspace_path(evidence['path'])
                if not evidence.get('locator') or digest(path) != evidence['sha256']:
                    raise ValueError('额外源候选证据不一致')
                protected[workspace_file(path)] = evidence['sha256']
        for ticker, item in context.get('company_context',{}).items():
            if ticker not in checked or set(item) != {'price_comparison_status','reason','evidence'}:
                raise ValueError('公司补充上下文证券或字段不合格')
            evidence = item['evidence']
            evidence_path = workspace_path(evidence['path'])
            if (not item['price_comparison_status'] or not item['reason'] or not evidence.get('locator')
                    or digest(evidence_path) != evidence['sha256']):
                raise ValueError('公司补充上下文缺少可核对证据')
            protected[workspace_file(evidence_path)] = evidence['sha256']
    protocol = dict(contract=PROTOCOL, frozen_at=now, target=len(companies), source_universe=len(companies),
        catalogue_id='initial-coverage-all-'+now[:10].replace('-', ''), companies=companies,
        sqlite_sha256=sqlite_hash, source_database_sha256=source_hash,
        financial_report_period=metadata['report_period'], information_as_of=metadata['information_as_of'],
        reference_snapshot_id=metadata['reference_snapshot_id'], runtime=runtime,
        publication_receipt=workspace_file(publication_file), publication_receipt_sha256=receipt_hash,
        calculation_archive=workspace_file(archive_file), calculation_archive_sha256=manifest['sha256'],
        calculation_archive_manifest=workspace_file(manifest_file), protected=protected, **sources, **context,
        assumption_rule='全部证券沿同版已发布原生条件；旧专项只关联，不混接旧请求或替换当前默认。',
        stopping_rule='完整已发布范围逐证券交付；缺项、金融范围外及分类待定保留，不填零、不另建模型。')
    verify_inputs(protocol, runtime=True)
    save(protocol_file, protocol)
    return dict(status='frozen', requested=len(companies), ready=sum(row['admission']=='ready' for row in companies),
                protocol=str(protocol_file), catalogue_id=protocol['catalogue_id'])


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
    protocol = load_protocol(protocol_file)
    verify_inputs(protocol)
    cache = workspace_path('eastmoney', 'business-analysis')
    cache.mkdir(parents=True, exist_ok=True)
    index = cache/'index.json'
    known = json.loads(index.read_text())['records'] if index.exists() else {}
    results = [dict(ticker=row['ticker'], status='not_requested', reason=row['admission'])
               for row in protocol['companies'] if row['admission'] != 'ready']
    selected = [row for row in protocol['companies'] if row['admission'] == 'ready']
    with ThreadPoolExecutor(max_workers=4) as executor:
        tasks = [executor.submit(collect_one, row, cache, known, protocol['financial_report_period']) for row in selected]
        for task in as_completed(tasks):
            result = task.result()
            results.append(result)
            if len(results) % 20 == 0:
                print(json.dumps(dict(collected=sum(r['status']=='collected' for r in results), total=len(tasks),
                    failed=sum(r['status']=='source_failed' for r in results))), flush=True)
    records = {r['ticker']:r for r in results if r['status']=='collected'}
    receipt = dict(contract='initial-coverage-business-collection-v1', protocol_sha256=digest(protocol_file),
        acquired_at=datetime.now(timezone.utc).isoformat(), requested=len(tasks), collected=len(records),
        failures=[r for r in results if r['status']=='source_failed'],
        states=[r for r in results if r['status']=='not_requested'], records=records,
        boundary='本批仅取得公司经营汇编，不认证全文语义、公司预测或财务事实；失败公司保留原名单')
    save(protocol_file.parent/'business-collection.json', receipt)
    save(index, dict(contract='company-business-cache-v1', records=known|records))
    verify_inputs(protocol)
    return {k:v for k,v in receipt.items() if k!='records'}


def collect_facts(protocol_file):
    from decimal import Decimal
    from tools.export_alphalake_sqlite import FIELDS, record_facts
    protocol = load_protocol(protocol_file)
    verify_inputs(protocol)
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
            scope = pending/'scope.json'
            save(scope, [{key:row[key] for key in ('instrument_id','ticker','name')} for row in protocol['companies']])
            subprocess.run([str(binary),'export-financial-snapshot',str(source),'--output',str(destination),
                '--scope-file',str(scope),'--fields',','.join(sorted(fields)),
                '--field-periods',str(spec),'--from',periods[0],'--period',end.isoformat(),
                '--as-of',protocol['information_as_of']],check=True,timeout=600,capture_output=True)
            definitions = [json.loads(line) for line in (destination/'fields.jsonl').read_text().splitlines()]
            conflicts = [json.loads(line) for line in (destination/'conflicts.jsonl').read_text().splitlines()]
            if conflicts:
                save(protocol_file.parent/'standard-source-conflicts.json',conflicts)
                raise ValueError('本研究范围有标准来源冲突')
            ticker_by_code = {t[5:]:t for t in tickers}
            identity = {row['ticker']:row['instrument_id'] for row in protocol['companies']}
            grouped = {t:{p:dict(values_cny={},source_record=None) for p in periods} for t in tickers}
            source_rows = 0
            with (destination/'records.jsonl').open() as stream:
                for fact in record_facts(stream,definitions):
                    ticker, period, field = ticker_by_code.get(fact['code']), fact['period'], fact['field']
                    if ticker not in grouped or period not in periods or field not in fields:
                        raise ValueError('标准来源返回未请求证券、期间或字段')
                    expected = 'instant' if fields[field]=='instant' else {3:'Q1',6:'H1',9:'9M',12:'FY'}[date.fromisoformat(period).month]
                    if (fact['instrument_id'] != identity[ticker]
                            or (fact['unit'],fact['period_type'],fact['statement_scope']) != ('CNY',expected,'provider_default')
                            or not Decimal(fact['value']).is_finite()):
                        raise ValueError('不兼容的经营事实身份/单位/期间/范围')
                    entry = grouped[ticker][period]
                    if field in entry['values_cny']:
                        raise ValueError('重复经营事实')
                    record = {k:fact[k] for k in ('instrument_id','source_record_id','artifact_sha256','available_at','announcement_source','source_observed_at')}
                    if entry['source_record'] is not None and entry['source_record'] != record:
                        raise ValueError('同报告期出现冲突的来源记录')
                    entry['source_record'] = record
                    entry['values_cny'][field] = fact['value']
                    source_rows += 1
        marks = ','.join('?' for _ in tickers)
        comparisons, known, missing, mismatches = 0, 0, 0, []
        columns = ('revenues','ebit','net_income','capex','r_and_d_expense','bv_equity')
        query = f'SELECT ticker,fy_offset,{",".join(columns)} FROM financials_annual WHERE ticker IN ({marks}) AND fy_offset BETWEEN 0 AND 4'
        for row in connection.execute(query, tickers):
            ticker, period = row['ticker'], f"{base_year-row['fy_offset']}-12-31"
            for key in columns:
                value = grouped[ticker][period]['values_cny'].get(FIELDS[key][0])
                expected = float(Decimal(value)/Decimal(1000000)) if value is not None else None
                if expected != row[key]:
                    mismatches.append(dict(ticker=ticker,period=period,field=key,source=expected,sqlite=row[key]))
                comparisons += 1
                known += row[key] is not None
                missing += row[key] is None
        if mismatches:
            save(protocol_file.parent/'financial-differences.json',mismatches)
            raise ValueError('标准来源与冻结SQLite年度输入存在差异')
    if digest(database) != protocol['sqlite_sha256'] or digest(source) != protocol['source_database_sha256']:
        raise ValueError('取证期间数据库变化')
    verify_inputs(protocol)
    data_file = protocol_file.parent/'financial-facts.jsonl'
    pending = data_file.with_suffix('.jsonl.tmp')
    offsets = {}
    with pending.open('wb') as stream:
        for ticker in tickers:
            raw = (json.dumps(dict(ticker=ticker,source_periods=grouped.pop(ticker)),
                              ensure_ascii=False,allow_nan=False,sort_keys=True)+'\n').encode()
            offsets[ticker] = [stream.tell(),len(raw)]
            stream.write(raw)
    os.replace(pending,data_file)
    save(protocol_file.parent/'financial-facts.json',dict(contract='initial-coverage-standard-facts-v2',
        source_database_sha256=protocol['source_database_sha256'],sqlite_sha256=protocol['sqlite_sha256'],
        report_end=end.isoformat(),periods=periods,fields=fields,companies_count=len(tickers),
        company_facts_file=workspace_file(data_file),company_facts_sha256=digest(data_file),company_offsets=offsets,
        source_rows=source_rows,native_comparisons=comparisons,differences=[],
        research_information_as_of=protocol['frozen_at'],valuation_information_as_of=protocol['information_as_of'],
        frozen_snapshot_global_equivalence=False,selected_annual_values_verified=True,
        boundary='当前标准源研究查询，单位元；与冻结SQLite按本范围年度重叠金额核对，不认证旧快照全范围相同。额外现金/周转事实是本次研究资料，不回写冻结估值；共享血缘按报告期保存，未知仍为空。'))
    save(protocol_file.parent/'financial-comparison-summary.json', dict(annual_compared_cells=comparisons,
        known_amounts_matched=known,missing_both=missing,differences=0))
    return dict(status='verified',companies=len(tickers),facts=source_rows,native_comparisons=comparisons)


def report_folder(ticker, protocol):
    return workspace_path('research', 'initial-'+('sh-' if ticker.startswith('SHSE:') else 'sz-')+ticker[5:]+'-'+protocol['frozen_at'][:10].replace('-',''))


def calculate(protocol_file, tickers=None):
    """重放同版发布的完整请求，不继承旧专项预测或生成新参数。"""
    os.environ['US_CN_HK_DB_PATH'] = str(workspace_path('valuation.sqlite'))
    from fastapi.testclient import TestClient
    from api.main import app
    from tools.evaluate_native_policy import calculate_request
    from tools.check_native_sqlite import value_digest
    protocol = load_protocol(protocol_file)
    runtime_full = verify_inputs(protocol, runtime=True)
    runtime = {key:runtime_full[key] for key in ('versions','stages')}
    selected = {r['ticker'] for r in protocol['companies']}
    if tickers:
        if not set(tickers).issubset(selected):
            raise ValueError('试运行证券必须在冻结名单内')
        selected = set(tickers)
    with sqlite3.connect(workspace_path('valuation.sqlite').as_uri()+'?mode=ro',uri=True) as c:
        metadata = dict(c.execute("SELECT key,value FROM metadata WHERE key NOT LIKE 'input_context:%'"))
        from data_sources.us_cn_hk_db import publication_scope
        identities = {row['ticker']:row['instrument_id'] for row in publication_scope(c)}
    if identities != {row['ticker']:row['instrument_id'] for row in protocol['companies']}:
        raise ValueError('冻结规范证券身份变化')
    counts, failures, used = {}, [], set()
    protocol_hash = digest(protocol_file)
    with TestClient(app,raise_server_exceptions=False) as client:
        for original in archive_rows(protocol):
            ticker = original['ticker']
            if ticker not in selected:
                continue
            if ticker in used:
                raise ValueError('冻结请求有重复证券')
            used.add(ticker)
            folder = report_folder(ticker,protocol)
            output = folder/'valuation.json'
            ready = original['admission'] == 'ready'
            if ready != (original['http_status'] == 200):
                raise ValueError('归档准入与实际HTTP状态不一致：'+ticker)
            request = dict(inputs=original['response']['inputs']) if ready else None
            if output.exists():
                previous = json.loads(output.read_text())
                if (previous['protocol_sha256'] != protocol_hash or previous.get('request') != request
                        or previous['admission'] != original['admission']
                        or any(previous['runtime']['stages'][stage] != runtime['stages'][stage]
                               for stage in ('calculation','review'))
                        or (ready and (not previous.get('api_verified')
                            or previous['response']['dcf'] != original['response']['dcf']
                            or previous['response']['final'] != original['response']['final']))
                        or (not ready and (previous['response'] != original['response']
                            or previous['diagnostic'] != original['diagnostic']))):
                    raise ValueError('已有公司计算与冻结输入冲突')
                counts['reused_verified'] = counts.get('reused_verified',0)+1
                continue
            if not ready:
                result = dict(contract='initial-coverage-calculation-v2', protocol_sha256=protocol_hash,
                    ticker=ticker,status=original['admission'],admission=original['admission'],
                    financial_admission=original['financial_admission'], diagnostic=original['diagnostic'],
                    response=original['response'], http_status=original['http_status'], request=None,
                    reason=original['response'].get('detail'), runtime=runtime, sensitivities=[],
                    financial_input_snapshot=protocol['sqlite_sha256'], api_verified=False, live_verified=False,
                    source_archive=protocol['calculation_archive'],source_archive_sha256=protocol['calculation_archive_sha256'])
                result['run_id'] = value_digest(dict(ticker=ticker,diagnostic=result['diagnostic'],
                    snapshot=protocol['sqlite_sha256'],protocol_sha256=protocol_hash))
                save(output,result)
                counts[original['admission']] = counts.get(original['admission'],0)+1
                continue
            body, review = calculate_request(client,request,metadata,source_context=original['response'])
            if body is None:
                failures.append(dict(ticker=ticker,phase='baseline',detail=review))
                continue
            if (body['dcf']!=original['response']['dcf'] or body['final']!=original['response']['final']
                    or review['economic_checks']['cashflow_bridge']['status']!='arithmetic_reconciled'):
                raise ValueError('原冻结请求重放值或现金流闭合发生变化：'+ticker)
            folder.mkdir(parents=True,exist_ok=True)
            result = dict(contract='initial-coverage-calculation-v2',protocol_sha256=protocol_hash,
                ticker=ticker,mode='native_conditions_with_company_story',status='calculated',admission='ready',
                request=request,response=body,review=review,
                source_archive=protocol['calculation_archive'],source_archive_sha256=protocol['calculation_archive_sha256'],
                runtime=runtime,sensitivities=[],api_verified=True,live_verified=False,
                financial_input_snapshot=protocol['sqlite_sha256'],model_parameters_changed=False)
            result['run_id'] = value_digest(dict(ticker=ticker,request=request,runtime=runtime,protocol_sha256=result['protocol_sha256']))
            save(output,result)
            counts[result['mode']] = counts.get(result['mode'],0)+1
            if len(used)%20==0:
                print(json.dumps(dict(calculated_or_verified=len(used),total=len(selected),failures=len(failures))),flush=True)
    if used != selected:
        raise ValueError('冻结请求归档缺证券')
    verify_inputs(protocol,runtime=True)
    summary = dict(contract='initial-coverage-calculation-summary-v2',requested=len(selected),processed=len(used),
        modes=counts,failures=failures,all_ready_api_verified=not failures,all_live_verified=False,
        model_changed=False,new_defaults=False)
    save(protocol_file.parent/('pilot-calculation.json' if tickers else 'calculation-summary.json'),summary)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command', required=True)
    frozen=sub.add_parser('freeze')
    frozen.add_argument('--protocol', type=Path, required=True)
    frozen.add_argument('--profile-inventory', type=Path, required=True)
    frozen.add_argument('--questions-file', type=Path, required=True)
    frozen.add_argument('--filing-inventory', type=Path, required=True)
    frozen.add_argument('--archive', type=Path)
    frozen.add_argument('--publication', type=Path)
    frozen.add_argument('--research-context', type=Path)
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
    if args.command=='freeze':
        print(json.dumps(freeze(args.protocol,args.profile_inventory,args.questions_file,args.filing_inventory,
                                args.archive,args.publication,args.research_context), ensure_ascii=False))
    elif args.command=='collect':
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
