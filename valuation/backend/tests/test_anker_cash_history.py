"""安克历史现金输入原文核对；此处尚不声称完成标准事实物化。"""
import copy
import hashlib
import json
from pathlib import Path
import shutil

import pytest
from tools.verify_anker_cash_history import verify

ROOT=Path(__file__).resolve().parents[3]
DIRECTORY=ROOT/'internal/ingest/testdata/anker-cash-history-2024'


def test_real_cash_history_and_semantic_tampering(tmp_path):
    actual=verify(DIRECTORY);saved=json.loads((DIRECTORY/'verified.json').read_text())
    assert actual=={k:v for k,v in saved.items() if k!='evidence'}
    assert len(actual['source_values'])==7
    assert [(r['field'],r['period']) for r in actual['source_values']]==[
        ('FN114','2024-06-30'),('FN114','2024-09-30'),('FN230','2024-09-30'),('FN234','2024-09-30'),
        ('FN114','2024-12-31'),('FN230','2024-12-31'),('FN234','2024-12-31')]
    assert [r['available_from'] for r in actual['periods']]==['2024-08-31T00:00:00+08:00','2024-10-31T00:00:00+08:00','2025-04-30T00:00:00+08:00']
    target=tmp_path/'evidence';shutil.copytree(DIRECTORY,target)
    ledger=json.loads((target/'evidence.json').read_text())
    bad=copy.deepcopy(ledger);bad['reports'][0]['rows'][2]['values'][0]='37900591.15'
    (target/'evidence.json').write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='PDF row values'):verify(target)
    bad=copy.deepcopy(ledger);bad['reports'][0]['rows'][0]['section']='4、母公司利润表'
    (target/'evidence.json').write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='scope/unit/columns'):verify(target)
    (target/'evidence.json').write_text(json.dumps(ledger))
    source=json.loads((target/'source-snapshot.json').read_text());bad=copy.deepcopy(source)
    next(r for r in bad['records'] if r['period']=='2024-09-30')['bits']['FN234']+=1
    (target/'source-snapshot.json').write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='source/PDF bits'):verify(target)
    (target/'source-snapshot.json').write_text(json.dumps(source))
    packages=json.loads((target/'packages.json').read_text());packages[0]['fetched_at']='2024-01-01T00:00:00Z'
    (target/'packages.json').write_text(json.dumps(packages))
    with pytest.raises(ValueError,match='source/package metadata'):verify(target)
    (target/'packages.json').write_bytes((DIRECTORY/'packages.json').read_bytes())
    catalogue=json.loads((target/'catalogue.json').read_text())
    next(a for a in catalogue['announcements'] if a['announcementId']=='1221558710')['announcementTime']+=86400000
    raw=json.dumps(catalogue).encode();(target/'catalogue.json').write_bytes(raw)
    request=json.loads((target/'catalogue-request.json').read_text());request['sha256']=hashlib.sha256(raw).hexdigest()
    (target/'catalogue-request.json').write_text(json.dumps(request))
    with pytest.raises(ValueError,match='publication differs'):verify(target)
