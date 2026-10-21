package duckdb

import (
	"compress/gzip"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/source/macro"
)

func macroFixture(t *testing.T) macro.Snapshot {
	t.Helper()
	f, err := os.Open("../../source/macro/testdata/cpi.html.gz")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	z, err := gzip.NewReader(f)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := io.ReadAll(z)
	if err != nil {
		t.Fatal(err)
	}
	feed, _ := macro.FindFeed("cpi")
	s, err := macro.Parse(raw, feed, "https://www.stats.gov.cn/sj/zxfb/202610/t20261014_1965477.html")
	if err != nil {
		t.Fatal(err)
	}
	return s
}
func macroArchive(t *testing.T, ctx context.Context, db *sql.DB, s macro.Snapshot) (int64, int64) {
	t.Helper()
	var run, id int64
	if err := db.QueryRowContext(ctx, `INSERT INTO meta.ingest_run(source,dataset,status) VALUES (?,?,'running') RETURNING ingest_run_id`, s.Source, s.Dataset).Scan(&run); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `INSERT INTO meta.artifact(source,dataset,source_locator,fetched_at,sha256,content_length) VALUES (?,?,?,current_timestamp,?,1) RETURNING artifact_id`, s.Source, s.Dataset, s.URL, s.SHA256).Scan(&id); err != nil {
		t.Fatal(err)
	}
	return run, id
}
func TestMacroPublicationRevisionQueryAndReopen(t *testing.T) {
	ctx := t.Context()
	path := filepath.Join(t.TempDir(), "macro.duckdb")
	db, err := OpenInitialized(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { db.Close() }()
	s := macroFixture(t)
	run, artifact := macroArchive(t, ctx, db, s)
	id, inserted, err := PublishMacroData(ctx, db, run, artifact, s)
	if err != nil || !inserted {
		t.Fatal(id, inserted, err)
	}
	replay, inserted, err := PublishMacroData(ctx, db, run, artifact, s)
	if err != nil || inserted || replay != id {
		t.Fatal("not idempotent", replay, inserted, err)
	}
	rows, err := QueryMacroData(ctx, db, []string{"cn_cpi_yoy"}, "2026-09-30", "2026-09-30", true)
	if err != nil || len(rows) != 1 || rows[0].Value == nil || *rows[0].Value != "0.009000000000" || rows[0].Evidence.PublishedAt == nil {
		t.Fatal(rows, err)
	}
	if rows[0].PeriodStart != "2026-09-01" || rows[0].Evidence.FirstAcquiredAt.Before(*rows[0].Evidence.PublishedAt) {
		t.Fatal("report period and publication/acquisition mixed")
	}
	snapshot, err := ExportMacroReadSnapshot(ctx, db)
	if err != nil || len(snapshot.Series) != len(s.Series) || len(snapshot.Releases) != 1 || snapshot.Releases[0].ReleaseID != id {
		t.Fatal("compact macro snapshot", snapshot, err)
	}
	var points int
	for _, series := range snapshot.Series {
		for _, p := range series.Points {
			points++
			if p.ReleaseID != id || p.Locator == "" {
				t.Fatal(p)
			}
		}
	}
	if points != len(s.Observations) {
		t.Fatal("snapshot point count", points)
	}
	// A source revision is another vintage; the old point must remain queryable in storage.
	changed := s
	changed.Observations = append([]macro.Observation{}, s.Observations...)
	for i, o := range changed.Observations {
		if o.SeriesCode == "cn_cpi_yoy" {
			v := "0.010000000000"
			changed.Observations[i].RawValue = "1.0"
			changed.Observations[i].Value = &v
		}
	}
	hash := sha256.Sum256([]byte("synthetic revised source, not a real NBS publication"))
	changed.SHA256 = hex.EncodeToString(hash[:])
	run2, raw2 := macroArchive(t, ctx, db, changed)
	newID, _, err := PublishMacroData(ctx, db, run2, raw2, changed)
	if err != nil || newID == id {
		t.Fatal(newID, err)
	}
	rows, err = QueryMacroData(ctx, db, []string{"cn_cpi_yoy"}, "", "", true)
	if err != nil || len(rows) != 1 || *rows[0].Value != "0.010000000000" || rows[0].Evidence.ReleaseID != newID {
		t.Fatal(rows, err)
	}
	var old string
	if err = db.QueryRow(`SELECT CAST(value AS VARCHAR) FROM macro.observation WHERE release_id=? AND series_code='cn_cpi_yoy'`, id).Scan(&old); err != nil || old != "0.009000000000" {
		t.Fatal(old, err)
	}
	// Missing is a new disclosed state, not zero and not an excuse to resurrect the previous number.
	missing := changed
	missing.Observations = append([]macro.Observation{}, changed.Observations...)
	for i, o := range missing.Observations {
		if o.SeriesCode == "cn_cpi_yoy" {
			missing.Observations[i].RawValue = "—"
			missing.Observations[i].Value = nil
			missing.Observations[i].Status = "not_reported"
		}
	}
	hash = sha256.Sum256([]byte("synthetic missing source"))
	missing.SHA256 = hex.EncodeToString(hash[:])
	r3, a3 := macroArchive(t, ctx, db, missing)
	if _, _, err = PublishMacroData(ctx, db, r3, a3, missing); err != nil {
		t.Fatal(err)
	}
	older := s
	earlier := s.PublishedAt.Add(-24 * time.Hour)
	older.PublishedAt = &earlier
	hash = sha256.Sum256([]byte("synthetic older vintage acquired later"))
	older.SHA256 = hex.EncodeToString(hash[:])
	r4, a4 := macroArchive(t, ctx, db, older)
	if _, _, err = PublishMacroData(ctx, db, r4, a4, older); err != nil {
		t.Fatal(err)
	}
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = OpenInitialized(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	rows, err = QueryMacroData(ctx, db, []string{"cn_cpi_yoy"}, "", "", true)
	if err != nil || len(rows) != 1 || rows[0].Value != nil || rows[0].Status != "not_reported" {
		t.Fatal(rows, err)
	}
	if _, err = QueryMacroData(ctx, db, nil, "2026-10-01", "2026-01-01", false); err == nil {
		t.Fatal("reversed range accepted")
	}
	catalogue, err := ListMacroSeries(ctx, db)
	if err != nil || len(catalogue) != len(s.Series) {
		t.Fatal(catalogue, err)
	}
	// Same source bytes cannot acquire a different interpretation under an unchanged parser digest.
	if _, _, err = PublishMacroData(ctx, db, run, artifact, changed); err == nil {
		t.Fatal("changed archive identity accepted")
	}
	if _, _, err = PublishMacroData(ctx, db, run2, raw2, s); err == nil {
		t.Fatal("wrong source lineage accepted")
	}
	reinterpreted := changed
	reinterpreted.SHA256 = s.SHA256
	if _, _, err = PublishMacroData(ctx, db, run, artifact, reinterpreted); err == nil {
		t.Fatal("same published bytes reinterpreted without a new parser version")
	}
	if _, err = db.Exec(`UPDATE macro.observation SET value=0 WHERE value_status='not_reported'`); err == nil {
		t.Fatal("missing silently became zero")
	}
}

func TestUpgradeMacroPreservesExistingContent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "upgrade.duckdb")
	db, err := OpenInitialized(t.Context(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err = db.Exec(`DROP TABLE macro.observation;DROP TABLE macro.series;DROP SCHEMA macro;UPDATE meta.schema_version SET version=58;INSERT INTO fundamental.statement_snapshot(source_record_id,instrument_id,report_period,ingest_run_id,revenue)VALUES(1,1,'2026-06-30',1,123.5)`); err != nil {
		t.Fatal(err)
	}
	if err = Initialize(t.Context(), db); err == nil {
		t.Fatal("old schema silently accepted")
	}
	if err = UpgradeMacroData(t.Context(), db); err != nil {
		t.Fatal(err)
	}
	var amount string
	if err = db.QueryRow(`SELECT CAST(revenue AS VARCHAR) FROM fundamental.statement_snapshot`).Scan(&amount); err != nil || !strings.HasPrefix(amount, "123.5") {
		t.Fatal(amount, err)
	}
	if err = UpgradeMacroData(t.Context(), db); err == nil {
		t.Fatal("repeat migration accepted")
	}
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	db, err = OpenInitialized(t.Context(), path)
	if err != nil {
		t.Fatal(err)
	}
	var version int
	if err = db.QueryRow(`SELECT max(version) FROM meta.schema_version`).Scan(&version); err != nil || version != 59 {
		t.Fatal(version, err)
	}
	var nulls int
	if err = db.QueryRow(`SELECT count(*) FROM macro.observation`).Scan(&nulls); err != nil || nulls != 0 {
		t.Fatal(nulls, err)
	}
}
