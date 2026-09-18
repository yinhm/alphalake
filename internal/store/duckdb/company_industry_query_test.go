package duckdb

import (
	"compress/gzip"
	"context"
	"io"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/source/damodaran"
)

func TestCompanyIndustryProjectionRealSourceAndIdentityBoundaries(t *testing.T) {
	python := os.Getenv("ALPHALAKE_TEST_PYTHON")
	if python == "" {
		t.Skip("CI requires archived company parser")
	}
	ctx := context.Background()
	dir := t.TempDir()
	db, err := OpenAndMigrate(ctx, filepath.Join(dir, "companies.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	f, err := os.Open("../../source/damodaran/testdata/indname.xls.gz")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	z, err := gzip.NewReader(f)
	if err != nil {
		t.Fatal(err)
	}
	defer z.Close()
	raw, err := io.ReadAll(z)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "source.xls")
	if err = os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	snapshot, hash, err := damodaran.ParseCompanyIndustries(ctx, python, filepath.Join("../../..", damodaran.CompanyIndustryScript), path)
	if err != nil {
		t.Fatal(err)
	}
	run, err := StartIngestRun(ctx, db, damodaran.Source, damodaran.CompanyIndustryDataset, nil)
	if err != nil {
		t.Fatal(err)
	}
	// UTC前一日16时就是中国本日零点；来源日期仍然未知。
	seen := time.Now().UTC().Truncate(24 * time.Hour).Add(-8 * time.Hour)
	stored, err := artifact.Persist(ctx, db, filepath.Join(dir, "raw"), artifact.Input{Source: damodaran.Source, Dataset: damodaran.CompanyIndustryDataset, SourceLocator: damodaran.CompanyIndustryURL, FetchedAt: seen, MediaType: "application/vnd.ms-excel", ParserVersion: damodaran.CompanyIndustryParserVersion, IngestRunID: &run, Content: raw})
	if err != nil {
		t.Fatal(err)
	}
	release, _, err := PublishCompanyIndustries(ctx, db, run, stored.ArtifactID, hash, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	if err = FinishIngestRun(ctx, db, run, IngestRunCompleted, nil, nil); err != nil {
		t.Fatal(err)
	}
	day := seen.Add(8 * time.Hour).Format("2006-01-02")
	_, err = db.ExecContext(ctx, `INSERT INTO core.instrument(instrument_id,instrument_type,exchange_mic,currency) VALUES
 (1,'equity','XSHG','CNY'),(9007199254740993,'equity','XSHE','CNY'),(2,'equity','XSHG','CNY'),(3,'equity','XSHG','CNY'),(4,'equity','XSHG','CNY'),(5,'etf','XSHE','CNY'),(6,'equity','XSHG','CNY')`)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.ExecContext(ctx, `INSERT INTO core.instrument_identifier(instrument_id,provider,identifier_type,identifier_value,valid_to) VALUES (9007199254740993,'tdx','symbol','sz300866',CAST(? AS DATE)),(1,'tdx','symbol','sh603288',CAST(? AS DATE))`, day, day)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.ExecContext(ctx, `INSERT INTO core.instrument_identifier(instrument_id,provider,identifier_type,identifier_value,valid_from) VALUES
 (9007199254740993,'tdx','symbol','sz300866',CAST(? AS DATE)),(2,'tdx','symbol','sh600519',NULL),(2,'tdx','symbol','sh600519',CAST(? AS DATE)),
 (3,'tdx','symbol','sh603288',CAST(? AS DATE)),(6,'tdx','symbol','sh601390',CAST(? AS DATE)+1),(4,'tdx','symbol','sz300124',NULL),(5,'tdx','symbol','sz002959',NULL)`, day, day, day, day)
	if err != nil {
		t.Fatal(err)
	}
	read := func(at time.Time) (map[int64][]any, map[string]any, error) {
		tx, e := db.BeginTx(ctx, nil)
		if e != nil {
			return nil, nil, e
		}
		defer tx.Rollback()
		return companyIndustriesAsOf(ctx, tx, at)
	}
	early, info, err := read(seen.Add(-time.Nanosecond))
	if err != nil || len(early) != 0 || info["status"] != "not_published" {
		t.Fatal("future source leaked", early, info, err)
	}
	at := time.Now().Add(time.Minute)
	members, info, err := read(at)
	if err != nil {
		t.Fatal(err)
	}
	if len(members) != 1 || len(members[9007199254740993]) != 1 || info["identity_date"] != day {
		t.Fatal("identity date/precision", members, info)
	}
	m := members[9007199254740993][0].(map[string]any)
	if m["node_code"] != "Computers/Peripherals" || m["source_release_id"] != release || m["source_ticker"] != "SZSE:300866" || m["artifact_sha256"] != snapshot.SHA256 {
		t.Fatal("missing source lineage", m)
	}
	counts := info["status_counts"].(map[string]int)
	if counts["ambiguous_identity"] != 1 || counts["identity_metadata_conflict"] != 1 || counts["outside_A_equity_scope"] != 1 || counts["resolved_A_equity"] != 1 || counts["unresolved"] != 5095 || counts["ambiguous_undated_source_identity"] != 1 {
		t.Fatal(counts)
	}
	// 合法JSON、合法名称仍不能篡改已发布快照中的语义内容。
	_, err = db.ExecContext(ctx, `UPDATE reference.security_industry SET raw_payload=CAST(json_merge_patch(CAST(raw_payload AS JSON),' {"name":"changed source name"}') AS VARCHAR) WHERE exchange_ticker='SZSE:300866'`)
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err = read(at); err == nil {
		t.Fatal("modified source payload accepted")
	}
	readiness, err := ExportValuationReadiness(ctx, db, time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC), at)
	if err != nil {
		t.Fatal(err)
	}
	if readiness["company_industry_reference"].(map[string]any)["status"] != "blocked_invalid_reference" {
		t.Fatal("invalid reference not isolated")
	}
}
