package ingest

import (
	"bytes"
	"compress/gzip"
	"encoding/json"
	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestMarketCapitalArchiveReplay(t *testing.T) {
	python := os.Getenv("ALPHALAKE_TEST_PYTHON")
	if python == "" {
		t.Skip("CI installs pypdf and Poppler explicitly")
	}
	ctx := t.Context()
	dir := t.TempDir()
	db, e := duckstore.OpenAndMigrate(ctx, filepath.Join(dir, "market.duckdb"))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	_, e = db.ExecContext(ctx, `INSERT INTO ref.instrument(instrument_type,exchange_mic,currency) VALUES ('equity','XSHE','CNY'),('equity','XSHG','CNY');
 INSERT INTO ref.instrument_identifier(instrument_id,provider,identifier_type,identifier_value,valid_from) VALUES (1,'tdx','symbol','sz300866','2026-09-05'),(2,'tdx','symbol','sh600519','2026-09-05')`)
	if e != nil {
		t.Fatal(e)
	}
	shareIDs := map[string]int64{}
	for _, tc := range []struct {
		code, path string
		count      int
	}{{"300866", "../source/capital/testdata/anker-20260831.pdf", 6}, {"600519", "testdata/moutai-valuation-2026/1225475868.pdf", 3}} {
		raw, e := os.ReadFile(tc.path)
		if e != nil {
			t.Fatal(e)
		}
		calls := 0
		bad := false
		client := &http.Client{Transport: countryTransport(func(r *http.Request) (*http.Response, error) {
			calls++
			b := raw
			if bad {
				b = []byte("bad PDF")
			}
			return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(b)), Header: make(http.Header)}, nil
		})}
		opts := ReferenceOptions{Python: python, Script: "../source/capital/parse.py", Client: client}
		out, e := SyncShareClasses(ctx, db, dir, tc.code, opts)
		if e != nil || !out.Inserted || out.Observations != tc.count {
			t.Fatal(out, e)
		}
		shareIDs[tc.code] = out.ReleaseID
		bad = true
		failed, e := SyncShareClasses(ctx, db, dir, tc.code, opts)
		if e == nil || failed.ReleaseID != 0 {
			t.Fatal("bad PDF published", failed)
		}
		opts.Offline = true
		again, e := SyncShareClasses(ctx, db, dir, tc.code, opts)
		if e != nil || again.Inserted || again.ReleaseID != out.ReleaseID || calls != 2 {
			t.Fatal(again, e, calls)
		}
		_, e = db.ExecContext(ctx, `UPDATE market.share_count_observation SET value=value+1 WHERE release_id=? AND share_basis='outstanding'`, out.ReleaseID)
		if e != nil {
			t.Fatal(e)
		}
		if _, e = SyncShareClasses(ctx, db, dir, tc.code, opts); e == nil {
			t.Fatal("tampered published share accepted")
		}
		_, e = db.ExecContext(ctx, `UPDATE market.share_count_observation SET value=value-1 WHERE release_id=? AND share_basis='outstanding'`, out.ReleaseID)
		if e != nil {
			t.Fatal(e)
		}
	}

	var fundingIDs []int64
	for _, event := range []string{"ipo", "greenshoe"} {
		body, err := os.ReadFile("../source/proceeds/testdata/" + event + ".pdf")
		if err != nil {
			t.Fatal(err)
		}
		calls := 0
		bad := false
		options := ReferenceOptions{Python: python, Script: "../source/proceeds/parse.py", Client: &http.Client{Transport: countryTransport(func(r *http.Request) (*http.Response, error) {
			calls++
			b := body
			if bad {
				b = []byte("bad PDF")
			}
			return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(b)), Header: make(http.Header)}, nil
		})}}
		result, err := SyncEquityProceeds(ctx, db, dir, event, options)
		if err != nil || !result.Inserted || result.Observations != 1 {
			t.Fatal(result, err)
		}
		fundingIDs = append(fundingIDs, result.ReleaseID)
		bad = true
		if _, err = SyncEquityProceeds(ctx, db, dir, event, options); err == nil {
			t.Fatal("bad proceeds PDF accepted")
		}
		options.Offline = true
		replay, err := SyncEquityProceeds(ctx, db, dir, event, options)
		if err != nil || replay.Inserted || replay.ReleaseID != result.ReleaseID || calls != 2 {
			t.Fatal(replay, err, calls)
		}
		if _, err = db.ExecContext(ctx, `UPDATE market.equity_proceeds_observation SET net_proceeds=net_proceeds+1 WHERE release_id=?`, result.ReleaseID); err != nil {
			t.Fatal(err)
		}
		if _, err = SyncEquityProceeds(ctx, db, dir, event, options); err == nil {
			t.Fatal("changed proceeds accepted")
		}
		if _, err = db.ExecContext(ctx, `UPDATE market.equity_proceeds_observation SET net_proceeds=net_proceeds-1 WHERE release_id=?`, result.ReleaseID); err != nil {
			t.Fatal(err)
		}
	}
	raw, e := os.ReadFile("../source/hkex/testdata/d260908e.html.gz")
	if e != nil {
		t.Fatal(e)
	}
	gz, e := gzip.NewReader(bytes.NewReader(raw))
	if e != nil {
		t.Fatal(e)
	}
	html, e := io.ReadAll(gz)
	if e != nil {
		t.Fatal(e)
	}
	gz.Close()
	calls := 0
	client := &http.Client{Transport: countryTransport(func(r *http.Request) (*http.Response, error) {
		calls++
		return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(html)), Header: make(http.Header)}, nil
	})}
	opts := ReferenceOptions{Python: python, Script: "../source/hkex/parse.py", Client: client}
	out, e := SyncHKEXQuote(ctx, db, dir, "2026-09-08", opts)
	if e != nil || !out.Inserted {
		t.Fatal(out, e)
	}
	var close string
	if e = db.QueryRowContext(ctx, `SELECT CAST(close AS VARCHAR) FROM market.listing_close_observation`).Scan(&close); e != nil || close != "128.200000" {
		t.Fatal(close, e)
	}
	opts.Offline = true
	again, e := SyncHKEXQuote(ctx, db, dir, "2026-09-08", opts)
	if e != nil || again.Inserted || calls != 1 {
		t.Fatal(again, e, calls)
	}
	if _, e = SyncHKEXQuote(ctx, db, dir, "2026-09-07", opts); e == nil {
		t.Fatal("wrong-day archive accepted")
	}

	hkID := out.ReleaseID
	fxraw, e := os.ReadFile("../source/safe/testdata/rates.html")
	if e != nil {
		t.Fatal(e)
	}
	opts = ReferenceOptions{Python: python, Script: "../source/safe/parse.py", Client: &http.Client{Transport: countryTransport(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(fxraw)), Header: make(http.Header)}, nil
	})}}
	fxout, e := SyncHKDCNY(ctx, db, dir, opts)
	if e != nil {
		t.Fatal(e)
	}
	day, _ := time.Parse("2006-01-02", "2026-09-08")
	run, e := duckstore.StartIngestRun(ctx, db, "tdx", "daily_ohlcv", nil)
	if e != nil {
		t.Fatal(e)
	}
	// Synthetic A-price inputs test joins and rejection gates. Live CLI acceptance is separate.
	for id := int64(1); id <= 2; id++ {
		price := 100.0
		if id == 2 {
			price = 1300
		}
		if e = duckstore.UpsertDailyBarsForRun(ctx, db, run, []domain.DailyBar{{InstrumentID: id, TradeDate: day, Open: price, High: price + 10, Low: price - 10, Close: price, Volume: 100, Amount: 10000, Source: "tdx"}}); e != nil {
			t.Fatal(e)
		}
	}
	if e = duckstore.FinishIngestRun(ctx, db, run, duckstore.IngestRunCompleted, nil, nil); e != nil {
		t.Fatal(e)
	}
	// Freeze only this synthetic test clock. DuckDB requires temporarily unlinking FK children when updating checked parent rows.
	_, e = db.ExecContext(ctx, `CREATE TEMP TABLE saved_release_links AS SELECT * FROM meta.dataset_release_artifact;
 DELETE FROM meta.dataset_release_artifact;
 UPDATE meta.dataset_release SET available_at=TIMESTAMPTZ '2026-09-09 12:00:00+00',first_seen_at=TIMESTAMPTZ '2026-09-09 12:00:00+00',recorded_at=TIMESTAMPTZ '2026-09-09 12:01:00+00';
 INSERT INTO meta.dataset_release_artifact SELECT * FROM saved_release_links;
 UPDATE meta.ingest_run SET started_at=TIMESTAMPTZ '2026-09-09 12:00:00+00',finished_at=TIMESTAMPTZ '2026-09-09 12:10:00+00';
 CREATE TEMP TABLE saved_listing_identifiers AS SELECT * FROM ref.listing_identifier;
 DELETE FROM ref.listing_identifier;
 UPDATE ref.listing SET recorded_at=TIMESTAMPTZ '2026-09-09 12:01:00+00';
 INSERT INTO ref.listing_identifier SELECT * FROM saved_listing_identifiers;
 UPDATE ref.listing_identifier SET recorded_at=TIMESTAMPTZ '2026-09-09 12:01:00+00';
 UPDATE market.daily_observation SET recorded_at=TIMESTAMPTZ '2026-09-09 12:01:00+00'`)
	if e != nil {
		t.Fatal(e)
	}
	at := time.Date(2026, 9, 9, 16, 0, 0, 0, time.UTC)
	for _, code := range []string{"300866", "600519"} {
		h, f := hkID, fxout.ReleaseID
		if code == "600519" {
			h, f = 0, 0
		}
		packet, e := duckstore.ExportMarketCapital(ctx, db, code, day, at, shareIDs[code], h, f)
		if e != nil {
			t.Fatal(code, e)
		}
		if output := os.Getenv("ALPHALAKE_MARKET_EXPORT_DIR"); output != "" {
			if e = os.MkdirAll(output, 0755); e != nil {
				t.Fatal(e)
			}
			b, e := json.MarshalIndent(packet, "", "  ")
			if e != nil {
				t.Fatal(e)
			}
			if e = os.WriteFile(filepath.Join(output, code+".json"), b, 0600); e != nil {
				t.Fatal(e)
			}
		}
		if _, e = duckstore.ExportMarketCapital(ctx, db, code, day.AddDate(0, 0, -1), at, shareIDs[code], h, f); e == nil {
			t.Fatal("missing exact date accepted")
		}
		if _, e = duckstore.ExportMarketCapital(ctx, db, code, day, day, shareIDs[code], h, f); e == nil {
			t.Fatal("future available evidence accepted")
		}
	}

	fundingPacket, err := duckstore.ExportMarketCapital(ctx, db, "300866", day, at, shareIDs["300866"], hkID, fxout.ReleaseID, fundingIDs...)
	if err != nil {
		t.Fatal(err)
	}
	if output := os.Getenv("ALPHALAKE_MARKET_EXPORT_DIR"); output != "" {
		b, err := json.MarshalIndent(fundingPacket, "", "  ")
		if err != nil {
			t.Fatal(err)
		}
		if err = os.WriteFile(filepath.Join(output, "300866-funding.json"), b, 0600); err != nil {
			t.Fatal(err)
		}
	}
	if _, err = duckstore.ExportMarketCapital(ctx, db, "300866", day, at, shareIDs["300866"], hkID, fxout.ReleaseID, fundingIDs[0]); err == nil {
		t.Fatal("partial funding pair accepted")
	}
	if _, err = duckstore.ExportMarketCapital(ctx, db, "300866", day, at, shareIDs["300866"], hkID, fxout.ReleaseID, fundingIDs[1], fundingIDs[0]); err == nil {
		t.Fatal("wrong funding release role accepted")
	}
	_, e = db.ExecContext(ctx, `DELETE FROM market.share_count_observation WHERE release_id=? AND share_basis='treasury'`, shareIDs["300866"])
	if e != nil {
		t.Fatal(e)
	}
	if _, e = duckstore.ExportMarketCapital(ctx, db, "300866", day, at, shareIDs["300866"], hkID, fxout.ReleaseID); e == nil {
		t.Fatal("incomplete classes accepted")
	}
	// All publication evidence, company and class associations survive reopening.
	db.Close()
	db, e = duckstore.Open(ctx, filepath.Join(dir, "market.duckdb"))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	var n int
	if e = db.QueryRowContext(ctx, `SELECT count(*) FROM market.equity_proceeds_observation`).Scan(&n); e != nil || n != 2 {
		t.Fatal(n, e)
	}
	if e = db.QueryRowContext(ctx, `SELECT count(*) FROM market.share_count_observation`).Scan(&n); e != nil || n != 7 {
		t.Fatal(n, e)
	}
	if e = db.QueryRowContext(ctx, `SELECT count(*) FROM meta.ingest_run WHERE status='failed'`).Scan(&n); e != nil || n != 9 {
		t.Fatal(n, e)
	}
}
