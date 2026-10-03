"""使用临时合成词条验证知识库契约；不依赖或写入实际 workspace。"""

import asyncio
from copy import deepcopy
import hashlib
import json
import sqlite3

from fastapi import FastAPI
import httpx
import pytest

from api.knowledge import router
from knowledge.schema import SCHEMA_VERSION
from knowledge.store import KnowledgeError, database_path, open_database, read_index, read_term, search_terms, validate_database
from tools.knowledge_db import build_database, main


@pytest.fixture
def manifest():
    return {
        "release": {"release_id": "test-v1", "content_commit": "synthetic", "published_at": "2026-01-01T00:00:00Z"},
        "terms": [
            {"term_id": "test-alpha", "title_zh": "测试概念甲", "title_en": "Test Alpha", "summary": "仅供回归测试。", "body_md": "测试正文。"},
            {"term_id": "test-beta", "title_zh": "测试概念乙", "title_en": "Test Beta", "summary": "另一个测试概念。"},
        ],
        "aliases": [
            {"term_id": "test-alpha", "alias": "ＡＢＣ"},
            {"term_id": "test-alpha", "alias": "共享测试"},
            {"term_id": "test-beta", "alias": "共享测试"},
        ],
        "relations": [{"from_term_id": "test-alpha", "to_term_id": "test-beta", "relation_type": "related"}],
        "sources": [{"source_id": "test-source", "title": "测试来源", "url": "https://example.com/test"}],
        "citations": [{"term_id": "test-alpha", "source_id": "test-source", "section_anchor": "definition", "locator": "测试定位"}],
        "bindings": [{"binding_id": "test-binding", "term_id": "test-alpha", "page_key": "wacc", "field_path": "wacc.wacc",
                      "context_key": "test-only", "usage_md": "测试用法", "limitations_md": "非实际方法说明", "reviewed_engine_ref": "test-ref"}],
    }


@pytest.fixture
def database(tmp_path, monkeypatch, manifest):
    path = tmp_path / "knowledge" / "knowledge.sqlite"
    monkeypatch.setenv("ALPHALAKE_KNOWLEDGE_DB", str(path))
    build_database(manifest, path)
    return path


def request(path, **kwargs):
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(path, **kwargs)

    return asyncio.run(run())


def test_missing_database_is_explicit_and_never_created(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPHALAKE_KNOWLEDGE_DB", raising=False)
    monkeypatch.setenv("ALPHALAKE_WORKSPACE", str(tmp_path))
    assert database_path() == tmp_path / "knowledge" / "knowledge.sqlite"
    result = request("/api/knowledge/index")
    assert result.status_code == 200
    assert result.json() == {"status": "unavailable", "release_id": None, "schema_version": SCHEMA_VERSION, "terms": []}
    detail = request("/api/knowledge/terms/test-alpha")
    assert detail.status_code == 503
    assert detail.json()["detail"]["code"] == "knowledge_unavailable"
    assert not (tmp_path / "knowledge").exists()


def test_index_ambiguous_aliases_and_read_only(database):
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    result = request("/api/knowledge/index")
    assert result.status_code == 200
    index = result.json()
    assert index["status"] == "ready" and index["release_label"] == "test-v1"
    assert len(index["release_id"]) == 64
    assert len(index["terms"]) == 2
    assert all("共享测试" in term["aliases"] for term in index["terms"])
    assert all("body_md" not in term for term in index["terms"])
    with open_database() as (connection, _):
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM kb_term")
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert list(database.parent.iterdir()) == [database]


def test_detail_separates_sources_relations_and_local_bindings(database):
    result = request("/api/knowledge/terms/test-alpha", params={"release_id": read_index()["release_id"]})
    assert result.status_code == 200
    detail = result.json()
    assert detail["term"]["body_md"] == "测试正文。"
    assert len(detail["term"]["content_hash"]) == 64
    assert detail["relations"] == [{"term_id": "test-beta", "title_zh": "测试概念乙", "title_en": "Test Beta", "relation_type": "related"}]
    assert detail["sources"][0]["published_at"] is None
    assert detail["sources"][0]["locator"] == "测试定位"
    assert detail["bindings"][0]["field_path"] == "wacc.wacc"
    assert request("/api/knowledge/terms/missing").status_code == 404
    with pytest.raises(KnowledgeError) as error:
        read_term("test-alpha' OR 1=1 --")
    assert error.value.status_code == 404


@pytest.mark.parametrize("path", ["/api/knowledge/index", "/api/knowledge/terms/test-alpha"])
def test_etag_conditional_get(database, path):
    first = request(path)
    etag = first.headers["etag"]
    assert first.headers["cache-control"] == "no-cache"
    for value in [etag, f'"other", W/{etag}', "*"]:
        cached = request(path, headers={"If-None-Match": value})
        assert cached.status_code == 304 and cached.content == b""
        assert cached.headers["etag"] == etag
    assert request(path, headers={"If-None-Match": '"other"'}).status_code == 200


def test_search_normalization_ranking_ambiguity_and_literals(database):
    assert [term["term_id"] for term in search_terms(" abc ", 20)["results"]] == ["test-alpha"]
    assert len(search_terms("共享测试", 20)["results"]) == 2
    assert len(search_terms("测试概念", 1)["results"]) == 1
    assert search_terms("%", 20)["results"] == []
    assert search_terms("_", 20)["results"] == []
    assert search_terms("   ", 20)["results"] == []
    assert request("/api/knowledge/search", params={"q": "共享测试"}).status_code == 200
    assert request("/api/knowledge/search", params={"q": "test", "limit": 0}).status_code == 422
    assert request("/api/knowledge/search", params={"q": "x" * 161}).status_code == 422


def test_atomic_publication_pins_one_request_and_rejects_stale_release(database, manifest):
    old_release = read_index()["release_id"]
    first_etag = request("/api/knowledge/index").headers["etag"]
    updated = deepcopy(manifest)
    updated["release"]["release_id"] = "test-v2"
    updated["terms"][0]["summary"] = "更新后的测试摘要。"
    with open_database() as (connection, release):
        build_database(updated, database)
        assert release["release_label"] == "test-v1"
        assert release["release_id"] == old_release
        assert connection.execute("SELECT summary FROM kb_term WHERE term_id='test-alpha'").fetchone()[0] == "仅供回归测试。"
    new_index = request("/api/knowledge/index", headers={"If-None-Match": first_etag})
    assert new_index.status_code == 200 and new_index.headers["etag"] != first_etag
    assert new_index.json()["release_label"] == "test-v2"
    for path in ["/api/knowledge/terms/test-alpha", "/api/knowledge/search?q=test"]:
        separator = "&" if "?" in path else "?"
        stale = request(path + separator + "release_id=" + old_release, headers={"If-None-Match": "*"})
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "knowledge_release_changed"


@pytest.mark.parametrize("kind", ["corrupt", "version", "table", "release", "content"])
def test_invalid_database_reports_safe_error(database, kind):
    if kind == "corrupt":
        database.write_bytes(b"not a sqlite database")
    else:
        with sqlite3.connect(database) as connection:
            connection.execute({"version": "PRAGMA user_version=99", "table": "DROP TABLE kb_alias", "release": "DELETE FROM kb_release",
                                "content": "UPDATE kb_term SET summary='tampered'"}[kind])
    result = request("/api/knowledge/index")
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "knowledge_invalid"
    assert str(database) not in result.text
    assert "SELECT" not in result.text


def test_failed_or_reused_release_keeps_published_database(database, manifest):
    original = database.read_bytes()
    changed = deepcopy(manifest)
    changed["terms"][0]["body_md"] = "不得覆盖同一发布。"
    with pytest.raises(ValueError, match="release_id"):
        build_database(changed, database)
    assert database.read_bytes() == original
    invalid = deepcopy(manifest)
    invalid["release"]["release_id"] = "test-v2"
    invalid["aliases"].append({"term_id": "not-present", "alias": "坏关联"})
    with pytest.raises(sqlite3.IntegrityError):
        build_database(invalid, database)
    assert database.read_bytes() == original
    assert list(database.parent.iterdir()) == [database]


def test_reused_historical_label_cannot_reuse_cache_version(database, manifest):
    before = read_index()
    other = deepcopy(manifest)
    other["release"]["release_id"] = "intermediate"
    build_database(other, database)
    changed = deepcopy(manifest)
    changed["terms"][0]["body_md"] = "只有详情发生变化。"
    build_database(changed, database)
    after = read_index()
    assert before["terms"] == after["terms"]
    assert before["release_label"] == after["release_label"]
    assert before["release_id"] != after["release_id"]
    with pytest.raises(KnowledgeError) as error:
        read_term("test-alpha", before["release_id"])
    assert error.value.status_code == 409


def test_publish_lock_refuses_concurrent_writer(database, manifest):
    before = database.read_bytes()
    lock = database.with_name(database.name + ".publish.lock")
    lock.write_text("existing writer", encoding="utf-8")
    with pytest.raises(ValueError, match="发布锁"):
        build_database(manifest, database)
    assert database.read_bytes() == before
    assert lock.read_text(encoding="utf-8") == "existing writer"


@pytest.mark.parametrize("change", ["unsafe_source", "unsafe_path", "bad_id", "extra_key", "naive_date"])
def test_content_validator_rejects_invalid_manifest(tmp_path, manifest, change):
    invalid = deepcopy(manifest)
    if change == "unsafe_source":
        invalid["sources"][0]["url"] = "javascript:alert(1)"
    elif change == "unsafe_path":
        invalid["bindings"][0]["field_path"] = "wacc.compute()"
    elif change == "bad_id":
        invalid["terms"][0]["term_id"] = "../../escape"
    elif change == "extra_key":
        invalid["terms"][0]["html"] = "<script/>"
    else:
        invalid["release"]["published_at"] = "2026-01-01"
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        build_database(invalid, tmp_path / "bad.sqlite")
    assert not (tmp_path / "bad.sqlite").exists()


def test_validate_detects_tampered_hash_and_alias(database):
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("UPDATE kb_term SET body_md='tampered' WHERE term_id='test-alpha'")
        with pytest.raises(ValueError, match="哈希"):
            validate_database(connection, full=True)
        connection.rollback()
        connection.execute("UPDATE kb_alias SET normalized_alias='wrong' WHERE alias='ＡＢＣ'")
        with pytest.raises(ValueError, match="别名"):
            validate_database(connection, full=True)


def test_cli_init_build_validate_and_no_overwrite(tmp_path, monkeypatch, capsys, manifest):
    monkeypatch.delenv("ALPHALAKE_KNOWLEDGE_DB", raising=False)
    monkeypatch.setenv("ALPHALAKE_WORKSPACE", str(tmp_path))
    assert main(["init", "--release-id", "empty-test"]) == 0
    assert read_index()["status"] == "ready" and read_index()["terms"] == []
    with pytest.raises(SystemExit) as error:
        main(["init", "--release-id", "empty-test"])
    assert error.value.code == 1
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(manifest), encoding="utf-8")
    assert main(["build", "--source", str(source)]) == 0
    assert main(["validate"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["terms"] == 2
