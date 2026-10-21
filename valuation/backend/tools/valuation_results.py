"""从已登记冻结研究发布选股摘要；只提取结果，不重新估值。"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

from data_sources.paths import workspace_path
from data_sources.research_reports import IDENTIFIER, read_report, verified_file
from data_sources.valuation_results import (ADMISSIONS, ASSUMPTIONS, COMPARISONS, CONTRACT,
                                            FLAGS, read_json, registry, result_path)
from tools.initial_coverage_report import model_review_flags

BUILD_VERSION = 'valuation-results-projection-v1'
BASE_FIELDS = ('ticker', 'name', 'reference_industry', 'admission', 'financial_admission', 'missing_fields',
               'reason', 'report_id', 'url', 'mode', 'condition_value_cny_per_share', 'close_cny', 'close_date',
               'descriptive_difference_pct', 'price_comparison_status', 'price_comparison_reason',
               'financial_period', 'share_date', 'information_as_of', 'research_frozen_at', 'run_id')


def checked(condition, message):
    if not condition:
        raise ValueError(message)


def number(value):
    checked(value is None or (not isinstance(value, bool) and isinstance(value, (int, float))
                             and math.isfinite(value)), '摘要数值必须有限；未知使用null')
    return value


def digest_value(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def registered_json(report, identifier):
    entry, path = verified_file(report, identifier)
    value, digest = read_json(path)
    checked(digest == entry.sha256, '读取期间来源发生变化')
    return value, entry


def comparison(row):
    if row['condition_value_cny_per_share'] is None:
        return 'not_calculated', '本批次未生成每股条件价值，不比较价差'
    if row['close_cny'] is None or row['close_cny'] <= 0:
        return 'missing_price', '本批次没有可比较的合格报价'
    if row['price_comparison_status'] not in COMPARISONS:
        return 'unsupported', '不支持的来源比较状态'
    if row['price_comparison_status'] == 'unreconciled_post_period_capital_change':
        return row['price_comparison_status'], row['price_comparison_reason']
    if row['descriptive_difference_pct'] is None:
        return 'not_compared', row['price_comparison_reason']
    return row['price_comparison_status'], row['price_comparison_reason']


def project_row(row, frozen, protocol, summary):
    checked(set(BASE_FIELDS) <= row.keys(), '总览缺少必需结果字段')
    ticker = row['ticker']
    checked(row['admission'] in ADMISSIONS and row['url'] == '/research/' + row['report_id'], '结果身份或状态不合格')
    for key in ('ticker', 'name', 'admission', 'financial_admission', 'missing_fields', 'reason'):
        checked(row[key] == frozen.get(key), ticker + '：总览与冻结证券不一致：' + key)
    for key, expected in (('financial_period', protocol['financial_report_period']),
                          ('information_as_of', protocol['information_as_of']),
                          ('research_frozen_at', protocol['frozen_at']), ('close_date', frozen.get('quote_date'))):
        checked(row[key] == expected, ticker + '：总览日期不一致：' + key)
    report = read_report(row['report_id'])
    checked(report.tickers == [ticker] and report.kind == 'research_note' and report.status == 'partial',
            ticker + '：原生基线必须明确为公司研究待补资料')
    model, entry = registered_json(report, 'valuation')
    checked(model.get('contract') == 'initial-coverage-calculation-v2'
            and model.get('ticker') == ticker and model.get('admission') == row['admission']
            and model.get('protocol_sha256') == summary['protocol_sha256']
            and model.get('financial_input_snapshot') == summary['sqlite_sha256']
            and model.get('run_id') == row['run_id'], ticker + '：计算版本或来源串批')
    for key in ('calculation', 'review'):
        checked(model['runtime']['stages'][key] == protocol['runtime']['stages'][key], ticker + '：冻结实现不一致')
    assumptions = dict.fromkeys(ASSUMPTIONS)
    ready = row['admission'] == 'ready'
    if ready:
        body, review = model['response'], model['review']
        checked(model.get('api_verified') and body['ticker'] == ticker
                and body['inputs']['ticker'] == ticker and model['request']['inputs']['ticker'] == ticker
                and body['reference_snapshot']['id'] == protocol['reference_snapshot_id']
                and digest_value(model['request']['inputs']) == frozen['inputs_sha256'], ticker + '：输入或参考不一致')
        checked(row['condition_value_cny_per_share'] == body['final']['value_per_share']
                and row['close_cny'] == body['ltm_financials']['stock_price_reporting'], ticker + '：数值或报价不一致')
        forecast = review.get('forecast', [])
        first = forecast[0] if forecast else {}
        terminal = review.get('terminal', {})
        assumptions.update(dict(initial_wacc_rate=body['cost_of_capital'].get('wacc'),
            first_year_growth_rate=first.get('growth'), first_year_adjusted_margin_rate=first.get('adjusted_operating_margin'),
            first_year_funding_sales_to_capital=(first.get('capital_funding') or {}).get('sales_to_capital'),
            terminal_growth_rate=terminal.get('growth'), terminal_wacc_rate=terminal.get('wacc'),
            terminal_pv_share_rate=terminal.get('value_share')))
    else:
        checked(row['condition_value_cny_per_share'] is None and row['share_date'] is None
                and row['descriptive_difference_pct'] is None
                and row['close_cny'] == frozen.get('stock_price_listing')
                and digest_value(model['diagnostic']) == frozen['diagnostic_sha256'], ticker + '：未计算状态不一致')
    checked(row['share_date'] == (protocol['financial_report_period'] if ready else None), ticker + '：股本期不一致')
    result = {key: row[key] for key in BASE_FIELDS}
    for key in ('condition_value_cny_per_share', 'close_cny', 'descriptive_difference_pct'):
        number(result[key])
    status, reason = comparison(row)
    result.update(assumptions={key: number(value) for key, value in assumptions.items()},
        assumption_missing_reasons={key: ('本批次未生成估值' if not ready else '已保存模型未提供该项；不估计或反推')
                                    for key, value in assumptions.items() if value is None},
        review_flags=model_review_flags(model), comparison_display_status=status, comparison_display_reason=reason,
        model_source=dict(path=entry.path, sha256=entry.sha256))
    return result


def project(report_id, dataset_id, tickers=None):
    checked(re.fullmatch(IDENTIFIER, dataset_id), '摘要ID须为规范小写字母、数字和连字符')
    result_path(dataset_id)
    report = read_report(report_id)
    summary, source = registered_json(report, 'results')
    protocol, protocol_source = registered_json(report, 'protocol')
    checked(summary.get('contract') == 'initial-coverage-all-v1'
            and protocol.get('contract') == 'initial-coverage-protocol-v2', '不支持的总览契约')
    checked(summary['protocol_sha256'] == protocol_source.sha256
            and summary['sqlite_sha256'] == protocol['sqlite_sha256'], '冻结总览与协议不一致')
    source_rows = summary['results']
    frozen = {row['ticker']: row for row in protocol['companies']}
    checked(len(frozen) == len(protocol['companies']) == len(source_rows) == summary['count'] == summary['source_universe']
            and len({row['ticker'] for row in source_rows}) == len(source_rows)
            and set(frozen) == {row['ticker'] for row in source_rows}, '证券集合、重复代码或分母不一致')
    checked(not tickers or set(tickers) <= set(frozen), '试验证券不属于总览')
    rows, flags = [], Counter()
    for row in source_rows:
        if tickers and row['ticker'] not in tickers:
            continue
        projected = project_row(row, frozen[row['ticker']], protocol, summary)
        flags.update(projected['review_flags'])
        rows.append(projected)
    counts = dict(Counter(row['admission'] for row in rows))
    if not tickers:
        checked(counts == summary['admissions'], '准入计数与总览不一致')
        for key in FLAGS:
            checked(flags[key] == summary['risk_and_diagnostic_counts'].get(key, 0), '审阅提示计数不一致：' + key)
    rows.sort(key=lambda row: row['ticker'])
    csv, _ = verified_file(report, 'csv')
    metadata = dict(id=dataset_id, title=report.title, source_report_id=report_id,
        report_url='/research/' + report_id, csv_url=f'/api/research/{report_id}/files/{csv.id}?download=true',
        research_date=report.research_date.isoformat() if report.research_date else None,
        financial_period=summary['report_period'], information_as_of=summary['valuation_information_as_of'],
        research_frozen_at=summary['research_frozen_at'], company_evidence_acquired_at=summary['company_evidence_acquired_at'],
        share_dates=dict(Counter(row['share_date'] or 'unknown' for row in rows)),
        quote_dates=dict(Counter(row['close_date'] or 'unknown' for row in rows)),
        condition_type='统一原生条件；未认证为公司预测', source_result_sha256=source.sha256,
        source_manifest_sha256=hashlib.sha256(workspace_path('research', report_id, 'research.json').read_bytes()).hexdigest(),
        protocol_sha256=protocol_source.sha256, sqlite_sha256=summary['sqlite_sha256'],
        reference_snapshot_id=protocol['reference_snapshot_id'], runtime_stages={key: protocol['runtime']['stages'][key]
            for key in ('calculation', 'review')}, build_version=BUILD_VERSION,
        built_at=datetime.now(timezone.utc).isoformat(), limitations=report.limitations)
    return dict(contract=CONTRACT, dataset=metadata, universe_count=len(rows), admission_counts=counts,
                review_flag_counts=dict(flags), rows=rows, issues=[])


def atomic_json(path, body):
    encoded = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.pending-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return hashlib.sha256(encoded).hexdigest()


def publish_projection(body, activate=False):
    dataset_id = body['dataset']['id']
    target = result_path(dataset_id, 'index.json')
    result_path().mkdir(parents=True, exist_ok=True)
    with result_path('publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = registry()
        if target.exists():
            existing, digest = read_json(target)
            if dataset_id in records['datasets']:
                checked(digest == records['datasets'][dataset_id]['sha256'], '已发布摘要字节发生变化，不重签旧ID')
            body['dataset']['built_at'] = existing['dataset']['built_at']
            checked(existing == body, '摘要ID已存在且内容不同；修订请新建ID')
        else:
            target.parent.mkdir(exist_ok=True)
            digest = atomic_json(target, body)
        records['datasets'][dataset_id] = dict(sha256=digest, dataset=body['dataset'], universe_count=body['universe_count'])
        if activate:
            records['current'] = dataset_id
        atomic_json(result_path('current.json'), records)
    return dict(id=dataset_id, count=body['universe_count'], admission_counts=body['admission_counts'],
                review_flag_counts=body['review_flag_counts'], sha256=digest, bytes=target.stat().st_size,
                current=records['current'], url='/screen?dataset=' + dataset_id)


def build(report_id, dataset_id, activate=False):
    return publish_projection(project(report_id, dataset_id), activate)


def import_snapshot(path, activate=False):
    """显式导入已按当前消费契约转换的摘要；HTTP不解析历史格式。"""
    body, _ = read_json(path)
    checked(body.get('contract') == CONTRACT, '只接受当前选股消费契约')
    metadata = body['dataset']
    checked(re.fullmatch(IDENTIFIER, metadata['id']), '摘要ID不合格')
    report = read_report(metadata['source_report_id'])
    checked(metadata['report_url'] == '/research/' + report.id, '总览链接与登记不一致')
    sources = metadata.get('source_files')
    checked(isinstance(sources, list) and sources, '导入摘要必须列明已登记来源')
    source_hashes = set()
    for reference in sources:
        entry, _ = verified_file(read_report(reference['report_id']), reference['file_id'])
        checked(entry.sha256 == reference['sha256'], '转换来源哈希与登记不一致')
        source_hashes.add(entry.sha256)
    checked(metadata['source_result_sha256'] in source_hashes, '结果来源未登记')
    checked(metadata['source_manifest_sha256'] == hashlib.sha256(
        workspace_path('research', report.id, 'research.json').read_bytes()).hexdigest(), '研究登记发生变化')
    checked(set(('title', 'csv_url', 'research_date', 'financial_period', 'information_as_of', 'research_frozen_at',
                 'company_evidence_acquired_at', 'condition_type', 'sqlite_sha256', 'reference_snapshot_id',
                 'runtime_stages', 'build_version', 'built_at', 'limitations')) <= metadata.keys(), '缺少批次上下文')
    if metadata['csv_url'] is not None:
        checked(any(metadata['csv_url'] == f'/api/research/{report.id}/files/{entry.id}?download=true'
                    and entry.path.endswith('.csv') for entry in report.files), 'CSV不是本总览登记文件')
    rows = body['rows']
    checked(isinstance(rows, list) and body['universe_count'] == len(rows)
            and len({row['ticker'] for row in rows}) == len(rows), '转换证券分母或重复代码有误')
    required = set(BASE_FIELDS) | {'assumptions', 'assumption_missing_reasons', 'review_flags',
                                 'comparison_display_status', 'comparison_display_reason', 'model_source'}
    reports = {report.id: report}
    for row in rows:
        checked(required <= row.keys(), '转换行缺少当前契约字段')
        checked(re.fullmatch(r'(SHSE|SZSE):[0-9]{6}', row['ticker']) and row['admission'] in ADMISSIONS,
                '转换行证券或计算状态无效')
        checked(row['comparison_display_status'] in COMPARISONS and row['price_comparison_status'] in COMPARISONS,
                '转换行比较状态无效')
        checked(set(row['assumptions']) == set(ASSUMPTIONS) and set(row['review_flags']) <= set(FLAGS),
                '转换行假设或提示不符合当前契约')
        for key in ('condition_value_cny_per_share', 'close_cny', 'descriptive_difference_pct'):
            number(row[key])
        for value in row['assumptions'].values():
            number(value)
        checked((row['admission'] == 'ready') == (row['condition_value_cny_per_share'] is not None),
                '转换行数值与计算状态不一致')
        if row['descriptive_difference_pct'] is not None:
            checked(row['comparison_display_status'] == 'capital_date_proxy'
                    and row['condition_value_cny_per_share'] is not None and row['close_cny'] is not None
                    and row['close_cny'] > 0, '不得发布不适用的价差')
        target = reports.get(row['report_id'])
        if target is None:
            target = reports[row['report_id']] = read_report(row['report_id'])
        checked(row['url'] == '/research/' + target.id
                and (not target.tickers or row['ticker'] in target.tickers), '转换行报告身份不一致')
        model = row['model_source']
        checked((model['path'] is None) == (model['sha256'] is None), '模型定位与哈希必须同时存在或同时未知')
        if model['path'] is not None:
            entry = next((entry for entry in target.files if entry.path == model['path']), None)
            checked(entry is not None and entry.sha256 == model['sha256'], '模型哈希与报告登记不一致')
            verified_file(target, entry.id)
    checked(body['admission_counts'] == dict(Counter(row['admission'] for row in rows)), '转换状态计数不一致')
    checked(body['review_flag_counts'] == dict(Counter(flag for row in rows for flag in row['review_flags'])),
            '转换提示计数不一致')
    for field, key in (('share_dates', 'share_date'), ('quote_dates', 'close_date')):
        checked(metadata[field] == dict(Counter(row[key] or 'unknown' for row in rows)), '转换日期分母不一致')
    checked(isinstance(body['issues'], list), '转换诊断必须保留')
    return publish_projection(body, activate)


def activate(identifier):
    from data_sources.valuation_results import load_dataset
    with result_path('publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        load_dataset(identifier)
        records = registry()
        records['current'] = identifier
        atomic_json(result_path('current.json'), records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check')
    check.add_argument('report_id')
    check.add_argument('--ticker', action='append', required=True)
    create = commands.add_parser('build')
    create.add_argument('report_id')
    create.add_argument('--dataset-id', required=True)
    create.add_argument('--activate', action='store_true')
    ingest = commands.add_parser('import-snapshot', help='导入显式转换成当前选股契约的摘要；不解析历史格式')
    ingest.add_argument('snapshot', type=Path)
    ingest.add_argument('--activate', action='store_true')
    switch = commands.add_parser('activate')
    switch.add_argument('dataset_id')
    args = parser.parse_args()
    try:
        if args.command == 'check':
            body = project(args.report_id, args.report_id, args.ticker)
            result = dict(count=len(body['rows']), rows=body['rows'])
        elif args.command == 'build':
            result = build(args.report_id, args.dataset_id, args.activate)
        elif args.command == 'import-snapshot':
            result = import_snapshot(args.snapshot, args.activate)
        else:
            activate(args.dataset_id)
            result = dict(current=args.dataset_id)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, str(exc) + '\n')
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
