"""格力金融兼营的真实来源核验；不自动解除估值准入。"""
import argparse
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import struct

from pypdf import PdfReader


def verify(directory, pdf_directory):
    ledger = json.loads((directory/'gree-finance-scope.json').read_bytes())
    assert (ledger['code'],ledger['report_period']) == ('000651','2025-12-31')
    raw = (directory/'capital-snapshot.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ledger['source_sha256']
    record, = [r for r in json.loads(raw)['records'] if r['code']==ledger['code'] and r['period']==ledger['report_period']]
    documents = {}
    for key, doc in ledger['documents'].items():
        path = pdf_directory/doc['file']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == doc['sha256'], 'PDF hash differs'
        documents[key] = PdfReader(path)
    def page(key, number):
        return re.sub(r'\s+', '', documents[key].pages[number-1].extract_text())
    def amount(text, label, cents=False):
        pattern = r'([\d,]+\.\d{2})' if cents else r'([\d,]+(?:\.\d+)?)'
        matches = re.findall(re.escape(label)+pattern,text)
        assert len(matches)==1, 'ambiguous amount: '+label
        return Decimal(matches[0].replace(',',''))
    notes = {}
    for note in ledger['notes']:
        text = page('finance',note['page'])
        actual = amount(text,note['label'])
        assert actual == Decimal(note['amount']), 'finance amount differs'
        assert re.search(re.escape(note['label'])+r'[\d,.]+'+re.escape(note['unit']),text)
        notes[note['item']] = actual * {'万元':10000,'亿元':100000000}[note['unit']]
    assert notes['finance_assets']-notes['finance_liabilities']==notes['finance_equity']
    for field in ledger['tdx_checks']:
        text = page('annual',field['page'])
        assert '单位：人民币元' in text and ('合并利润表' in text or '合并资产负债表' in text)
        actual = amount(text,field['label'],cents=True)
        assert actual == Decimal(field['amount_cny']), 'consolidated amount differs'
        encoded = (actual/10000).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        assert struct.unpack('<I',struct.pack('<f',float(encoded)))[0] == record['bits'][field['field']], 'TDX bits differ'
    assert '金融99.540.46100.00' in page('annual',185)
    assert '公司分为消费电器、工业制品及绿色能源、智能装备及其他四个分部' in page('annual',207)
    assert ledger['status']=='partial_finance_scope_inputs_not_deconsolidated' and len(ledger['missing'])==5
    return ledger


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('pdf_directory',type=Path)
    args = parser.parse_args()
    verify(args.directory,args.pdf_directory)
    print('seven subsidiary disclosures and four TDX source fields verified; admission unchanged')
