import json
from pathlib import Path
import shutil

import pytest

from tools.verify_anker_earnings_history import verify


def test_original_earnings_and_tamper_rejection(tmp_path):
    source = Path(__file__).resolve().parents[3]/'internal/ingest/testdata/anker-cash-history-2024'
    result = verify(source)
    assert result['printed_amounts'] == 48 and len(result['source_values']) == 18
    assert {r['field'] for r in result['source_values']} == {'FN86','FN305','FN306','FN83','FN82','FN301'}
    copy = tmp_path/'evidence'; shutil.copytree(source, copy)
    path = copy/'earnings-evidence.json'; original = path.read_text()
    bad = json.loads(original); bad['reports'][0]['rows'][0]['values'][0] = '1017568922.80'
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match='PDF earnings amount'):
        verify(copy)
    bad = json.loads(original); bad['reports'][0]['main_page'] = 59
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match='consolidated statement'):
        verify(copy)
    path.write_text(original)
    path = copy/'source-snapshot.json'; bad = json.loads(path.read_text())
    next(r for r in bad['records'] if r['code']=='300866' and r['period']=='2024-06-30')['bits']['FN305'] ^= 1
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match='TDX earnings bits'):
        verify(copy)
