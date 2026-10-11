"""按已留存申万细分行业识别沪深300周期暴露，复用原生API重估并保留拒绝。"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from decimal import Decimal
import html
import io
import json
import math
from pathlib import Path
import resource
import sqlite3
import tarfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tools.check_native_sqlite import value_digest
from tools.cyclical_valuation import METHOD_SOURCE, POLICY, cycle_conditions, economic_review, load_scope
from tools.publish_native_valuation import digest, runtime_identity, save
from tools.review_native_policy import review_report


# 这是本轮获授权的业务识别政策，不是来源分类事实或达摩达兰发布的行业表。
CYCLE_GROUPS = {
    '资源价格': ('动力煤', '铜', '铝', '钨钼', '黄金', '稀土', '其他金属', '钾肥', '锂', '钴', '油气开采'),
    '材料价差': ('板材', '特钢', '涤纶', '有机化工原料', '氟化工', '聚氨酯', '煤化工', '有机硅',
                 '玻纤制造', '水泥制造', '电池化学品', '炼油化工'),
    '供需产能': ('面板', '光伏硅料', '光伏电池组件', '半导体材料', '半导体制造', '半导体封测', '功率半导体'),
    '生物供给': ('生猪养殖',),
    '运力运价': ('航运', '航空运输'),
    '经济需求': ('综合乘用车', '电动乘用车', '商用载客车', '工程机械整机', '工程机械零件', '住宅开发'),
    '投资订单': ('半导体设备', '能源及重型设备'),
}
LEAF_GROUP = {leaf: group for group, leaves in CYCLE_GROUPS.items() for leaf in leaves}
DISPLAY_CONDITION = 'arithmetic-3y'


def classify(company, override=None):
    leaves = {r['node_name'] for r in company['industry_evidence']
              if r['taxonomy_code'] == 'tdx_shenwan_industry'}
    leaf = next(iter(leaves)) if len(leaves) == 1 else None
    group = LEAF_GROUP.get(leaf)
    basis = '申万细分行业＋经济机制判断（政策，非来源事实）'
    if company['scope'] == 'outside_financial_scope':
        if override:
            raise ValueError('financial scope cannot be replaced by cycle review')
        return dict(category='金融范围外', group=None, industry=leaf, basis='既有金融范围审核')
    if override:
        source = override.get('source', {})
        if (override.get('ticker') != company['ticker'] or not override.get('group')
                or not override.get('business_basis') or override.get('expected_industry') != leaf
                or not source.get('sha256') or not source.get('path') or not source.get('url')
                or not source.get('pages') or any(not isinstance(p, int) or p < 1 for p in source['pages'])):
            raise ValueError('cycle business review requires matching identity, industry and document locator')
        group, basis = override['group'], override['business_basis']
    return dict(category='周期暴露' if group else '未采用周期方法' if leaf else '分类待核实',
                group=group, industry=leaf, basis=basis, business_review=override,
                cycle_position_verified=False, full_cycle_verified=False)


def comparison(value, price):
    if value is None or price is None:
        return None, None
    v, p = Decimal(str(value)), Decimal(str(price))
    if not v.is_finite() or not p.is_finite() or p <= 0:
        raise ValueError('finite value and positive comparison price required')
    return float(v-p), float((v/p-1)*100)


def display_value(row):
    """固定展示条件失败时留空；不能静默用原默认或其他成功情景填回。"""
    if row['classification']['category'] == '周期暴露':
        item = row['variants'].get(DISPLAY_CONDITION, {})
        return item.get('value') if item.get('status') == 'calculated_explicit_condition' else None
    return row.get('baseline_value')


def check_cashflows(body):
    d = body['dcf']
    decimal = lambda x: Decimal(str(x))
    close = lambda a, b: math.isclose(float(a), float(b), rel_tol=1e-11, abs_tol=1e-6)
    for fcff, nopat, investment, factor, pv in zip(d['fcff_projections'], d['nopat_projections'],
            d['reinvestment_projections'], d['discount_factors'], d['pv_fcff'], strict=True):
        if not close(decimal(nopat)-decimal(investment), fcff) or not close(decimal(fcff)*decimal(factor), pv):
            raise ValueError('independent FCFF or discount aggregation differs')
    terminal = decimal(d['terminal_fcff'])/(decimal(d['terminal_wacc'])-decimal(d['terminal_growth']))
    terminal += decimal(d['terminal_tax_transition_value'])
    if not close(terminal, d['terminal_value_firm']) or not close(
            terminal*decimal(d['discount_factors'][-1]), d['pv_terminal_value']):
        raise ValueError('independent terminal aggregation differs')
    if not close(sum(map(decimal, d['pv_fcff']))+decimal(d['pv_terminal_value']), d['value_of_operating_assets']):
        raise ValueError('independent operating value differs')


def write_tables(output, rows, summary):
    from openpyxl import Workbook

    headers = ('代码', '名称', '分类', '机制', '申万细分', '状态', '收盘价', '报价日期', '原默认条件值',
               '重估条件值', '价差元', '价差百分比', '算术1年', '算术5年', '收入加权3年', '说明')
    cells = []
    for r in rows:
        cells.append([r['code'], r['name'], r['classification']['category'], r['classification']['group'],
            r['classification']['industry'], r['status'], r['price'], r['price_date'], r['baseline_value'],
            r['value'], r['difference_yuan'], r['difference_pct'],
            *[r['variants'].get(k, {}).get('value') for k in ('arithmetic-1y', 'arithmetic-5y', 'revenue_weighted-3y')],
            r['reason']])
    with (output/'results.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream); writer.writerow(headers); writer.writerows(cells)
    workbook = Workbook(); sheet = workbook.active; sheet.title = '沪深300'
    sheet.append(headers)
    for row in cells:
        sheet.append(row)
    sheet.freeze_panes = 'C2'; sheet.auto_filter.ref = sheet.dimensions
    for column in range(7, 16):
        if column == 8:
            continue
        for cell in list(sheet.columns)[column-1][1:]:
            cell.number_format = '0.00'
    notes = workbook.create_sheet('说明')
    for key, value in summary.items():
        if key != 'results':
            notes.append([key, json.dumps(value, ensure_ascii=False)])
    workbook.save(output/'results.xlsx')
    title = '# 沪深300周期识别及条件重估\n\n'
    title += ('单位：元／股；价差为条件值／收盘价−1。周期股展示固定三年算术过渡条件，其他企业保留原默认；'
              '拒绝不回退。四种周期条件无概率、无自动胜出者；五年观察窗、正常收入和资本释放尚未经济认证。\n\n')
    formatted = [[f'{v:.2f}' if isinstance(v, float) else str(v) if v is not None else '—' for v in row] for row in cells]
    (output/'results.md').write_text(title+'| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+
        ''.join('| '+' | '.join(v.replace('|', '/') for v in row)+' |\n' for row in formatted))
    table = '<tr>'+''.join('<th>'+html.escape(h)+'</th>' for h in headers)+'</tr>'
    table += ''.join('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in row)+'</tr>' for row in formatted)
    (output/'results.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>沪深300周期识别及重估</title><style>body{font-family:sans-serif;margin:20px}table{border-collapse:collapse;white-space:nowrap}th,td{padding:8px;border-bottom:1px solid #ddd}th{background:#eee;position:sticky;top:0}.scroll{overflow:auto}input{padding:8px;width:280px}</style>'
        '<h1>沪深300周期识别及条件重估</h1><p>周期股：三年算术过渡条件；其余：原默认。单位元／股，价差单位%。这是条件比较，不是目标价。完整边界见同目录说明。</p>'
        '<label>搜索代码、名称、分类 <input id="search" type="search"></label><p id="count">300行</p><div class="scroll"><table>'+table+'</table></div>'
        '<script>const rows=[...document.querySelectorAll("table tr")].slice(1);const count=document.querySelector("#count");const search=document.querySelector("#search");count.textContent=rows.length+"行";search.addEventListener("input",()=>{let n=0;for(const r of rows){r.hidden=!r.textContent.toLowerCase().includes(search.value.toLowerCase());if(!r.hidden)n++;}count.textContent=n+"行";});</script></html>')


def run(database, source_database, constituents_file, scope_file, overrides_file, output, web, quote_date, tickers=None):
    from data_sources.paths import workspace_path

    started = time.monotonic()
    database, source_database = database.resolve(strict=True), source_database.resolve(strict=True)
    if output.exists() or not output.resolve().is_relative_to(workspace_path('derived').resolve()):
        raise ValueError('new output under workspace/derived required')
    constituents = json.loads(constituents_file.read_text())
    members = {r['ticker']: r for r in constituents}
    if len(constituents) != 300 or len(members) != 300 or any(
            t != ('SHSE:' if r['code'][0] == '6' else 'SZSE:')+r['code'] for t, r in members.items()):
        raise ValueError('complete, unique SH/SZ CSI300 constituents required')
    selected = sorted(members) if tickers is None else sorted(tickers)
    if len(selected) != len(set(selected)) or set(selected)-set(members):
        raise ValueError('unique tickers in frozen constituents required')
    inputs = [database, source_database, constituents_file, scope_file, overrides_file]
    hashes = {str(p): digest(p) for p in inputs}
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as conn:
        metadata = dict(conn.execute("SELECT key,value FROM metadata WHERE key < 'input_context:' OR key >= 'input_context;'"))
        published = [r[0] for r in conn.execute('SELECT ticker FROM companies ORDER BY ticker')]
        if metadata.get('contract') != 'alphalake-sqlite-v12' or metadata['source_database_sha256'] != hashes[str(source_database)]:
            raise ValueError('current canonical source and SQLite contract required')
        companies = {r[0]: r[1] for r in conn.execute('SELECT ticker,stock_price_listing FROM companies') if r[0] in members}
        from data_sources.us_cn_hk_db import input_contexts
        quotes = {r['ticker'].split(':')[-1]:r['market'].get('quote') or {} for r in input_contexts(conn)
                  if r['ticker'] in members}
        if set(members) != set(companies):
            raise ValueError('constituents missing from publication')
    load_scope(scope_file, hashes[str(database)], metadata, published)
    coverage = json.loads(scope_file.read_text())
    scopes = {r['ticker']: r for r in coverage['companies'] if r['ticker'] in members}
    del coverage
    packet = json.loads(overrides_file.read_text())
    if packet.get('contract') != 'explicit-cycle-business-review-v1' or not packet.get('approval'):
        raise ValueError('explicit cycle business review contract required')
    overrides = {r['ticker']: r for r in packet['reviews']}
    if len(overrides) != len(packet['reviews']) or set(overrides)-set(members):
        raise ValueError('unique business reviews within constituents required')
    for override in overrides.values():
        path = Path(override['source']['path'])
        if digest(path) != override['source']['sha256']:
            raise ValueError('business evidence hash mismatch')
        hashes[str(path)] = digest(path)
    classifications = {t: classify(scopes[t], overrides.get(t)) for t in members}
    output.mkdir(parents=True)
    save(output/'classification.json', [dict(**members[t], **classifications[t], industry_evidence=scopes[t]['industry_evidence']) for t in sorted(members)])
    protocol = dict(contract='csi300-cycle-conditional-valuation-v1', frozen_at=datetime.now(timezone.utc).isoformat(),
        source_hashes=hashes, metadata=metadata, runtime=runtime_identity(database.parent),
        implementation_sha256=digest(Path(__file__)),
        constituents_count=300, evaluated_scope=selected, cycle_groups=CYCLE_GROUPS,
        classification_counts=dict(Counter(v['category'] for v in classifications.values())),
        group_counts=dict(Counter(v['group'] for v in classifications.values() if v['group'])),
        classification_sha256=digest(output/'classification.json'), method_source=METHOD_SOURCE,
        display_condition=DISPLAY_CONDITION, condition_policy=POLICY, quote_date=quote_date,
        engine_defaults_changed=False, automatic_adoption=False, selected_scenario=None)
    save(output/'protocol.json', protocol)

    def request(path, body=None, method='POST'):
        req = Request(web.rstrip('/')+path, data=json.dumps(body, allow_nan=False).encode() if body is not None else None,
            headers={'Content-Type':'application/json'}, method=method)
        try:
            with urlopen(req, timeout=60) as response:
                return response.status, json.load(response) if response.status != 204 else None
        except HTTPError as error:
            if error.code != 422:
                raise
            return error.code, json.load(error)

    rows, files = [], {}
    with tarfile.open(output/'api-evidence.tar.gz', 'x:gz') as archive:
        def record(name, body):
            content = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            member = tarfile.TarInfo(name); member.size = len(content)
            archive.addfile(member, io.BytesIO(content)); files[name] = value_digest(body)
        for ticker in selected:
            member = members[ticker]; classification = classifications[ticker]
            quote = quotes[member['code']]
            price = float(quote['close']) if quote else None
            if quote and (quote['trade_date'] != quote_date or quote['adjustment'] != 'unadjusted' or price != companies[ticker]):
                raise ValueError('completed close mismatch: '+ticker)
            row = dict(**member, classification=classification, price=price, price_date=quote.get('trade_date'),
                       baseline_value=None, variants={}, reason='', status='金融范围外', value=None)
            rows.append(row)
            if classification['category'] != '金融范围外':
                code, body = request('/api/valuation/from-database', dict(ticker=ticker))
                record(member['code']+'-baseline.json', dict(http_status=code, response=body))
                if code != 200:
                    row.update(status='原生输入受阻', reason=json.dumps(body.get('detail'), ensure_ascii=False))
                else:
                    session = body['id']
                    try:
                        if body['ticker'] != ticker or body['ltm_financials']['stock_price'] != price:
                            raise ValueError('native identity or price mismatch')
                        prepared = body['inputs'].get('prepared_ttm') or {}
                        if (prepared.get('period_end') != metadata['report_period']
                                or prepared.get('provenance', {}).get('source_database_sha256') != metadata['source_database_sha256']
                                or datetime.fromisoformat(prepared.get('information_as_of', '')) != datetime.fromisoformat(metadata['information_as_of'])
                                or body['reference_snapshot']['id'] != metadata['reference_snapshot_id']):
                            raise ValueError('live service source, period, cutoff or reference differs from frozen input')
                        review_report(body); check_cashflows(body)
                        row.update(baseline_value=body['final']['value_per_share'], status='原默认条件')
                        if classification['category'] == '周期暴露':
                            try:
                                variants, evidence = cycle_conditions(body)
                            except ValueError as error:
                                variants, evidence = {}, None
                                row['reason'] = str(error)
                            if evidence is not None:
                                record(member['code']+'-condition-basis.json', evidence)
                                for name, variant in variants.items():
                                    item = {k:v for k,v in variant.items() if k != 'request'}
                                    row['variants'][name] = item
                                    if variant['status'] != 'prepared_explicit_condition':
                                        continue
                                    code, actual = request('/api/valuation', variant['request'])
                                    record(member['code']+'-'+name+'.json', dict(request=variant['request'], http_status=code, response=actual))
                                    if code != 200:
                                        item.update(status='condition_rejected', reason=json.dumps(actual.get('detail'), ensure_ascii=False))
                                        continue
                                    try:
                                        if actual['reference_snapshot']['id'] != body['reference_snapshot']['id']:
                                            raise ValueError('reference version changed during condition')
                                        review = economic_review(actual); check_cashflows(actual)
                                        item.update(status='calculated_explicit_condition', value=actual['final']['value_per_share'],
                                            nonresearch_release_years=len(review['nonresearch_capital_release_years']),
                                            terminal_nonresearch_release=bool((review['terminal_research_capital'] or {}).get('positive_growth_with_nonresearch_release')),
                                            economic_forecast_verified=False)
                                    finally:
                                        if request('/api/valuation/'+actual['id'], method='DELETE')[0] != 204:
                                            raise ValueError('condition session release failed')
                            primary = row['variants'].get(DISPLAY_CONDITION, {})
                            row['status'] = '周期显式条件' if primary.get('status') == 'calculated_explicit_condition' else '周期条件待补依据'
                            row['reason'] = row['reason'] or primary.get('reason', '')
                            if primary.get('nonresearch_release_years'):
                                row['reason'] += '; 预测含未证实非研发资本释放'
                            if primary.get('terminal_nonresearch_release'):
                                row['reason'] += '; 终值含未证实非研发资本释放'
                    finally:
                        if request('/api/valuation/'+session, method='DELETE')[0] != 204:
                            raise ValueError('baseline session release failed')
            row['value'] = display_value(row)
            row['difference_yuan'], row['difference_pct'] = comparison(row['value'], row['price'])
            if len(rows) % 25 == 0:
                print(json.dumps(dict(processed=len(rows), total=len(selected), statuses=dict(Counter(r['status'] for r in rows))), ensure_ascii=False), flush=True)
    # 每个归档成员重开核对；只保留当前成员，避免积累整个批次报告。
    with tarfile.open(output/'api-evidence.tar.gz', 'r|gz') as archive:
        verified = {}
        for member in archive:
            verified[member.name] = value_digest(json.load(archive.extractfile(member)))
        if verified != files:
            raise ValueError('API evidence archive mismatch')
    for path, expected in hashes.items():
        if digest(Path(path)) != expected:
            raise ValueError('source changed during batch: '+path)
    if digest(output/'classification.json') != protocol['classification_sha256']:
        raise ValueError('classification changed during batch')
    summary = dict(contract=protocol['contract'], companies=len(rows), full_constituents=len(selected) == 300,
        classification_counts=protocol['classification_counts'], group_counts=protocol['group_counts'],
        statuses=dict(Counter(r['status'] for r in rows)), baseline_calculated=sum(r['baseline_value'] is not None for r in rows),
        displayed_calculated=sum(r['value'] is not None for r in rows), condition_count=sum(v.get('status') == 'calculated_explicit_condition' for r in rows for v in r['variants'].values()),
        prices=sum(r['price'] is not None for r in rows), quote_date=quote_date, financial_period=metadata['report_period'],
        information_as_of=metadata['information_as_of'], display_condition=DISPLAY_CONDITION,
        automatic_adoption=False, selected_scenario=None, economic_forecast_verified=False,
        source_files_unchanged=True, archive_members=len(files), archive_sha256=digest(output/'api-evidence.tar.gz'),
        elapsed_seconds=time.monotonic()-started, python_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
        results=rows)
    summary['run_id'] = value_digest(summary)
    save(output/'summary.json', summary)
    write_tables(output, rows, summary)
    return {k:v for k,v in summary.items() if k != 'results'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('database', 'source-database', 'constituents', 'scope', 'business-overrides', 'output'):
        parser.add_argument('--'+option, type=Path, required=True)
    parser.add_argument('--web', required=True)
    parser.add_argument('--quote-date', required=True)
    parser.add_argument('--ticker', action='append')
    args = parser.parse_args()
    print(json.dumps(run(args.database, args.source_database, args.constituents, args.scope,
        args.business_overrides, args.output, args.web, args.quote_date, args.ticker), ensure_ascii=False, indent=2))
