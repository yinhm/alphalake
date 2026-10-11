"""归档已确认目录中的公司经营报告；不提取财务事实或选择预测参数。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen

from data_sources.paths import workspace_path
from data_sources.cninfo_documents import (document_lock, publish_reference, read_reference,
                                           verified_pdf)
from tools.export_alphalake_sqlite import digest
from tools.narrative_valuation import new_output, read_json, save


def reports(inventory, codes=None):
    scope_codes = {t.split(':')[1] for t in inventory['scope']}
    if codes and set(codes)-scope_codes:
        raise ValueError('requested codes outside frozen scope')
    result = []
    seen = set()
    for row in inventory['reports']:
        if row['provider_code'] not in (set(codes) if codes else scope_codes):
            continue
        if row['filing_variant'] != 'full' or row['resolution_status'] != 'resolved':
            continue
        if (row['filing_type'], row['period']) not in {
                ('annual', '2025-12-31'), ('semiannual', '2026-06-30')}:
            continue
        if not re.fullmatch(r'\d{6}', row['provider_code']) or not re.fullmatch(r'\d+', row['source_filing_id']):
            raise ValueError('invalid resolved filing identity')
        if not re.fullmatch(r'https://static\.cninfo\.com\.cn/finalpage/\d{4}-\d{2}-\d{2}/'+
                re.escape(row['source_filing_id'])+r'\.(?i:pdf)', row['source_url']):
            raise ValueError('document URL differs from official filing identity')
        if row['source_filing_id'] in seen:
            raise ValueError('duplicate source filing ID')
        seen.add(row['source_filing_id'])
        result.append(row)
    return result


def collect_one(row, output):
    result = dict(row)
    source_id = row['source_filing_id']
    try:
        if row.get('sha256') is not None and not re.fullmatch(r'[a-f0-9]{64}', row['sha256']):
            raise ValueError('invalid expected archive hash')
        with document_lock(row['source_url']):
            prior = read_reference(row['source_url'], row.get('sha256'))
            cached = workspace_path(row['local_path']) if row.get('local_path') else None
            if cached is not None and not cached.resolve().is_relative_to(workspace_path().resolve()):
                raise ValueError('cache leaves workspace')
            if cached is not None and cached.exists():
                sha = row.get('sha256') or digest(cached)
                raw = verified_pdf(cached, sha)
                prior = publish_reference(row['source_url'], raw, sha,
                    prior['fetched_at'] if prior else row.get('document_fetched_at'))
            elif prior is None and row.get('sha256'):
                sha = row['sha256']
                raw = workspace_path('cninfo', 'objects', sha[:2], sha+'.pdf')
                if raw.exists():
                    prior = publish_reference(row['source_url'], raw, sha, row.get('document_fetched_at'))
            if prior is not None:
                result['retrieval'] = 'existing_cache'
                result['hash_basis'] = 'matched_prior_archive' if row.get('sha256') else 'matched_url_download_receipt_not_semantic_review'
            else:
                workspace_path('cninfo').mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(prefix='.pdf-download-', dir=workspace_path('cninfo'), delete=False) as target:
                    temporary = Path(target.name)
                    try:
                        request = Request(row['source_url'], headers={'User-Agent': 'AlphaLake/0.0',
                            'Referer': 'https://www.cninfo.com.cn/'})
                        with urlopen(request, timeout=25) as response:
                            if response.url != row['source_url']:
                                raise ValueError('document redirect not approved')
                            size = 0
                            while chunk := response.read(1 << 20):
                                size += len(chunk)
                                if size > 256 << 20:
                                    raise ValueError('document exceeds source size limit')
                                target.write(chunk)
                        target.flush()
                        os.fsync(target.fileno())
                        sha = row.get('sha256') or digest(temporary)
                        try:
                            verified_pdf(temporary, sha)
                        except ValueError:
                            temporary.rename(output/(source_id+'.failed-response'))
                            raise
                        prior = publish_reference(row['source_url'], temporary, sha,
                            datetime.now(timezone.utc).isoformat())
                    finally:
                        temporary.unlink(missing_ok=True)
                result['retrieval'] = 'official_download'
                result['hash_basis'] = 'matched_prior_archive' if row.get('sha256') else 'official_download_first_observation'
            raw = workspace_path(prior['local_path'])
        result.update(pdf_file=prior['local_path'], pdf_sha256=prior['sha256'], first_fetched_at=prior['fetched_at'],
            retrieved_at=datetime.now(timezone.utc).isoformat())
        text_file = output/(source_id+'.txt')
        subprocess.run(['pdftotext', '-layout', '-enc', 'UTF-8', str(raw), str(text_file)],
            check=True, timeout=90, capture_output=True)
        text = text_file.read_text()
        if not text.strip():
            raise ValueError('empty extracted text; OCR not silently substituted')
        result.update(status='archived_for_review', text_file=text_file.name, text_sha256=digest(text_file),
            pages=len(text.split('\f'))-int(text.endswith('\f')),
            semantic_review='pending; hash/text extraction does not verify company or forecast')
    except Exception as error:
        result.update(status='source_or_extraction_failed', error_type=type(error).__name__, reason=str(error))
    save(output/(source_id+'.json'), result)
    return result


def collect(inventory_file, output, codes=None):
    inventory = read_json(inventory_file)
    selected = reports(inventory, codes)
    output = new_output(output)
    workspace_path('cninfo').mkdir(parents=True, exist_ok=True)
    save(output/'selection.json', dict(inventory_sha256=digest(inventory_file),
        scope=inventory['scope'], requested_codes=codes, reports=selected,
        selection_basis='resolved full documents in explicit FY2025 and H1-2026 periods; all duplicates retained'))
    started = time.monotonic()
    # ponytail: 两个网络/文本任务共享服务预算；只有实测吞吐瓶颈才扩大并发。
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = []
        for row in pool.map(lambda row: collect_one(row, output), selected):
            rows.append(row)
            if len(rows) % 20 == 0:
                print(f'archived {len(rows)}/{len(selected)}', flush=True)
    result = dict(contract='narrative-document-collection-v2', companies=len(inventory['scope']),
        selected_documents=len(selected), archived=sum(r['status']=='archived_for_review' for r in rows),
        failed=sum(r['status']!='archived_for_review' for r in rows),
        elapsed_seconds=time.monotonic()-started, reports=rows)
    save(output/'collection.json', result)
    return {k:v for k,v in result.items() if k != 'reports'}


def verify_collection(output):
    """仅验归档完整性；正文、业务语义和预测仍需分别审阅。"""
    receipt = read_json(output/'collection.json')
    if receipt.get('contract') != 'narrative-document-collection-v2':
        raise ValueError('current PDF reference collection contract required; frozen history uses original commit')
    rows = receipt['reports']
    good = [r for r in rows if r['status'] == 'archived_for_review']
    if (len(rows), len(good), len(rows)-len(good)) != (
            receipt['selected_documents'], receipt['archived'], receipt['failed']):
        raise ValueError('collection denominator mismatch')
    for row in good:
        sha = row['pdf_sha256']
        if not re.fullmatch(r'[a-f0-9]{64}', sha) or row['pdf_file'] != f'cninfo/objects/{sha[:2]}/{sha}.pdf':
            raise ValueError('collection PDF reference is not canonical: '+row['source_filing_id'])
        verified_pdf(workspace_path(row['pdf_file']), sha)
        text = output/row['text_file']
        if not text.resolve().is_relative_to(output.resolve()) or digest(text) != row['text_sha256']:
            raise ValueError('collection evidence hash/path mismatch: '+row['source_filing_id'])
    return dict(verified_documents=len(good), retained_failures=receipt['failed'], semantic_review='not_certified')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--inventory', type=Path)
    mode.add_argument('--verify', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--code', action='append')
    args = parser.parse_args()
    if args.verify:
        if args.output or args.code:
            parser.error('--verify does not collect or change scope')
        print(json.dumps(verify_collection(args.verify), ensure_ascii=False))
        return
    if args.output is None:
        parser.error('--inventory requires --output')
    print(json.dumps(collect(args.inventory, args.output, args.code), ensure_ascii=False))


if __name__ == '__main__':
    main()
