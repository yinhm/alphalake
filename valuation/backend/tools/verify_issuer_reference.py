"""复验人工审核的同发行人参考关联，生成既有API的显式输入；不修改默认分类。"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from pypdf import PdfReader
import xlrd

from data_sources.damodaran_parsers.company_industry_parser import alphalake_company_industry_snapshot
from tools.export_alphalake_sqlite import digest


def signature(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def compact(text):
    return re.sub(r'\s+', '', text).casefold()


def local_file(workspace, name):
    path = (workspace / name).resolve()
    if not path.is_relative_to(workspace.resolve()):
        raise ValueError('evidence path leaves workspace')
    return path


def verify(manifest, workspace, workbook, universe):
    if manifest['contract'] != 'alphalake-issuer-reference-review-v1':
        raise ValueError('unsupported issuer review contract')
    snapshot = alphalake_company_industry_snapshot(workbook)
    if snapshot['sha256'] != manifest['workbook_sha256']:
        raise ValueError('company workbook hash mismatch')
    if (universe['contract_version'] != 'alphalake-native-coverage-v1'
            or not universe['full_source_universe']
            or len(universe['companies']) != universe['source_universe']):
        raise ValueError('complete security denominator required')
    targets = {c['ticker']: c for c in universe['companies']}
    if len(targets) != universe['source_universe']:
        raise ValueError('duplicate target identity')
    exact = {r['ticker'] for r in snapshot['companies']}
    # 复用完整三表核验；额外读取非沪深原行，不改写官方5100条源范围。
    book = xlrd.open_workbook(workbook, on_demand=True)
    try:
        sheet = book.sheet_by_name('By company name')
        source = {str(sheet.cell_value(i, 1)): (i + 1, sheet.row_values(i))
                  for i in range(1, sheet.nrows) if sheet.cell_value(i, 1)}
    finally:
        book.release_resources()
    current, documents = {}, {}
    for event in manifest['events']:
        ticker = event['target_ticker']
        if not re.fullmatch(r'(SHSE|SZSE):[0-9]{6}', ticker) or ticker not in targets:
            raise ValueError('SHSE/SZSE target required')
        prior = current.get(ticker)
        if event.get('supersedes') != (prior['review_sha256'] if prior else None):
            raise ValueError('review chain mismatch')
        if not event['reviewer'].strip() or not event['review_note'].strip():
            raise ValueError('explicit semantic review required')
        reviewed_at = datetime.fromisoformat(event['reviewed_at'])
        if reviewed_at.utcoffset() is None:
            raise ValueError('timezone-aware review time required')
        if reviewed_at > datetime.now(reviewed_at.tzinfo):
            raise ValueError('future review time')
        if prior and reviewed_at <= datetime.fromisoformat(prior['reviewed_at']):
            raise ValueError('review time must advance')
        if event['action'] == 'revoke':
            if prior is None or prior['action'] != 'publish':
                raise ValueError('no active review to revoke')
            current[ticker] = dict(action='revoke', reviewed_at=event['reviewed_at'], review_sha256=signature(event))
            continue
        if event['action'] != 'publish':
            raise ValueError('unsupported review action')
        current[ticker] = dict(event, review_sha256=signature(event))
    # 先重放撤销链，再验证活动依据；已失效正文不应反向阻止撤销。
    for ticker, event in current.items():
        if event['action'] == 'revoke':
            continue
        if ticker in exact:
            raise ValueError('missing exact-source SHSE/SZSE target required')
        if event['relationship'] != 'same_legal_issuer_different_share_class':
            raise ValueError('same legal issuer review required; group/subsidiary excluded')
        source_ticker = event['source_ticker']
        if not re.fullmatch(r'SEHK:[0-9]{1,5}', source_ticker) or source_ticker not in source:
            raise ValueError('explicit other-share-class source required')
        row, values = source[source_ticker]
        if event['source_locator'] != f'By company name!A{row}:H{row}':
            raise ValueError('source row mismatch')
        source_name = values[0].removesuffix(' (' + source_ticker + ')')
        if compact(source_name) != compact(event['legal_name_en']):
            raise ValueError('source issuer legal name differs')
        doc = event['document']
        catalogue = local_file(workspace, doc['catalogue_path'])
        if digest(catalogue) != doc['catalogue_sha256']:
            raise ValueError('catalogue hash mismatch')
        announcements = [a for a in json.loads(catalogue.read_text())['announcements']
                         if a['announcementId'] == doc['announcement_id']]
        if len(announcements) != 1 or announcements[0]['secCode'] != ticker.split(':')[1]:
            raise ValueError('catalogue announcement target mismatch')
        announcement = announcements[0]
        if ('https://static.cninfo.com.cn/' + announcement['adjunctUrl'] != doc['source_url']
                or announcement['orgId'] != doc['organization_id']):
            raise ValueError('document provenance mismatch')
        pdf = local_file(workspace, doc['local_path'])
        if digest(pdf) != doc['sha256']:
            raise ValueError('PDF hash mismatch')
        if doc['sha256'] not in documents:
            documents[doc['sha256']] = PdfReader(pdf)
        reader = documents[doc['sha256']]
        evidence = event['evidence']
        roles = {'legal_name_cn', 'legal_name_en', 'a_listing', 'other_listing', 'exchanges'}
        if set(evidence) != roles:
            raise ValueError('issuer-name and share-class evidence required')
        for anchor in evidence.values():
            page = anchor['page']
            if type(page) is not int or not 1 <= page <= len(reader.pages):
                raise ValueError('invalid evidence page')
            if not anchor['text'].strip() or compact(anchor['text']) not in compact(reader.pages[page-1].extract_text()):
                raise ValueError('PDF evidence anchor changed')
        checks = {
            'legal_name_cn': event['legal_name_cn'], 'legal_name_en': event['legal_name_en'],
            'a_listing': ticker.split(':')[1], 'other_listing': source_ticker.split(':')[1],
        }
        for role, value in checks.items():
            text = compact(evidence[role]['text'])
            if role == 'other_listing':
                codes = re.findall(r'(?<!\d)\d{1,5}(?!\d)', text)
                if not any(int(code) == int(value) for code in codes):
                    raise ValueError('other share-class code mismatch')
            elif compact(value) not in text:
                raise ValueError('identity anchor mismatch: ' + role)
        if ('a股' not in compact(evidence['a_listing']['text'])
                or 'h股' not in compact(evidence['other_listing']['text'])
                or '香港' not in evidence['exchanges']['text']
                or ('上海' if ticker.startswith('SHSE:') else '深圳') not in evidence['exchanges']['text']):
            raise ValueError('share-class/exchange role mismatch')
        current[ticker] = dict(action='publish', reviewed_at=event['reviewed_at'],
            review_sha256=event['review_sha256'], source_ticker=source_ticker, source_locator=event['source_locator'],
            document=doc, relationship=event['relationship'], reviewer=event['reviewer'],
            request=dict(ticker=ticker, industry_override=values[2], country_override=values[5]))
    return dict(contract='alphalake-reviewed-issuer-inputs-v1', workbook_sha256=snapshot['sha256'],
        review_manifest_sha256=signature(manifest), source_universe=universe['source_universe'],
        coverage_sha256=signature(universe),
        active=[dict(target_ticker=t, **r) for t, r in sorted(current.items()) if r['action']=='publish'],
        revoked=[t for t, r in sorted(current.items()) if r['action']=='revoke'],
        defaults_changed=False, valuations_run=0,
        boundary='人工语义审核后的身份关联复验；行业/国家沿用官方原行，通过既有显式输入消费，不认证经济适用性。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--workbook', type=Path, required=True)
    parser.add_argument('--coverage', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(json.loads(args.manifest.read_text()), args.workspace, args.workbook,
                            json.loads(args.coverage.read_text())), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
