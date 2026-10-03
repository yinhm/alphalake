"""每次请求读取一个 SQLite 快照；文件原子替换后下一次请求立即可见。"""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

from data_sources.paths import workspace_path

from .schema import ID_PATTERN, SCHEMA_VERSION, TABLE_COLUMNS, content_hash, normalize_alias


class KnowledgeError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 503):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def database_path() -> Path:
    override = os.environ.get("ALPHALAKE_KNOWLEDGE_DB")
    return Path(override) if override else workspace_path("knowledge", "knowledge.sqlite")


def validate_database(connection: sqlite3.Connection, *, full: bool = False) -> dict:
    """运行时校验结构；发布工具额外校验内容及完整性。"""
    if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        raise ValueError("不支持的知识库结构版本")
    for table, expected in TABLE_COLUMNS.items():
        columns = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
        if columns != expected:
            raise ValueError(f"知识库表结构不符合 v{SCHEMA_VERSION}: {table}")
    releases = connection.execute("SELECT * FROM kb_release").fetchall()
    if len(releases) != 1:
        raise ValueError("知识库必须包含一个发布版本")
    release = dict(releases[0])
    if release["singleton"] != 1 or release["schema_version"] != SCHEMA_VERSION or not release["release_id"]:
        raise ValueError("知识库发布版本无效")
    if full:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("知识库完整性检查失败")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("知识库存在无效关联")
        for row in connection.execute("SELECT * FROM kb_term"):
            term = dict(row)
            if not ID_PATTERN.fullmatch(term["term_id"]):
                raise ValueError("词条 ID 必须为稳定 ASCII 标识")
            if not term["title_zh"].strip() or not term["summary"].strip():
                raise ValueError("词条必须提供中文名称和摘要")
            if term["content_hash"] != content_hash(term):
                raise ValueError("词条内容哈希不匹配")
        for row in connection.execute("SELECT alias, normalized_alias FROM kb_alias"):
            if not row[1] or normalize_alias(row[0]) != row[1]:
                raise ValueError("别名规范化结果无效")
        for row in connection.execute("SELECT source_id, url FROM kb_source"):
            parsed = urlparse(row[1])
            if not ID_PATTERN.fullmatch(row[0]) or parsed.scheme not in ("https", "http") or not parsed.netloc:
                raise ValueError("来源必须提供有效 ID 和 HTTP(S) 地址")
        for row in connection.execute("SELECT binding_id, field_path FROM kb_binding"):
            if not ID_PATTERN.fullmatch(row[0]):
                raise ValueError("绑定 ID 无效")
            # field_path 仅为声明性定位，不支持表达式、函数或下标访问。
            if row[1] and not all(ID_PATTERN.fullmatch(part) for part in row[1].split(".")):
                raise ValueError("绑定字段路径无效")
    return release


def publication_token(connection: sqlite3.Connection) -> str:
    """API 版本绑定全部内容，发布标签重复使用也不会复用旧详情缓存。"""
    tables = {table: sorted([tuple(row) for row in connection.execute(f"SELECT * FROM {table}")], key=repr)
              for table in TABLE_COLUMNS}
    return hashlib.sha256(json.dumps(tables, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@contextmanager
def open_database(path: Path | None = None):
    path = path or database_path()
    connection = None
    try:
        if not path.exists():
            raise KnowledgeError("knowledge_unavailable", "词条库尚未配置。")
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        release = validate_database(connection, full=True)
        release["release_label"] = release["release_id"]
        release["release_id"] = publication_token(connection)
        yield connection, release
    except KnowledgeError:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        # 不通过公共 API 暴露本机路径、SQL 或内容细节。
        raise KnowledgeError("knowledge_invalid", "词条库不可读取或结构版本不受支持，请联系维护者。") from exc
    finally:
        if connection is not None:
            connection.close()


def require_release(release: dict, requested: str | None) -> None:
    if requested is not None and requested != release["release_id"]:
        raise KnowledgeError("knowledge_release_changed", "词条库版本已更新，请刷新后重试。", 409)


def index_terms(connection: sqlite3.Connection) -> list[dict]:
    terms = [dict(row) for row in connection.execute(
        "SELECT term_id, title_zh, title_en, kind, category, summary FROM kb_term ORDER BY term_id"
    )]
    aliases: dict[str, list[str]] = {}
    for row in connection.execute("SELECT term_id, alias FROM kb_alias ORDER BY normalized_alias, alias"):
        values = aliases.setdefault(row[0], [])
        if row[1] not in values:
            values.append(row[1])
    return [{**term, "aliases": aliases.get(term["term_id"], [])} for term in terms]


def read_index() -> dict:
    try:
        with open_database() as (connection, release):
            return {"status": "ready", "release_id": release["release_id"], "schema_version": SCHEMA_VERSION,
                    "release_label": release["release_label"],
                    "terms": index_terms(connection)}
    except KnowledgeError as exc:
        if exc.code != "knowledge_unavailable":
            raise
        return {"status": "unavailable", "release_id": None, "schema_version": SCHEMA_VERSION, "terms": []}


def read_term(term_id: str, requested_release: str | None = None) -> dict:
    with open_database() as (connection, release):
        require_release(release, requested_release)
        term = connection.execute("SELECT * FROM kb_term WHERE term_id = ?", (term_id,)).fetchone()
        if term is None:
            raise KnowledgeError("knowledge_term_not_found", "未找到此词条。", 404)
        relations = [dict(row) for row in connection.execute("""
            SELECT t.term_id, t.title_zh, t.title_en, r.relation_type
            FROM kb_relation r JOIN kb_term t ON t.term_id = r.to_term_id
            WHERE r.from_term_id = ? ORDER BY r.sort_order, t.term_id
        """, (term_id,))]
        sources = [dict(row) for row in connection.execute("""
            SELECT s.*, c.section_anchor, c.locator
            FROM kb_citation c JOIN kb_source s USING (source_id)
            WHERE c.term_id = ? ORDER BY s.source_id, c.section_anchor, c.locator
        """, (term_id,))]
        bindings = [dict(row) for row in connection.execute(
            "SELECT * FROM kb_binding WHERE term_id = ? ORDER BY binding_id", (term_id,)
        )]
        return {"release_id": release["release_id"], "release_label": release["release_label"], "term": dict(term),
                "relations": relations, "sources": sources, "bindings": bindings}


def search_terms(query: str, limit: int, requested_release: str | None = None) -> dict:
    with open_database() as (connection, release):
        require_release(release, requested_release)
        normalized = normalize_alias(query)
        if not normalized:
            return {"release_id": release["release_id"], "release_label": release["release_label"], "results": []}
        ranked = []
        for term in index_terms(connection):
            names = [normalize_alias(value) for value in (term["term_id"], term["title_zh"], term["title_en"], *term["aliases"])]
            if normalized in names:
                rank = 0
            elif any(name.startswith(normalized) for name in names):
                rank = 1
            elif any(normalized in name for name in names):
                rank = 2
            else:
                continue
            ranked.append((rank, term["term_id"], term))
        ranked.sort(key=lambda item: item[:2])
        return {"release_id": release["release_id"], "release_label": release["release_label"], "results": [item[2] for item in ranked[:limit]]}
