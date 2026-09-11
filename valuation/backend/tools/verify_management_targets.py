"""核验已定位的管理层经营目标；资料未齐时不评分、不改变准入。"""
import argparse
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
    checked = 0
    for row in evidence['positions']:
        if row['status']=='document_pending':
            assert not row['targets']
            continue
        assert row['status']=='extracted_selection_pending'
        doc = row['document'];path = pdf_directory/doc['file']
        assert doc['code']==row['code'] and doc['report_year']==row['origin_year']-1
        assert hashlib.sha256(path.read_bytes()).hexdigest()==doc['sha256'], 'PDF hash differs'
        text = re.sub(r'\s+', '', PdfReader(path).pages[row['page']-1].extract_text())
        assert str(row['origin_year'])+'年，公司计划' in text
        for target in row['targets']:
            matches = re.findall(re.escape(target['label'])+r'([\d,.]+)'+re.escape(target['printed_unit']),text)
            assert len(matches)==1, 'ambiguous target'
            assert Decimal(matches[0].replace(',',''))==Decimal(target['printed_amount']), 'target differs'
            assert target['kind']=='management_plan_not_reported_fact'
            checked += 1
    assert evidence['status']=='collection_in_progress_not_scored'
    return checked


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('pdf_directory',type=Path)
    args = parser.parse_args()
    print(verify(args.directory,args.pdf_directory),'printed targets verified; no scoring')
