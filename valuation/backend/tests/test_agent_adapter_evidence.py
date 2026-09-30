"""现行双公司适配证据：完整计算、冻结不变及篡改拒绝；实链另由CI默认入口执行。"""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tools import rebuild_agent_adapter_evidence as rebuild

ROOT = Path(__file__).resolve().parents[3]
CURRENT = ROOT / 'valuation/research/agent-adapters-current-contract-20260930'


def verifier(company, monkeypatch):
    path = ROOT / f'internal/ingest/testdata/{company}-agent-adapter-2026/verify.py'
    spec = importlib.util.spec_from_file_location(f'{company}_adapter_verifier', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def source_already_covered(argv, **kwargs):
        # 仅此轻量单测隔离上游Go/PDF进程；正式脚本没有skip开关，CI仍实际运行源链。
        assert argv == [module.sys.executable, str(module.CHAIN / 'verify.py')]
        assert kwargs == {'check': True}
        calls.append(argv)

    monkeypatch.setattr(module.subprocess, 'run', source_already_covered)
    return module, calls


@pytest.mark.parametrize('company', ['anker', 'moutai'])
def test_current_adapter_computation_matches_complete_evidence(company, monkeypatch):
    module, calls = verifier(company, monkeypatch)
    module.main()
    assert len(calls) == 1


def test_current_evidence_rebuild_is_exact_and_keeps_frozen_files(tmp_path):
    receipt = json.loads((CURRENT / 'receipt.json').read_bytes())
    before = {}
    for entry in receipt['records']:
        source = ROOT / entry['source']
        before[source] = source.read_bytes()
        assert rebuild.digest(before[source]) == entry['source_sha256']
        assert rebuild.digest((CURRENT / entry['file']).read_bytes()) == entry['sha256']
    target = tmp_path / 'rebuilt'
    actual = rebuild.rebuild(target, receipt['recorded_at'])
    assert actual == receipt
    for entry in receipt['records']:
        assert (target / entry['file']).read_bytes() == (CURRENT / entry['file']).read_bytes()
    assert all(path.read_bytes() == raw for path, raw in before.items())
    with pytest.raises(FileExistsError):
        rebuild.rebuild(target, receipt['recorded_at'])


@pytest.mark.parametrize('company', ['anker', 'moutai'])
@pytest.mark.parametrize('mutation', ['forecast', 'missing', 'request'])
def test_adapter_rejects_changed_numeric_missing_and_contract_evidence(company, mutation, tmp_path, monkeypatch):
    module, _ = verifier(company, monkeypatch)
    target = tmp_path / company
    shutil.copytree(module.CURRENT, target)
    if mutation == 'request':
        path = target / ('request.json' if company == 'anker' else 'requests.json')
        body = json.loads(path.read_bytes())
        inputs = body['inputs'] if company == 'anker' else body[0]['request']['inputs']
        del inputs['prepared_ttm']['financials']['consolidated_book_equity']
    else:
        path = target / 'result.json'
        body = json.loads(path.read_bytes())
        result = body if company == 'anker' else body['scenarios'][0]
        if mutation == 'forecast':
            result['forecast'][0]['fcff'] += 1
        else:
            result['historical_fcff'] = 0
    path.write_bytes(rebuild.encode(body))
    monkeypatch.setattr(module, 'CURRENT', target)
    with pytest.raises(AssertionError):
        module.main()


@pytest.mark.parametrize('company', ['anker', 'moutai'])
def test_adapter_does_not_continue_after_upstream_failure(company, monkeypatch):
    module, _ = verifier(company, monkeypatch)

    def fail(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(module.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        module.main()


@pytest.mark.parametrize('filename', ['facts.csv', 'windows.csv'])
def test_source_change_receipt_rejects_tampering(filename, tmp_path, monkeypatch):
    for name in rebuild.SOURCE_HASHES:
        shutil.copyfile(rebuild.CHAIN / name, tmp_path / name)
    path = tmp_path / filename
    path.write_bytes(path.read_bytes().replace(b'0.0000000000', b'1.0000000000', 1))
    monkeypatch.setattr(rebuild, 'CHAIN', tmp_path)
    with pytest.raises(ValueError, match='reviewed source hash changed'):
        rebuild.verify_source_changes()


def test_rebuild_rejects_unreviewed_model_fields():
    frozen = json.loads((ROOT / 'internal/ingest/testdata/anker-agent-adapter-2026/request.json').read_bytes())
    frozen['inputs']['unexpected_old_alias'] = 0
    with pytest.raises(ValueError, match='unreviewed model contract'):
        rebuild.rebuild_request(frozen)
