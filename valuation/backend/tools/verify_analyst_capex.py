"""核验固定六份研报资本开支口径诊断，不评分或生成估值。"""
import argparse
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import re

from pypdf import PdfReader
from tools.backtest_tdx_history import value


def verify(directory, pdf_directory):
    evidence = json.loads((directory/'capex-scope.json').read_bytes())
    sources = directory/'sources.json'
    snapshot = Path('valuation/research/continuing-operations-five/capital-snapshot.json')
    for path, key in ((sources,'sources_sha256'),(snapshot,'source_sha256')):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == evidence[key], 'source hash differs'
    original = json.loads(sources.read_bytes())
    assert [r['info_code'] for r in evidence['reports']] == [r['info_code'] for r in original]
    records = {(r['code'],r['period']):r for r in json.loads(snapshot.read_bytes())['records']}
    for row, source in zip(evidence['reports'],original):
        assert row['code'] == source['code'] and row['pdf_file'] == source['pdf_file']
        path = pdf_directory/row['pdf_file']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['pdf_sha256'] == source['metadata']['sha256'], 'PDF hash differs'
        text = PdfReader(path).pages[row['page']-1].extract_text()
        normalized = lambda s: re.sub(r'\s+', '', s).replace('（','(').replace('）',')')
        assert all(normalized(s) in normalized(text) for s in (row['unit'],' '.join(row['years']))), 'unit or years differ'
        number = r'\(?-?[\d,]+(?:\.\d+)?\)?'
        found = re.findall(row['label']+r'\s+('+number+r'(?:\s+'+number+r'){'+str(len(row['years'])-1)+r'})',text)
        assert len(found) == 1, 'ambiguous printed row'
        amounts = [Decimal(x.replace(',','').replace('(','-').replace(')','')) for x in found[0].split()]
        assert amounts == row['printed_million_cny'], 'printed amount differs'
        history = []
        for year, amount in zip(row['years'],row['printed_million_cny']):
            if year.endswith('E'): continue
            record = records.get((row['code'],year[:4]+'-12-31'))
            h = dict(year=year,printed_million_cny=amount)
            if record is None: h['status'] = 'missing_in_frozen_source'
            else:
                actual = value(record,'FN114')
                h.update(tdx_purchase_cash_cny=str(actual),bits=record['bits']['FN114'],artifact=record['artifact'],status='numeric_match_only' if (actual/1000000).quantize(Decimal(1),rounding=ROUND_HALF_UP)==abs(amount) else 'differs_from_gross_purchase_cash')
            history.append(h)
        assert history == row['historical'], 'historical diagnostic differs'
        assert row['status'] == 'unreviewed_capex_definition' and row['eligible_forecasts'] == 0
    assert evidence['positions'] == sum(sum(y.endswith('E') for y in r['years']) for r in evidence['reports']) == 18
    assert evidence['eligible'] == 0
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('pdf_directory',type=Path)
    args = parser.parse_args()
    verify(args.directory,args.pdf_directory)
    print('six PDF rows verified; 18 forecasts remain ineligible')
