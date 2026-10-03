"""默认只运行日常回归；真实源链须显式全局执行。"""
import os
import json
from pathlib import Path
import subprocess
import pytest

REPO = Path(__file__).resolve().parents[3]


def pytest_addoption(parser):
    parser.addoption("--full", action="store_true", help="包含真实来源与跨语言全局验收")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--full") or os.getenv("CI", "").lower() in {"true", "1"}:
        return
    deselected = [item for item in items if item.get_closest_marker("full")]
    items[:] = [item for item in items if not item.get_closest_marker("full")]
    config.hook.pytest_deselected(items=deselected)


@pytest.fixture(scope='session')
def exports(tmp_path_factory):
    output = tmp_path_factory.mktemp('alphalake_exports')
    env = os.environ | {'ALPHALAKE_VALUATION_EXPORT_DIR':str(output),'GOPROXY':'off','GOSUMDB':'off'}
    subprocess.run(['go','test','-tags=full','./internal/ingest','-run','^TestRealValuationStandardChain$','-count=1'],
                   cwd=REPO,env=env,check=True,capture_output=True,text=True)
    return {code:json.loads((output/(code+'.json')).read_text()) for code in ['300866','600519','999999']}


@pytest.fixture(scope='session')
def reference_export(tmp_path_factory):
    output = tmp_path_factory.mktemp('wacc_references')
    env = os.environ | {'ALPHALAKE_WACC_EXPORT_DIR':str(output),'GOPROXY':'off','GOSUMDB':'off'}
    subprocess.run(['go','test','-tags=full','./internal/ingest','-run','^TestWACCReferenceExport$','-count=1'],
                   cwd=REPO,env=env,check=True,capture_output=True,text=True)
    return json.loads((output/'credit-references.json').read_text())


@pytest.fixture(scope='session')
def market_export(tmp_path_factory):
    import sys
    output=tmp_path_factory.mktemp('market_capital')
    env=os.environ|{'ALPHALAKE_MARKET_EXPORT_DIR':str(output),'ALPHALAKE_TEST_PYTHON':sys.executable,'GOPROXY':'off','GOSUMDB':'off'}
    subprocess.run(['go','test','-tags=full','./internal/ingest','-run','^TestMarketCapitalArchiveReplay$','-count=1'],cwd=REPO,env=env,check=True,capture_output=True,text=True)
    return {code:json.loads((output/(code+'.json')).read_text()) for code in ['300866','600519','300866-funding']}
