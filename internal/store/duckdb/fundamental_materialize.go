package duckdb

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

type CanonicalFundamentalResult struct{ Candidates, Materialized, Inserted, Updated, Removed, Rejected int }

// FinancialArchiveRoot uses the caller's explicit workspace or the database's
// directory. A moved database must be accompanied by its immutable evidence.
func FinancialArchiveRoot(ctx context.Context, db *sql.DB) (string, error) {
	if root := os.Getenv("ALPHALAKE_WORKSPACE"); root != "" {
		return filepath.Abs(root)
	}
	var path string
	if err := db.QueryRowContext(ctx, `SELECT path FROM duckdb_databases() WHERE database_name=?`, PersistentCatalog).Scan(&path); err != nil {
		return "", fmt.Errorf("financial archive workspace required: %w", err)
	}
	return filepath.Dir(path), nil
}

func readFinancialArchive(root, path, name, hash string, size int64) (financial.Package, error) {
	if filepath.IsAbs(path) || strings.HasPrefix(filepath.Clean(path), "..") {
		return financial.Package{}, fmt.Errorf("invalid archive locator")
	}
	raw, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(path)))
	if err != nil {
		return financial.Package{}, err
	}
	if int64(len(raw)) != size || fmt.Sprintf("%x", sha256.Sum256(raw)) != hash {
		return financial.Package{}, fmt.Errorf("financial archive integrity failure: %s", path)
	}
	return financial.ParsePackage(filepath.Base(name), raw)
}

// MaterializeCanonicalFundamentals decodes each changed archive once. A package
// values, content signatures and published semantics commit together.
func MaterializeCanonicalFundamentals(ctx context.Context, db *sql.DB, runID int64, source string, requested ...string) (out CanonicalFundamentalResult, err error) {
	if source != "tdx" || runID <= 0 {
		return out, fmt.Errorf("TDX source and run required")
	}
	// Source-maintenance requests invalidate the whole package: one physical row
	// has one semantic catalogue version. No separate per-field writer remains.
	for _, field := range requested {
		if !sourcePositionName.MatchString(field) {
			return out, fmt.Errorf("invalid source maintenance field")
		}
	}
	fields, err := LoadSnapshotFields(ctx, db)
	if err != nil {
		return out, err
	}
	root, err := FinancialArchiveRoot(ctx, db)
	if err != nil {
		return out, err
	}
	var catalog string
	if err = db.QueryRowContext(ctx, `SELECT sha256(CAST(to_json(list(m ORDER BY source,provider_field,valid_from)) AS VARCHAR)) FROM (SELECT p.*,f.unit AS definition_unit,f.value_kind AS definition_kind,f.period_basis AS definition_basis FROM fundamental.provider_field p LEFT JOIN fundamental.field f USING(canonical_field)) m`).Scan(&catalog); err != nil {
		return out, err
	}
	type input struct {
		id                          int64
		path, name, hash, signature string
		size                        int64
	}
	rows, err := db.QueryContext(ctx, `SELECT a.artifact_id,a.local_path,a.source_locator,a.sha256,a.content_length,
 sha256(? || CAST(to_json(list(struct_pack(record_id:=r.source_record_id,instrument:=r.instrument_id,filing:=l.filing_id,status:=l.status,filing_instrument:=f.instrument_id,period:=f.report_period,announcement:=f.announcement_time,kind:=f.filing_type,resolution:=f.resolution_status) ORDER BY r.source_record_id)) AS VARCHAR)) AS signature
 FROM meta.artifact a JOIN fundamental.source_record r USING(artifact_id)
 LEFT JOIN fundamental.provider_filing_link l ON l.provider_artifact_id=r.artifact_id AND l.provider_code=r.provider_code
 LEFT JOIN fundamental.filing f USING(filing_id)
 WHERE a.source='tdx' GROUP BY a.artifact_id,a.local_path,a.source_locator,a.sha256,a.content_length
 HAVING signature IS DISTINCT FROM (SELECT input_signature FROM fundamental.materialization_state st WHERE st.artifact_id=a.artifact_id)
 ORDER BY a.artifact_id`, catalog)
	if err != nil {
		return out, err
	}
	var inputs []input
	for rows.Next() {
		var p input
		if err = rows.Scan(&p.id, &p.path, &p.name, &p.hash, &p.size, &p.signature); err != nil {
			rows.Close()
			return out, err
		}
		inputs = append(inputs, p)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return out, err
	}
	var catalogChanged bool
	if err = db.QueryRowContext(ctx, `WITH candidate AS (
 SELECT m.* FROM fundamental.provider_field m JOIN fundamental.field f ON f.canonical_field=m.canonical_field AND f.unit=m.unit AND f.value_kind=m.value_kind AND f.period_basis=m.period_basis)
 SELECT EXISTS((SELECT * FROM candidate EXCEPT SELECT * FROM fundamental.statement_field)
 UNION ALL (SELECT * FROM fundamental.statement_field EXCEPT SELECT * FROM candidate))`).Scan(&catalogChanged); err != nil {
		return out, err
	}
	conn, err := db.Conn(ctx)
	if err != nil {
		return out, err
	}
	defer conn.Close()
	if catalogChanged {
		if _, err = conn.ExecContext(ctx, `BEGIN`); err != nil {
			return out, err
		}
	}
	defer conn.ExecContext(context.WithoutCancel(ctx), `ROLLBACK`)
	for _, p := range inputs {
		before := out
		pkg, e := readFinancialArchive(root, p.path, p.name, p.hash, p.size)
		if e != nil {
			return out, e
		}
		rows, e = conn.QueryContext(ctx, `SELECT source_record_id,source_row,provider_code,instrument_id FROM fundamental.source_record WHERE artifact_id=? AND instrument_id IS NOT NULL ORDER BY source_row`, p.id)
		if e != nil {
			return out, e
		}
		var records []IndexedFinancialRecord
		for rows.Next() {
			var id, instrument int64
			var ordinal int
			var code string
			if e = rows.Scan(&id, &ordinal, &code, &instrument); e != nil {
				rows.Close()
				return out, e
			}
			if ordinal < 1 || ordinal > len(pkg.Records) || pkg.Records[ordinal-1].Code != code {
				rows.Close()
				return out, fmt.Errorf("archive row identity mismatch")
			}
			r := pkg.Records[ordinal-1]
			records = append(records, IndexedFinancialRecord{ID: id, Revision: p.hash, Record: domain.ProviderFinancialRecord{InstrumentID: instrument, Provider: "tdx", ProviderCode: code, ReportPeriod: r.ReportPeriod, ProviderFields: r.Fields, ArtifactID: p.id, SourceRow: uint32(ordinal)}})
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return out, e
		}
		if !catalogChanged {
			if _, err = conn.ExecContext(ctx, `BEGIN`); err != nil {
				return out, err
			}
		}
		for start := 0; start < len(records); start += 4096 {
			end := min(start+4096, len(records))
			r, e := MaterializeFinancialSnapshotBatch(ctx, conn, runID, fields, records[start:end])
			if e != nil {
				return out, fmt.Errorf("%s rows %d..%d: %w", p.name, start, end, e)
			}
			out.Candidates += r.Candidates
			out.Materialized += r.Materialized
			out.Inserted += r.Inserted
			out.Updated += r.Updated
			out.Removed += r.Removed
			out.Rejected += r.Rejected
		}
		if _, e := conn.ExecContext(ctx, `DELETE FROM fundamental.statement_snapshot s USING fundamental.source_record r WHERE s.source_record_id=r.source_record_id AND r.artifact_id=? AND r.instrument_id IS NULL`, p.id); e != nil {
			return out, e
		}
		if _, e := conn.ExecContext(ctx, `INSERT INTO fundamental.materialization_state VALUES (?,?,?,?,?) ON CONFLICT(artifact_id) DO UPDATE SET input_signature=excluded.input_signature,candidates=excluded.candidates,materialized=excluded.materialized,rejected=excluded.rejected`, p.id, p.signature, out.Candidates-before.Candidates, out.Materialized-before.Materialized, out.Rejected-before.Rejected); e != nil {
			return out, e
		}
		if !catalogChanged {
			if _, err = conn.ExecContext(ctx, `COMMIT`); err != nil {
				return out, err
			}
		}
	}
	// Stable semantics permit package commits; a catalogue change commits all
	// affected packages and its meaning together, or leaves the old publication.
	if catalogChanged {
		_, err = conn.ExecContext(ctx, `DELETE FROM fundamental.statement_field; INSERT INTO fundamental.statement_field SELECT m.* FROM fundamental.provider_field m JOIN fundamental.field f ON f.canonical_field=m.canonical_field AND f.unit=m.unit AND f.value_kind=m.value_kind AND f.period_basis=m.period_basis`)
	}
	if err != nil {
		return out, err
	}
	err = conn.QueryRowContext(ctx, `SELECT coalesce(sum(candidates),0),coalesce(sum(materialized),0),coalesce(sum(rejected),0) FROM fundamental.materialization_state`).Scan(&out.Candidates, &out.Materialized, &out.Rejected)
	if err == nil && catalogChanged {
		_, err = conn.ExecContext(ctx, `COMMIT`)
	}
	return out, err
}
