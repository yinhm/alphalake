"""构建、初始化和校验 workspace 中的词条库；不生成估值事实或默认词条。"""

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from knowledge.schema import DDL, ID_PATTERN, SCHEMA_VERSION, TABLE_COLUMNS, content_hash, normalize_alias
from knowledge.store import database_path, publication_token, validate_database

MANIFEST_TABLES = {
    "terms": "kb_term", "aliases": "kb_alias", "relations": "kb_relation",
    "sources": "kb_source", "citations": "kb_citation", "bindings": "kb_binding",
}
DEFAULTS = {
    "kb_term": {"title_en": "", "kind": "concept", "category": "", "body_md": ""},
    "kb_alias": {"locale": "und", "alias_type": "synonym"},
    "kb_relation": {"relation_type": "related", "sort_order": 0},
    "kb_source": {"author": "", "published_at": None, "verified_at": None},
    "kb_citation": {"section_anchor": "", "locator": ""},
    "kb_binding": {"page_key": "", "field_path": "", "context_key": "", "usage_md": "",
                   "limitations_md": "", "reviewed_engine_ref": ""},
}


def validate_manifest(manifest: dict) -> dict[str, list[dict]]:
    if not isinstance(manifest, dict) or set(manifest) - {"release", *MANIFEST_TABLES}:
        raise ValueError("输入必须为包含 release 与词条表数组的 JSON 对象，不能包含未知字段")
    release = manifest.get("release")
    if not isinstance(release, dict) or set(release) != {"release_id", "content_commit", "published_at"}:
        raise ValueError("release 必须包含 release_id、content_commit、published_at")
    if any(not isinstance(value, str) for value in release.values()) or not ID_PATTERN.fullmatch(release["release_id"]):
        raise ValueError("发布 ID 必须为 1–128 位稳定 ASCII 标识，发布元数据必须为字符串")
    _timestamp(release["published_at"], required_timezone=True)
    tables = {"kb_release": [{"singleton": 1, **release, "schema_version": SCHEMA_VERSION}]}
    for key, table in MANIFEST_TABLES.items():
        entries = manifest.get(key, [])
        if not isinstance(entries, list):
            raise ValueError(f"{key} 必须为数组")
        rows = []
        generated = {"content_hash"} if table == "kb_term" else {"normalized_alias"} if table == "kb_alias" else set()
        allowed = set(TABLE_COLUMNS[table]) - generated
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) - allowed:
                raise ValueError(f"{key} 包含未知字段或非对象条目")
            row = {**DEFAULTS[table], **entry}
            if set(row) != allowed:
                raise ValueError(f"{key} 缺少必需字段")
            for column, value in row.items():
                if table == "kb_relation" and column == "sort_order":
                    if type(value) is not int:
                        raise ValueError("sort_order 必须为整数")
                elif table == "kb_source" and column in ("published_at", "verified_at"):
                    if value is not None:
                        _timestamp(value)
                elif not isinstance(value, str):
                    raise ValueError(f"{key}.{column} 必须为字符串")
            if table == "kb_term":
                row["content_hash"] = content_hash(row)
            elif table == "kb_alias":
                row["normalized_alias"] = normalize_alias(row["alias"])
            rows.append(row)
        tables[table] = rows
    return tables


def _timestamp(value, *, required_timezone=False):
    if not isinstance(value, str) or not value:
        raise ValueError("日期必须为非空 ISO 8601 字符串；未知来源日期使用 null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("日期必须为 ISO 8601 字符串") from exc
    if required_timezone and parsed.tzinfo is None:
        raise ValueError("发布时间必须包含时区")


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _logical_content(connection: sqlite3.Connection) -> dict:
    return {table: sorted([tuple(row) for row in connection.execute(f"SELECT * FROM {table}")], key=repr)
            for table in TABLE_COLUMNS}


@contextmanager
def _publication_lock(output: Path):
    """单写发布；并发构建显式拒绝，崩溃遗留锁交由维护者核验后清理。"""
    lock = output.with_name(output.name + ".publish.lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("词条库正在发布或存在遗留发布锁，请确认没有运行中的发布进程后重试") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode())
        yield
    finally:
        os.close(descriptor)
        lock.unlink()


def build_database(manifest: dict, output: Path, *, require_new=False) -> dict:
    tables = validate_manifest(manifest)
    if require_new and output.exists():
        raise ValueError("目标词条库已存在，init 不覆盖已有内容")
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=".knowledge-", suffix=".sqlite", dir=output.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.row_factory = sqlite3.Row
            connection.executescript(DDL)
            for table, rows in tables.items():
                columns = TABLE_COLUMNS[table]
                connection.executemany(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                    [tuple(row[column] for column in columns) for row in rows],
                )
            release = validate_database(connection, full=True)
            new_content = _logical_content(connection)
            token = publication_token(connection)
            connection.commit()
        # 发布文件不使用 WAL；校验通过后单文件原子替换。
        with _publication_lock(output):
            if output.exists():
                if require_new:
                    raise ValueError("目标词条库已存在，init 不覆盖已有内容")
                with closing(_read_only(output)) as current:
                    current_release = validate_database(current, full=True)
                    if current_release["release_id"] == release["release_id"] and _logical_content(current) != new_content:
                        raise ValueError("已有发布 ID 的内容不可修改，请使用新的 release_id")
            with temporary.open("rb") as completed:
                os.fsync(completed.fileno())
            os.replace(temporary, output)
        return {"release_id": token, "release_label": release["release_id"], "schema_version": SCHEMA_VERSION,
                "terms": len(tables["kb_term"])}
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="创建空词条库，不含首版内容")
    init.add_argument("--release-id", required=True)
    init.add_argument("--output", type=Path, default=database_path())
    build = commands.add_parser("build", help="从 JSON 内容清单构建并原子发布")
    build.add_argument("--source", type=Path, required=True)
    build.add_argument("--output", type=Path, default=database_path())
    validate = commands.add_parser("validate", help="只读校验结构、关联及词条哈希")
    validate.add_argument("--database", type=Path, default=database_path())
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            with closing(_read_only(args.database)) as connection:
                release = validate_database(connection, full=True)
                result = {"release_id": publication_token(connection), "release_label": release["release_id"], "schema_version": SCHEMA_VERSION,
                          "terms": connection.execute("SELECT count(*) FROM kb_term").fetchone()[0]}
        else:
            if args.command == "init":
                manifest = {"release": {"release_id": args.release_id, "content_commit": "",
                                        "published_at": datetime.now(timezone.utc).isoformat()}}
            else:
                manifest = json.loads(args.source.read_text(encoding="utf-8"))
            result = build_database(manifest, args.output, require_new=args.command == "init")
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"词条库操作失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
