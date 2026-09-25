package duckdb

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	duckdbgo "github.com/duckdb/duckdb-go/v2"
)

const (
	DriverName        = "duckdb"
	PersistentCatalog = "alphalake"
)

// Open opens a DuckDB database and verifies that the connection is usable.
//
// Persistent files are deliberately attached to an in-memory DuckDB instance
// under the stable catalog alias "alphalake" instead of being opened as the
// default file-derived catalog. DuckDB treats a two-part name like
// `classification.taxonomy` as ambiguous when the catalog and schema share the
// same name. A fixed catalog makes AlphaLake's domain schemas independent of the
// user's database filename (e.g. classification.duckdb is safe).
//
// Use :memory: for a purely in-memory database.
func Open(ctx context.Context, path string) (*sql.DB, error) {
	return open(ctx, path, false)
}

// OpenReadOnly 只读附加已有数据库，允许多个查询进程同时读取。
func OpenReadOnly(ctx context.Context, path string) (*sql.DB, error) {
	return open(ctx, path, true)
}

func open(ctx context.Context, path string, readOnly bool) (*sql.DB, error) {
	path = strings.TrimSpace(path)
	if path == "" {
		return nil, errors.New("duckdb path is empty")
	}

	if readOnly && path == ":memory:" {
		return nil, errors.New("read-only duckdb requires an existing persistent database")
	}

	var connector *duckdbgo.Connector
	var err error
	// 驱动内存配置允许查询溢写，但不是进程RSS硬上限；大任务仍需外部资源隔离。
	options := url.Values{}
	for option, variable := range map[string]string{"memory_limit": "ALPHALAKE_DUCKDB_MEMORY_LIMIT", "threads": "ALPHALAKE_DUCKDB_THREADS"} {
		if value := strings.TrimSpace(os.Getenv(variable)); value != "" {
			options.Set(option, value)
		}
	}
	dsn := ":memory:?" + options.Encode()
	if path == ":memory:" {
		connector, err = duckdbgo.NewConnector(dsn, nil)
	} else {
		absolutePath, absErr := filepath.Abs(path)
		if absErr != nil {
			return nil, fmt.Errorf("resolve duckdb path %q: %w", path, absErr)
		}
		attachSQL := fmt.Sprintf("ATTACH IF NOT EXISTS %s AS %s", duckdbStringLiteral(absolutePath), PersistentCatalog)
		if readOnly {
			attachSQL += " (READ_ONLY)"
		}
		connector, err = duckdbgo.NewConnector(dsn, func(execer driver.ExecerContext) error {
			if _, err := execer.ExecContext(context.Background(), attachSQL, nil); err != nil {
				return fmt.Errorf("attach AlphaLake database %q: %w", absolutePath, err)
			}
			if _, err := execer.ExecContext(context.Background(), "USE "+PersistentCatalog+".main", nil); err != nil {
				return fmt.Errorf("select AlphaLake catalog: %w", err)
			}
			return nil
		})
	}
	if err != nil {
		return nil, fmt.Errorf("open duckdb %q: %w", path, err)
	}

	db := sql.OpenDB(connector)
	if err := db.PingContext(ctx); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("ping duckdb %q: %w", path, err)
	}
	return db, nil
}

// OpenInitialized opens a current database or initializes an empty database.
func OpenInitialized(ctx context.Context, path string) (*sql.DB, error) {
	db, err := Open(ctx, path)
	if err != nil {
		return nil, err
	}
	if err := Initialize(ctx, db); err != nil {
		_ = db.Close()
		return nil, err
	}
	return db, nil
}

func duckdbStringLiteral(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "''") + "'"
}

// ReloadPersistentCatalog releases resident index memory between serialized
// write batches. Call only outside transactions with no concurrent DB users.
// DuckDB indexes are not evicted by the buffer manager; re-attachment makes them
// lazy again: https://duckdb.org/docs/lts/guides/performance/indexing
func ReloadPersistentCatalog(ctx context.Context, db *sql.DB) error {
	conn, err := db.Conn(ctx)
	if err != nil {
		return err
	}
	defer conn.Close()
	var path string
	err = conn.QueryRowContext(ctx, "SELECT path FROM duckdb_databases() WHERE database_name=?", PersistentCatalog).Scan(&path)
	if errors.Is(err, sql.ErrNoRows) {
		return nil
	} // Pure in-memory test databases.
	if err != nil {
		return err
	}
	for _, statement := range []string{"CHECKPOINT " + PersistentCatalog, "USE memory.main", "DETACH " + PersistentCatalog, "ATTACH " + duckdbStringLiteral(path) + " AS " + PersistentCatalog, "USE " + PersistentCatalog + ".main"} {
		if _, err := conn.ExecContext(context.WithoutCancel(ctx), statement); err != nil {
			return fmt.Errorf("reload persistent catalog: %w", err)
		}
	}
	return nil
}
