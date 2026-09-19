package duckdb

import (
	"path/filepath"
	"strconv"
	"testing"
)

func TestInitializeCurrentSchemaIsAtomicAndIdempotent(t *testing.T) {
	ctx := t.Context()
	path := filepath.Join(t.TempDir(), "current.duckdb")
	db, err := OpenInitialized(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if err = Initialize(ctx, db); err != nil {
		t.Fatal(err)
	}
	var version, versions, fields, mappings int
	if err = db.QueryRowContext(ctx, `SELECT (SELECT max(version) FROM meta.schema_version),(SELECT count(*) FROM meta.schema_version),(SELECT count(*) FROM fundamental.field),(SELECT count(*) FROM fundamental.provider_field)`).Scan(&version, &versions, &fields, &mappings); err != nil {
		t.Fatal(err)
	}
	if version != SchemaVersion || versions != 1 || fields != 81 || mappings != 81 {
		t.Fatal(version, versions, fields, mappings)
	}
	// 初始化不得修复或覆盖已存在的当前业务内容。
	if _, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=10000 WHERE canonical_field='revenue'`); err != nil {
		t.Fatal(err)
	}
	if err = Initialize(ctx, db); err != nil {
		t.Fatal(err)
	}
	var multiplier float64
	if err = db.QueryRowContext(ctx, `SELECT value_multiplier FROM fundamental.provider_field WHERE canonical_field='revenue'`).Scan(&multiplier); err != nil || multiplier != 10000 {
		t.Fatal(multiplier, err)
	}
	// 序列/模式冲突使初始化失败，事务不得留下部分表或版本。
	broken, err := Open(ctx, filepath.Join(t.TempDir(), "partial.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer broken.Close()
	if _, err = broken.ExecContext(ctx, `CREATE SCHEMA core`); err != nil {
		t.Fatal(err)
	}
	if err = Initialize(ctx, broken); err == nil {
		t.Fatal("conflicting schema accepted")
	}
	var tables int
	if err = broken.QueryRowContext(ctx, `SELECT count(*) FROM duckdb_tables() WHERE database_name=current_database() AND NOT internal`).Scan(&tables); err != nil || tables != 0 {
		t.Fatal(tables, err)
	}
	if v, e := CurrentSchemaVersion(ctx, broken); e != nil || v != 0 {
		t.Fatal(v, e)
	}
}

func TestInitializeRejectsUnsupportedAndUnversionedDatabases(t *testing.T) {
	for _, version := range []int{44, 45, 47} {
		t.Run(strconv.Itoa(version), func(t *testing.T) {
			db, err := Open(t.Context(), filepath.Join(t.TempDir(), "old.duckdb"))
			if err != nil {
				t.Fatal(err)
			}
			defer db.Close()
			if _, err = db.ExecContext(t.Context(), `CREATE SCHEMA meta; CREATE TABLE meta.schema_version(version INTEGER,description VARCHAR); INSERT INTO meta.schema_version VALUES (?,'frozen')`, version); err != nil {
				t.Fatal(err)
			}
			if err = Initialize(t.Context(), db); err == nil {
				t.Fatal("unsupported version accepted")
			}
			if got, e := CurrentSchemaVersion(t.Context(), db); e != nil || got != version {
				t.Fatal(got, e)
			}
		})
	}
	db, err := Open(t.Context(), filepath.Join(t.TempDir(), "unversioned.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err = db.ExecContext(t.Context(), `CREATE TABLE keep_evidence(value INTEGER); INSERT INTO keep_evidence VALUES (17)`); err != nil {
		t.Fatal(err)
	}
	if err = Initialize(t.Context(), db); err == nil {
		t.Fatal("nonempty unversioned database accepted")
	}
	var v int
	if err = db.QueryRowContext(t.Context(), `SELECT value FROM keep_evidence`).Scan(&v); err != nil || v != 17 {
		t.Fatal(v, err)
	}
}
