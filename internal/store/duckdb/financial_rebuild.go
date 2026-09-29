package duckdb

import (
	"context"
	"crypto/sha256"
	"database/sql/driver"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"time"

	duckdbgo "github.com/duckdb/duckdb-go/v2"
	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

type FinancialRebuildResult struct {
	Packages, SourceRecords, Snapshots, StandardValues, Rejections int64
	Seconds                                                        float64
	MetadataSeconds, FinancialSeconds                              float64
}

// RebuildFinancialStorage builds an isolated candidate from immutable archives
// and retained governance metadata. It never mutates or replaces the input.
func RebuildFinancialStorage(ctx context.Context, previous, output, root, audit string) (result FinancialRebuildResult, err error) {
	started := time.Now()
	if _, e := os.Stat(output); !os.IsNotExist(e) {
		return result, fmt.Errorf("output must not exist")
	}
	if e := os.Mkdir(audit, 0700); e != nil {
		return result, e
	}
	db, err := Open(ctx, output)
	if err != nil {
		return result, err
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	absolute, err := filepath.Abs(previous)
	if err != nil {
		return result, err
	}
	if _, err = db.ExecContext(ctx, `ATTACH `+duckdbStringLiteral(absolute)+` AS previous (READ_ONLY)`); err != nil {
		return result, err
	}
	var version int
	if err = db.QueryRowContext(ctx, `SELECT max(version) FROM previous.meta.schema_version`).Scan(&version); err != nil {
		return result, err
	}
	if version != 51 {
		return result, fmt.Errorf("rebuild requires schema51, got %d", version)
	}
	var unsupported bool
	if err = db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM previous.fundamental.fact WHERE primary_source<>'tdx' OR primary_source IS NULL)`).Scan(&unsupported); err != nil {
		return result, err
	}
	if unsupported {
		return result, fmt.Errorf("non-TDX standard facts require a separate rebuild; refusing loss")
	}
	// Seed every sequence from the source before creating dependent tables. No
	// sequence is advanced through millions of discarded financial field IDs.
	ddl := schemaSQL
	rows, err := db.QueryContext(ctx, `SELECT schema_name,sequence_name,coalesce(last_value,start_value-1)+1 FROM duckdb_sequences() WHERE database_name='previous'`)
	if err != nil {
		return result, err
	}
	for rows.Next() {
		var schema, name string
		var start int64
		if err = rows.Scan(&schema, &name, &start); err != nil {
			rows.Close()
			return result, err
		}
		prefix := "CREATE SEQUENCE " + schema + "." + name + " "
		for _, line := range strings.Split(ddl, "\n") {
			if strings.HasPrefix(line, prefix) {
				ddl = strings.Replace(ddl, line, strings.Replace(line, "START 1 ", fmt.Sprintf("START %d ", start), 1), 1)
				break
			}
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return result, err
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return result, err
	}
	if _, err = tx.ExecContext(ctx, ddl); err == nil {
		_, err = tx.ExecContext(ctx, filingCoverageSQL)
	}
	if err == nil {
		err = insertSourceFieldCatalog(ctx, tx)
	}
	if err != nil {
		tx.Rollback()
		return result, err
	}
	if _, err = tx.ExecContext(ctx, `DELETE FROM meta.schema_version; INSERT INTO meta.schema_version(version,description) VALUES(-53,'Incomplete financial rebuild; not runtime-ready')`); err != nil {
		tx.Rollback()
		return result, err
	}
	if err = tx.Commit(); err != nil {
		return result, err
	}
	// Preserve non-rebuildable reviews, identifiers, reference releases and all
	// other domains. Original field diagnostics are archived once, not discarded.
	for _, line := range strings.Split(schemaSQL, "\n") {
		if !strings.HasPrefix(line, "CREATE TABLE ") {
			continue
		}
		table := strings.SplitN(strings.TrimPrefix(line, "CREATE TABLE "), "(", 2)[0]
		if table == "meta.schema_version" || table == "fundamental.fact" || table == "fundamental.provider_fact" || table == "meta.validation_result" {
			continue
		}
		if _, err = db.ExecContext(ctx, `DELETE FROM `+table+`; INSERT INTO `+table+` SELECT * FROM previous.`+table); err != nil {
			return result, fmt.Errorf("copy %s: %w", table, err)
		}
	}
	// Cold audit retains original fact IDs, field-level runs and historical
	// normalization metadata. Raw numeric evidence remains solely in source ZIPs.
	if _, err = db.ExecContext(ctx, `COPY previous.fundamental.fact TO `+duckdbStringLiteral(filepath.Join(audit, "standard-history.parquet"))+` (FORMAT PARQUET,COMPRESSION ZSTD)`); err != nil {
		return result, err
	}
	archive := filepath.Join(audit, "validation-history.parquet")
	if _, err = db.ExecContext(ctx, `COPY (SELECT * FROM previous.meta.validation_result) TO `+duckdbStringLiteral(archive)+` (FORMAT PARQUET,COMPRESSION ZSTD)`); err != nil {
		return result, err
	}
	if _, err = db.ExecContext(ctx, `INSERT INTO meta.validation_result SELECT * FROM previous.meta.validation_result WHERE dataset<>'fundamental_fact'`); err != nil {
		return result, err
	}
	// All data needed from the original database has been copied or archived.
	// Release its pinned indexes/buffers before decoding full financial packages.
	if _, err = db.ExecContext(ctx, `DETACH previous`); err != nil {
		return result, err
	}
	fields, err := LoadSnapshotFields(ctx, db)
	if err != nil {
		return result, err
	}
	tx, err = db.BeginTx(ctx, nil)
	if err != nil {
		return result, err
	}
	if err = CreateFinancialSnapshotTables(ctx, tx, fields); err != nil {
		tx.Rollback()
		return result, err
	}
	if err = tx.Commit(); err != nil {
		return result, err
	}
	runID, err := StartIngestRun(ctx, db, "alphalake", "financial_storage_rebuild", nil)
	if err != nil {
		return result, err
	}
	finished := false
	defer func() {
		if err != nil && !finished {
			status := IngestRunFailed
			if errors.Is(err, context.Canceled) {
				status = IngestRunCanceled
			}
			err = errors.Join(err, FinishIngestRun(context.WithoutCancel(ctx), db, runID, status, nil, err))
		}
	}()
	result.MetadataSeconds = time.Since(started).Seconds()
	financialStarted := time.Now()
	type input struct {
		id               int64
		path, hash, name string
		size             int64
	}
	rows, err = db.QueryContext(ctx, `SELECT DISTINCT a.artifact_id,a.local_path,a.sha256,a.source_locator,a.content_length FROM meta.artifact a WHERE a.source='tdx' AND (EXISTS(SELECT 1 FROM fundamental.provider_record_resolution r WHERE r.artifact_id=a.artifact_id) OR EXISTS(SELECT 1 FROM fundamental.provider_filing_link l WHERE l.provider_artifact_id=a.artifact_id)) ORDER BY a.artifact_id`)
	if err != nil {
		return result, err
	}
	var inputs []input
	for rows.Next() {
		var p input
		if err = rows.Scan(&p.id, &p.path, &p.hash, &p.name, &p.size); err != nil {
			rows.Close()
			return result, err
		}
		inputs = append(inputs, p)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return result, err
	}
	for _, p := range inputs {
		if p.id <= 0 || p.id > (math.MaxInt64-8191)/8192/65536-1 {
			return result, fmt.Errorf("artifact ID exceeds source locator bounds: %d", p.id)
		}
		if err = ctx.Err(); err != nil {
			return result, err
		}
		if filepath.IsAbs(p.path) || strings.HasPrefix(filepath.Clean(p.path), "..") {
			return result, fmt.Errorf("invalid archive path")
		}
		raw, e := os.ReadFile(filepath.Join(root, filepath.FromSlash(p.path)))
		if e != nil {
			return result, e
		}
		if int64(len(raw)) != p.size || fmt.Sprintf("%x", sha256.Sum256(raw)) != p.hash {
			return result, fmt.Errorf("archive integrity failure: %s", p.name)
		}
		pkg, e := financial.ParsePackage(filepath.Base(p.name), raw)
		if e != nil {
			return result, e
		}
		identities := map[string]int64{}
		rows, e = db.QueryContext(ctx, `SELECT r.provider_code,r.instrument_id FROM fundamental.provider_record_resolution r WHERE artifact_id=? AND status='resolved' UNION ALL SELECT l.provider_code,l.instrument_id FROM fundamental.provider_filing_link l WHERE l.provider_artifact_id=? AND l.instrument_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM fundamental.provider_record_resolution r WHERE r.artifact_id=l.provider_artifact_id AND r.provider_code=l.provider_code)`, p.id, p.id)
		if e != nil {
			return result, e
		}
		for rows.Next() {
			var c string
			var id int64
			if e = rows.Scan(&c, &id); e != nil {
				rows.Close()
				return result, e
			}
			identities[c] = id
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return result, e
		}
		conn, e := db.Conn(ctx)
		if e != nil {
			return result, e
		}
		e = func() error {
			defer conn.Close()
			if _, e := conn.ExecContext(ctx, "BEGIN"); e != nil {
				return e
			}
			committed := false
			defer func() {
				if !committed {
					_, _ = conn.ExecContext(context.WithoutCancel(ctx), "ROLLBACK")
				}
			}()
			if e := conn.Raw(func(raw any) error {
				app, e := duckdbgo.NewAppender(raw.(driver.Conn), PersistentCatalog, "fundamental", "source_record")
				if e != nil {
					return e
				}
				done := false
				defer func() {
					if !done {
						_ = app.Clear()
						_ = app.Close()
					}
				}()
				indexed := map[string]bool{}
				for i, r := range pkg.Records {
					resolved := identities[r.Code]
					if indexed[r.Code] {
						resolved = 0
					}
					indexed[r.Code] = true
					id := p.id*65536 + int64(i+1)
					if e = app.AppendRow(id, p.id, uint32(i+1), r.Code, r.MarketMarker, uint16(len(r.Fields)), r.ReportPeriod, nullableInstrument(resolved)); e != nil {
						return e
					}
				}
				if e = app.CloseWithCancel(ctx); e != nil {
					return e
				}
				done = true
				return nil
			}); e != nil {
				return e
			}
			excluded, e := UnmappedDuplicatePositions(ctx, conn)
			if e != nil {
				return e
			}
			seen := map[string]financial.Record{}
			batch := []IndexedFinancialRecord{}
			flush := func() error {
				if len(batch) == 0 {
					return nil
				}
				s, e := MaterializeFinancialSnapshotBatch(ctx, conn, runID, fields, batch)
				if e != nil {
					return e
				}
				result.StandardValues += int64(s.Materialized)
				result.Rejections += int64(s.Rejected)
				batch = batch[:0]
				return nil
			}
			for i, r := range pkg.Records {
				if prior, ok := seen[r.Code]; ok {
					if identities[r.Code] > 0 && (len(prior.Fields) != len(r.Fields) || prior.MarketMarker != r.MarketMarker) {
						return fmt.Errorf("conflicting resolved duplicate")
					}
					for j, v := range r.Fields {
						if identities[r.Code] > 0 && !excluded[j+1] && v.Bits != prior.Fields[j].Bits {
							return fmt.Errorf("conflicting resolved duplicate")
						}
					}
					continue
				}
				seen[r.Code] = r
				id := identities[r.Code]
				if id == 0 {
					continue
				}
				batch = append(batch, IndexedFinancialRecord{ID: p.id*65536 + int64(i+1), Revision: p.hash, Record: domain.ProviderFinancialRecord{InstrumentID: id, Provider: "tdx", ProviderCode: r.Code, ReportPeriod: r.ReportPeriod, ProviderFields: r.Fields, SourceFile: pkg.Filename, ArtifactID: p.id}})
				if len(batch) == 4096 {
					if e := flush(); e != nil {
						return e
					}
				}
			}
			if e := flush(); e != nil {
				return e
			}
			if _, e := conn.ExecContext(ctx, "COMMIT"); e != nil {
				return e
			}
			committed = true
			return nil
		}()
		if e != nil {
			return result, fmt.Errorf("rebuild %s: %w", p.name, e)
		}
		fmt.Fprintf(os.Stderr, "rebuilt %s: source_records=%d standard_values=%d rejected=%d\n", p.name, len(pkg.Records), result.StandardValues, result.Rejections)
		result.Packages++
		result.SourceRecords += int64(len(pkg.Records))
	}
	if err = InstallSnapshotQueries(ctx, db, fields); err != nil {
		return result, err
	}
	if err = db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.statement_snapshot`).Scan(&result.Snapshots); err != nil {
		return result, err
	}
	// Candidate deliberately has no old financial numeric tables and no claim to
	// be runtime-ready until the read/ingest contracts have been migrated.
	tx, err = db.BeginTx(ctx, nil)
	if err != nil {
		return result, err
	}
	if _, err = tx.ExecContext(ctx, `DELETE FROM meta.schema_version; INSERT INTO meta.schema_version(version,description) VALUES(53,'Financial wide-table rebuild with disclosure coverage')`); err != nil {
		tx.Rollback()
		return result, err
	}
	if err = tx.Commit(); err != nil {
		return result, err
	}
	if err = FinishIngestRun(ctx, db, runID, IngestRunCompleted, nil, nil); err != nil {
		return result, err
	}
	finished = true
	if _, err = db.ExecContext(ctx, "CHECKPOINT"); err != nil {
		return result, err
	}

	result.FinancialSeconds = time.Since(financialStarted).Seconds()
	result.Seconds = time.Since(started).Seconds()
	return result, nil
}
