"""核验已定位的管理层经营目标；资料未齐时不评分、不改变准入。"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from pypdf import PdfReader


def verify(directory, pdf_directory):
    study = json.loads((directory/'study.json').read_bytes())
    raw = (directory/'snapshot.json').read_bytes()
    receipt = json.loads((directory/'source-receipt.json').read_bytes())
    source = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest()==receipt['snapshot_sha256'], 'snapshot hash differs'
    assert source['study_sha256']==hashlib.sha256((directory/'study.json').read_bytes()).hexdigest()
    for name, digest in study['inputs'].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest
    old = json.loads(Path('valuation/research/continuing-operations-five/capital-snapshot.json').read_bytes())
    index = {(r['code'],r['period']):r for r in old['records']}
    assert len(source['records'])==receipt['records']==40
    checked_bits = 0
    for row in source['records']:
        before = index[row['code'],row['period']]
        assert row['artifact']==before['artifact']
        for field, bits in row['bits'].items():
            if field in before['bits']:
                assert bits==before['bits'][field]
                checked_bits += 1
    assert checked_bits==receipt['preserved_bits']==520
    evidence = json.loads((directory/'targets.json').read_bytes())
    expected = [(s['code'],year) for s in study['samples'] for year in study['origins']]
    assert [(r['code'],r['origin_year']) for r in evidence['positions']] == expected
    registry = json.loads((directory/'documents.json').read_bytes())
    catalogues = {}
    for entry in registry['catalogues']:
        raw = (directory/entry['file']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==entry['sha256'], 'catalogue hash differs'
        catalogue = json.loads(raw); items = catalogue['announcements']
        assert len(items)==catalogue['totalAnnouncement']==entry['count'] and not catalogue['hasMore']
        assert len({a['announcementId'] for a in items})==len(items)
        catalogues[entry['file']] = items
    assert [(d['code'],d['origin_year']) for d in registry['documents']]==expected
    checked = 0
    labels = {'total_operating_income':'营业总收入', 'operating_revenue':'营业收入目标为',
              'pretax_profit':'利润总额', 'net_income_parent':'归母净利目标为'}
    for row, doc in zip(evidence['positions'],registry['documents']):
        assert row['document']==doc
        assert doc['code']==row['code'] and doc['report_year']==row['origin_year']-1
        candidates = [a for a in catalogues[doc['catalogue']] if a['secCode']==row['code']
            and re.sub('<[^>]+>', '', a['announcementTitle']).endswith(str(doc['report_year'])+'年年度报告')]
        assert len(candidates)==1, 'original annual selection ambiguous'
        announcement = candidates[0]
        assert (announcement['announcementId'],announcement['orgId'])==(doc['announcement_id'],doc['org_id'])
        assert 'https://static.cninfo.com.cn/'+announcement['adjunctUrl']==doc['url']
        zone = timezone(timedelta(hours=8))
        available = datetime.fromtimestamp(announcement['announcementTime']/1000,zone).replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
        assert available.isoformat()==doc['available_at'] and available < datetime(row['origin_year'],9,1,tzinfo=zone)
        path = pdf_directory/doc['file']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==doc['sha256'], 'PDF hash differs'
        reader = PdfReader(path)
        for candidate in row['outside_plan_candidates']:
            assert candidate['anchor'] in re.sub(r'\s+', '',reader.pages[candidate['page']-1].extract_text())
            assert candidate['reason']
        texts = {i:re.sub(r'\s+', '',reader.pages[i-1].extract_text()) for i in row['reviewed_pages']}
        reviewed = ''.join(texts.values())
        assert hashlib.sha256(reviewed.encode()).hexdigest()==row['reviewed_text_sha256'], 'reviewed pages differ'
        if row['status']=='no_numeric_target_found_in_reviewed_plan':
            assert not row['targets'] and row['section_heading'] in reviewed and row['review_note']
            # 人工语义判读留痕；页哈希不能独立证明整份年报没有数值目标。
            continue
        assert row['status']=='numeric_targets_found_actual_semantics_pending' and row['targets']
        text = texts[row['page']]
        assert str(row['origin_year'])+'年，公司计划' in text
        for target in row['targets']:
            assert target['label']==labels[target['concept']] and target['printed_unit']=='亿元'
            matches = re.findall(re.escape(target['label'])+r'([\d,.]+)亿元',text)
            assert len(matches)==1, 'ambiguous target'
            assert Decimal(matches[0].replace(',',''))==Decimal(target['printed_amount']), 'target differs'
            assert target['kind']=='management_plan_not_reported_fact'
            checked += 1
    result = coverage(study,evidence)
    assert evidence['status']==result['decision']
    result['printed_targets_verified'] = checked
    result.update({name+'_sha256':hashlib.sha256((directory/(name+'.json')).read_bytes()).hexdigest()
                   for name in ('study','targets','documents')})
    return result


def coverage(study, evidence):
    rows = evidence['positions']
    primary = sum(any(t['concept'] in ('operating_revenue','total_operating_income') for t in r['targets']) for r in rows)
    minimum = study['gate']['primary_revenue_positions_at_least']
    return dict(positions=len(rows),source_statuses=dict(Counter(r['status'] for r in rows)),
        primary_eligible_upper_bound=primary,minimum_required=minimum,scored_positions=0,
        decision='stopped_insufficient_source_coverage' if primary<minimum else 'pending_semantic_review',
        boundary='source coverage only; no error scoring or claim of forecast effectiveness; no DCF adoption')


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('pdf_directory',type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory,args.pdf_directory),ensure_ascii=False,indent=2))
