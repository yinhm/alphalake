import json
from pathlib import Path
import shutil

import pytest

from tools.verify_analyst_operating_profit import build


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT/'valuation/research/analyst-revenue-latest'
PDFS = ROOT/'workspace/analyst-revenue-latest-20260911'


def test_real_reports_profit_pair_and_tamper(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    reports = json.loads((SOURCE/'sources.json').read_bytes())
    if not all((PDFS/r['pdf_file']).exists() for r in reports):
        pytest.skip('six archived broker PDFs absent; restore sources.json URLs and hashes for this original-PDF check')
    result = build(SOURCE,PDFS)
    assert result == json.loads((SOURCE/'operating-profit-result.json').read_bytes())
    assert result['summary']['statuses'] == {'blocked_history_anchor':3,'evaluated':9,'not_yet_observable':6}
    assert all(set(r['profit_predictions_cny'])=={'broker','companion_benchmark'} for r in result['results'])
    source = SOURCE/'operating-profit-history-source.json'
    completed = build(SOURCE,PDFS,source)
    assert completed == json.loads((SOURCE/'operating-profit-completed-result.json').read_bytes())
    assert completed['summary']['statuses'] == {'evaluated':12,'not_yet_observable':6}
    assert all(h['status']=='matched' for h in completed['history'])
    for old, new in zip(result['results'],completed['results']):
        for key in ('profit_predictions_cny','revenue_predictions_cny'):
            assert old[key] == new[key]
        if old['status'] == 'evaluated':
            assert old == new
    bad_source = tmp_path/'source.json'
    original_source = json.loads(source.read_bytes())
    original_source['records'][0]['bits']['FN86'] += 1
    bad_source.write_text(json.dumps(original_source))
    with pytest.raises(ValueError,match='supplement source hash'):
        build(SOURCE,PDFS,bad_source)
    copy = tmp_path/'spec';copy.mkdir()
    for name in ('operating-profit-plan.json','operating-profit-evidence.json'):
        shutil.copyfile(SOURCE/name,copy/name)
    path = copy/'operating-profit-evidence.json';original=path.read_bytes()
    for key,change in [('row',lambda s:s.replace('1,867','1,868')),('unit',lambda s:'人民币万元'),('years',lambda s:[x.replace('2023E','2023A') for x in s])]:
        bad=json.loads(original);bad[0][key]=change(bad[0][key]);path.write_text(json.dumps(bad))
        with pytest.raises(ValueError,match='PDF header/unit/row'):
            build(copy,PDFS)
    path.write_bytes(original)
    bad=tmp_path/'pdf';bad.mkdir()
    for r in reports:(bad/r['pdf_file']).symlink_to(PDFS/r['pdf_file'])
    target=bad/reports[0]['pdf_file'];target.unlink();target.write_bytes(b'corrupt PDF')
    with pytest.raises(ValueError,match='PDF hash'):
        build(copy,bad)
