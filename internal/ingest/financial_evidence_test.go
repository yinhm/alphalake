package ingest

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"github.com/yinhm/alphalake/internal/domain"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"
)

// sourceEvidenceDB is test-only: source assertions re-read hash-verified ZIPs.
// No production source EAV table or compatibility view is installed.
func sourceEvidenceDB(t *testing.T, ctx context.Context, db *sql.DB) *sql.DB {
	t.Helper()
	rows, e := db.QueryContext(ctx, `SELECT provider_code,report_period,min(instrument_id) FROM fundamental.source_record WHERE instrument_id IS NOT NULL GROUP BY provider_code,report_period`)
	if e != nil {
		t.Fatal(e)
	}
	type key struct {
		code       string
		period     time.Time
		instrument int64
	}
	var keys []key
	for rows.Next() {
		var k key
		if e = rows.Scan(&k.code, &k.period, &k.instrument); e != nil {
			t.Fatal(e)
		}
		keys = append(keys, k)
	}
	if e = rows.Err(); e != nil {
		t.Fatal(e)
	}
	rows.Close()
	var payload []map[string]any
	for _, k := range keys {
		out, e := store.ExportSourceFinancialData(ctx, db, k.code, k.period)
		if e != nil {
			t.Fatal(e)
		}
		for _, r := range out.Observations {
			var value any
			if r.Evidence.Bits != nil {
				v := float64(math.Float32frombits(*r.Evidence.Bits))
				if !math.IsNaN(v) && !math.IsInf(v, 0) {
					value = v
				}
			}
			payload = append(payload, map[string]any{"id": r.ProviderFactID, "instrument": k.instrument, "code": k.code, "period": k.period.Format("2006-01-02"), "field": r.Evidence.Field, "value": value, "bits": r.Evidence.Bits, "artifact": r.ArtifactID, "revision": r.Revision})
		}
	}
	raw, e := json.Marshal(payload)
	if e != nil {
		t.Fatal(e)
	}
	_, e = db.ExecContext(ctx, `CREATE OR REPLACE TEMP TABLE _source_evidence AS SELECT CAST(value->>'id' AS BIGINT) AS provider_fact_id,CAST(value->>'instrument' AS BIGINT) AS instrument_id,'tdx' AS source,value->>'code' AS provider_code,CAST(value->>'period' AS DATE) AS report_period,value->>'field' AS provider_field,CAST(value->>'value' AS DOUBLE) AS value,CAST(value->>'bits' AS UBIGINT) AS value_float32_bits,CAST(value->>'artifact' AS BIGINT) AS artifact_id,value->>'revision' AS revision_key FROM json_each(CAST(? AS JSON))`, string(raw))
	if e != nil {
		t.Fatal(e)
	}
	return db
}

// seedFinancialArchive writes a genuine parseable ZIP, registers its SHA and
// locator, and supplies the resolved record. Tests can then tamper with the file.
func seedFinancialArchive(t *testing.T, ctx context.Context, db *sql.DB, code string, instrument int64, period time.Time, values map[int]float32) (domain.ProviderFinancialRecord, string, string) {
	t.Helper()
	count := 1
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
	root, e := store.FinancialArchiveRoot(ctx, db)
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

func copyFinancialTestArchives(t *testing.T, db *sql.DB, sourceRoot, targetRoot string) {
	t.Helper()
	rows, e := db.Query(`SELECT DISTINCT local_path FROM meta.artifact WHERE local_path IS NOT NULL AND local_path<>''`)
	if e != nil {
		t.Fatal(e)
	}
	defer rows.Close()
	for rows.Next() {
		var path string
		if e = rows.Scan(&path); e != nil {
			t.Fatal(e)
		}
		raw, e := os.ReadFile(filepath.Join(sourceRoot, path))
		if e != nil {
			t.Fatal(e)
		}
		destination := filepath.Join(targetRoot, path)
		if e = os.MkdirAll(filepath.Dir(destination), 0700); e != nil {
			t.Fatal(e)
		}
		if e = os.WriteFile(destination, raw, 0600); e != nil {
			t.Fatal(e)
		}
	}
	if e = rows.Err(); e != nil {
		t.Fatal(e)
	}
}
