import json
from pathlib import Path
import shutil

import pytest

from tools.verify_anker_q1_history import verify


def test_q1_original_and_rejection(tmp_path):
    source = Path(__file__).resolve().parents[3]/'internal/ingest/testdata/anker-q1-history-2024'
    assert verify(source) == json.loads((source/'verified.json').read_bytes())
    copy = tmp_path/'evidence'; shutil.copytree(source, copy)
    path = copy/'evidence.json'; original = path.read_bytes()
    bad = json.loads(original); bad['revenue_current_prior_cny'][0] = '4377729053.28'
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match='PDF revenue'):
        verify(copy)
    bad = json.loads(original); bad['source_bits'] ^= 1
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match='TDX revenue bits'):
        verify(copy)
    path.write_bytes(original)
    path = copy/'reports.json'; bad = json.loads(path.read_bytes()); bad[0]['code'] = '600519'
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match='announcement identity'):
        verify(copy)
