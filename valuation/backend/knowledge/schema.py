"""知识库 v1 契约；只提交结构与工具，不提交词条内容。"""

import hashlib
import json
import re
import unicodedata

SCHEMA_VERSION = 1
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

TERM_FIELDS = ("term_id", "title_zh", "title_en", "kind", "category", "summary", "body_md")
TABLE_COLUMNS = {
    "kb_release": ("singleton", "release_id", "schema_version", "content_commit", "published_at"),
    "kb_term": (*TERM_FIELDS, "content_hash"),
    "kb_alias": ("term_id", "alias", "normalized_alias", "locale", "alias_type"),
    "kb_relation": ("from_term_id", "to_term_id", "relation_type", "sort_order"),
    "kb_source": ("source_id", "title", "author", "url", "published_at", "verified_at"),
    "kb_citation": ("term_id", "source_id", "section_anchor", "locator"),
    "kb_binding": ("binding_id", "term_id", "page_key", "field_path", "context_key", "usage_md", "limitations_md", "reviewed_engine_ref"),
}

DDL = """
PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;
CREATE TABLE kb_release (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    release_id TEXT NOT NULL UNIQUE CHECK (length(release_id) > 0),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    content_commit TEXT NOT NULL,
    published_at TEXT NOT NULL
);
CREATE TABLE kb_term (
    term_id TEXT PRIMARY KEY NOT NULL,
    title_zh TEXT NOT NULL,
    title_en TEXT NOT NULL,
    kind TEXT NOT NULL,
    category TEXT NOT NULL,
    summary TEXT NOT NULL,
    body_md TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
CREATE TABLE kb_alias (
    term_id TEXT NOT NULL REFERENCES kb_term(term_id),
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    locale TEXT NOT NULL,
    alias_type TEXT NOT NULL,
    PRIMARY KEY (term_id, normalized_alias, locale, alias_type)
);
CREATE INDEX kb_alias_lookup ON kb_alias(normalized_alias);
CREATE TABLE kb_relation (
    from_term_id TEXT NOT NULL REFERENCES kb_term(term_id),
    to_term_id TEXT NOT NULL REFERENCES kb_term(term_id),
    relation_type TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (from_term_id, to_term_id, relation_type)
);
CREATE TABLE kb_source (
    source_id TEXT PRIMARY KEY NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT,
    verified_at TEXT
);
CREATE TABLE kb_citation (
    term_id TEXT NOT NULL REFERENCES kb_term(term_id),
    source_id TEXT NOT NULL REFERENCES kb_source(source_id),
    section_anchor TEXT NOT NULL,
    locator TEXT NOT NULL,
    PRIMARY KEY (term_id, source_id, section_anchor, locator)
);
CREATE TABLE kb_binding (
    binding_id TEXT PRIMARY KEY NOT NULL,
    term_id TEXT NOT NULL REFERENCES kb_term(term_id),
    page_key TEXT NOT NULL,
    field_path TEXT NOT NULL,
    context_key TEXT NOT NULL,
    usage_md TEXT NOT NULL,
    limitations_md TEXT NOT NULL,
    reviewed_engine_ref TEXT NOT NULL
);
CREATE INDEX kb_binding_term ON kb_binding(term_id);
"""


def normalize_alias(value: str) -> str:
    """中英文共用 NFKC、小写及空白折叠，不猜测缩写对应概念。"""
    return " ".join(unicodedata.normalize("NFKC", value).lower().split())


def content_hash(term: dict) -> str:
    value = {key: term[key] for key in TERM_FIELDS}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
