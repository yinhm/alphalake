"""固定研发样本：六个安克年度与2017年正值/零值原文对照，不写标准事实。"""
import csv
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT/'valuation/research/tdx-capital-inputs'


def load_inputs():
    ledger = json.loads((DIRECTORY/'rd-semantics.json').read_text())
    sources = []
    for name, key in [('anker-rd-source.json.gz','anker_source_sha256'), ('2017-source.json.gz','source_2017_sha256')]:
        raw = gzip.decompress((DIRECTORY/name).read_bytes())
        if hashlib.sha256(raw).hexdigest() != ledger[key]:
            raise ValueError('source hash differs')
        sources.append(json.loads(raw))
    return ledger, *sources


def verify(ledger, anker, old):
    def record(source, code, period):
        rows = [r for r in source['records'] if (r['code'],r['period']) == (code,period)]
        if len(rows) != 1:
            raise ValueError('source identity not unique')
        return rows[0]

    def check_bits(row, amount):
        if row['bits']['FN304'] != struct.unpack('<I',struct.pack('<f',float(amount)))[0]:
            raise ValueError('FN304 bits differ')

    path = ROOT/'internal/ingest/testdata/anker-history-2026/reported.csv'
    if hashlib.sha256(path.read_bytes()).hexdigest() != ledger['anker_reported_sha256']:
        raise ValueError('Anker PDF ledger hash differs')
    with path.open() as f:
        rows = [r for r in csv.DictReader(f) if r['key'] == 'rd_expense']
    if sorted(r['year'] for r in rows) != [str(y) for y in range(2020,2026)]:
        raise ValueError('Anker years differ')
    result = []
    for r in rows:
        source = record(anker,'300866',r['year']+'-12-31')
        check_bits(source,r['value'])
        result.append(dict(code='300866',period=source['period'],status='matched_source_bits',
                           pdf_value_cny=r['value'],source_bits=source['bits']['FN304'],pdf_id=r['pdf_id'],page=r['pdf_page']))
    for report in ledger['reports']:
        pdf = DIRECTORY/report['file']
        if hashlib.sha256(pdf.read_bytes()).hexdigest() != report['sha256']:
            raise ValueError('PDF hash differs')
        # Existing CI poppler handles the encrypted public prospectus without a new Python dependency.
        text = subprocess.check_output(['pdftotext','-f',str(report['page']),'-l',str(report['page']),'-layout',str(pdf),'-'],text=True)
        body = re.sub(r'\s+','',text)
        if report['section'] not in body or report['unit_anchor'] not in body:
            raise ValueError('statement scope or unit differs')
        body = body.split(report['section'],1)[1]
        if any(h not in body for h in report['headers']):
            raise ValueError('period columns differ')
        matches = re.findall(re.escape(report['label'])+r'((?:[\d,]+\.\d{2})+)',body)
        if len(matches) != 1:
            raise ValueError('PDF row not unique')
        values = [v.replace(',','') for v in re.findall(r'[\d,]+\.\d{2}',matches[0])]
        if values != report['values']:
            raise ValueError('PDF amounts differ')
        for note in report.get('notes', []):
            text = subprocess.check_output(['pdftotext','-f',str(note['page']),'-l',str(note['page']),'-layout',str(pdf),'-'],text=True)
            if any(a not in re.sub(r'\s+','',text) for a in note['anchors']):
                raise ValueError('reclassification note differs')
        amount = values[report['column']]
        source = record(old,report['code'],report['period'])
        if report['status'] == 'matched_source_bits':
            check_bits(source,amount)
        elif report['status'] != 'zero_source_positive_comparative' or source['bits']['FN304'] != 0 or Decimal(amount) <= 0:
            raise ValueError('zero counterexample differs')
        result.append(dict(code=report['code'],period=report['period'],status=report['status'],
                           pdf_value_cny=amount,source_bits=source['bits']['FN304'],page=report['page']))
    return result


if __name__ == '__main__':
    print(json.dumps(verify(*load_inputs()),ensure_ascii=False,indent=2))
