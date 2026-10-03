"""选择规则不得遗漏显式全局/CI验收，也不得启动默认排除的重型夹具。"""
from types import SimpleNamespace
import pytest
from tests.conftest import pytest_collection_modifyitems


@pytest.mark.parametrize("full,ci,expected", [(False,"",1), (False,"false",1),
                                              (True,"",2), (False,"true",2), (False,"1",2)])
def test_full_selection(full, ci, expected, monkeypatch):
    monkeypatch.setenv("CI", ci)
    excluded = []
    config = SimpleNamespace(getoption=lambda _: full,
        hook=SimpleNamespace(pytest_deselected=lambda items: excluded.extend(items)))
    ordinary = SimpleNamespace(get_closest_marker=lambda _: None)
    heavy = SimpleNamespace(get_closest_marker=lambda _: True)
    items = [ordinary, heavy]
    pytest_collection_modifyitems(config, items)
    assert len(items) == expected
    assert excluded == ([heavy] if expected == 1 else [])
