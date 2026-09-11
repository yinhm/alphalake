"""真实财务公司与合并表的口径隔离，不能因部分取证解除准入。"""
import json
from pathlib import Path
import shutil

import pytest

from tools.verify_gree_finance_scope import verify

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT/'valuation/research/continuing-operations-five'
PDFS = ROOT/'workspace/gree-finance-scope-20260911'


def test_real_finance_scope_and_tamper(tmp_path):
    original = (SOURCE/'gree-finance-scope.json').read_bytes()
    ledger = json.loads(original)
    if not all((PDFS/d['file']).exists() for d in ledger['documents'].values()):
        pytest.skip('two original CNINFO PDFs absent; restore ledger URLs and hashes')
    assert verify(SOURCE,PDFS)==ledger
    copy = tmp_path/'spec';copy.mkdir()
    shutil.copyfile(SOURCE/'capital-snapshot.json',copy/'capital-snapshot.json')
    path = copy/'gree-finance-scope.json'
    for target in ('subsidiary','consolidated','admission'):
        bad = json.loads(original)
        if target=='subsidiary':bad['notes'][0]['amount']='4635992'
        elif target=='consolidated':bad['tdx_checks'][0]['amount_cny']='671102741.85'
        else:bad['status']='ready'
        path.write_text(json.dumps(bad))
        with pytest.raises(AssertionError):verify(copy,PDFS)
    path.write_bytes(original)
    bad_pdf=tmp_path/'pdf';bad_pdf.mkdir()
    for doc in ledger['documents'].values():
        (bad_pdf/doc['file']).symlink_to(PDFS/doc['file'])
    damaged=bad_pdf/ledger['documents']['finance']['file']
    damaged.unlink();damaged.write_bytes(b'corrupt PDF')
    with pytest.raises(AssertionError,match='PDF hash'):verify(copy,bad_pdf)
