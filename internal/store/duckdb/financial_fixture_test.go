package duckdb

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/binary"
	"fmt"
	"github.com/yinhm/alphalake/internal/domain"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"
)

// seedStandardSnapshot is only a standard-query fixture, never source evidence.
func seedStandardSnapshot(t *testing.T, db *sql.DB, instrument int64, code, source, field, period, announced string, value any) int64 {
	t.Helper()
	ctx := t.Context()
	if !standardSnapshotName.MatchString(field) {
		t.Fatal(field)
	}
	var id int64
	if e := db.QueryRowContext(ctx, `SELECT coalesce(max(source_record_id),0)+1 FROM fundamental.source_record`).Scan(&id); e != nil {
		t.Fatal(e)
	}
	artifact := insertTestArtifact(t, ctx, db, fmt.Sprintf("fixture-%d", id), time.Now())
	for _, q := range []struct {
		sql  string
		args []any
	}{
		{`UPDATE meta.artifact SET source=? WHERE artifact_id=?`, []any{source, artifact}},
		{`INSERT INTO fundamental.source_record VALUES(?,?,1,?,0,584,CAST(? AS DATE),?)`, []any{id, artifact, code, period, instrument}},
		{`INSERT INTO fundamental.statement_snapshot(source_record_id,instrument_id,source_filing_id,report_period,announcement_time,ingest_run_id,"` + field + `") VALUES(?,?,1,CAST(? AS DATE),CAST(? AS TIMESTAMPTZ),1,?)`, []any{id, instrument, period, announced, value}},
	} {
		if _, e := db.ExecContext(ctx, q.sql, q.args...); e != nil {
			t.Fatal(e)
		}
	}

	if source != "tdx" {
		if _, e := db.ExecContext(ctx, `INSERT INTO fundamental.statement_field SELECT * REPLACE(? AS source) FROM fundamental.statement_field WHERE source='tdx' AND NOT EXISTS(SELECT 1 FROM fundamental.statement_field WHERE source=?)`, source, source); e != nil {
			t.Fatal(e)
		}
	}
	var fact int64
	if e := db.QueryRowContext(ctx, `SELECT fact_id FROM fundamental.financial_observations(NULL,NULL,NULL,NULL) WHERE source_record_id=? AND canonical_field=?`, id, field).Scan(&fact); e != nil {
		t.Fatal(e)
	}
	return fact
}

// archiveFinancialFixture writes a genuine parseable ZIP, registers its SHA and
// locator, and supplies the resolved record. Tests can then tamper with the file.
func archiveFinancialFixture(t *testing.T, ctx context.Context, db *sql.DB, code string, instrument int64, period time.Time, values map[int]float32) (domain.ProviderFinancialRecord, string, string) {
	t.Helper()
	count := 584
	for n := range values {
		if n > count {
			count = n
		}
	}
	data := make([]byte, 31+count*4)
	date, _ := strconv.Atoi(period.Format("20060102"))
	binary.LittleEndian.PutUint32(data[2:6], uint32(date))
	binary.LittleEndian.PutUint16(data[6:8], 1)
	binary.LittleEndian.PutUint32(data[12:16], uint32(count*4))
	copy(data[20:26], code)
	binary.LittleEndian.PutUint32(data[27:31], 31)
	fields := make([]domain.ProviderFloat32, count)
	for n, v := range values {
		bits := math.Float32bits(v)
		binary.LittleEndian.PutUint32(data[31+(n-1)*4:], bits)
		fields[n-1] = domain.ProviderFloat32{Bits: bits, Value: float64(v)}
	}
	var b bytes.Buffer
	z := zip.NewWriter(&b)
	name := "gpcw" + period.Format("20060102") + ".zip"
	w, e := z.Create(name[:len(name)-4] + ".dat")
	if e != nil {
		t.Fatal(e)
	}
	if _, e = w.Write(data); e != nil {
		t.Fatal(e)
	}
	if e = z.Close(); e != nil {
		t.Fatal(e)
	}
	sha := fmt.Sprintf("%x", sha256.Sum256(b.Bytes()))
	root, e := FinancialArchiveRoot(ctx, db)
	if e != nil {
		t.Fatal(e)
	}
	path := filepath.Join(root, sha+".zip")
	if e = os.WriteFile(path, b.Bytes(), 0600); e != nil {
		t.Fatal(e)
	}
	var artifact int64
	e = db.QueryRowContext(ctx, `INSERT INTO meta.artifact(source,dataset,source_locator,fetched_at,sha256,content_length,local_path) VALUES('tdx','professional_financial',?,now(),?,?,?) RETURNING artifact_id`, name, sha, b.Len(), filepath.Base(path)).Scan(&artifact)
	if e != nil {
		t.Fatal(e)
	}
	r := domain.ProviderFinancialRecord{InstrumentID: instrument, Provider: "tdx", ProviderCode: code, ReportPeriod: period, ArtifactID: artifact, SourceFile: name, SourceRow: 1, ProviderFields: fields}
	return r, sha, path
}
