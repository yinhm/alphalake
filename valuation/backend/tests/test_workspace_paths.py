from data_sources.paths import workspace_path
from data_sources.us_cn_hk_db import get_db_path


def test_dynamic_paths_use_workspace_without_seed_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(tmp_path))
    monkeypatch.delenv('US_CN_HK_DB_PATH', raising=False)
    assert workspace_path('derived', 'valuation-runs') == tmp_path/'derived/valuation-runs'
    assert get_db_path() == tmp_path/'derived/valuation.sqlite'
    monkeypatch.setenv('US_CN_HK_DB_PATH', str(tmp_path/'explicit.sqlite'))
    assert get_db_path() == tmp_path/'explicit.sqlite'
