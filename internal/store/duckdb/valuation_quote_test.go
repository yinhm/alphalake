package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"github.com/yinhm/alphalake/internal/domain"
	"path/filepath"
	"testing"
	"time"
)

func TestValuationQuoteObservations(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "quotes.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	id, err := UpsertInstrument(ctx, db, domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY"}, domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600519"})
	if err != nil {
		t.Fatal(err)
	}
	day := time.Date(2026, 9, 3, 0, 0, 0, 0, time.UTC)
	bar := domain.DailyBar{InstrumentID: id, TradeDate: day, Open: 10, High: 12, Low: 9, Close: 10, Volume: 100, Amount: 1000, Source: "tdx"}
	// 无运行血缘的写入必须拒绝，也不能生成估值证据。
	if err = UpsertDailyBarsForRun(ctx, db, 0, []domain.DailyBar{bar}); err == nil {
		t.Fatal("untracked write accepted")
	}
	if _, err = ExportValuationQuote(ctx, db, "sh600519", day, time.Now()); err == nil {
		t.Fatal("untracked quote accepted")
	}
	run, _ := StartIngestRun(ctx, db, "tdx", "daily_ohlcv", nil)
	write := func() {
		t.Helper()
		if err := UpsertDailyBarsForRun(ctx, db, run, []domain.DailyBar{bar}); err != nil {
			t.Fatal(err)
		}
	}
	write()
	write()
	var count int
	db.QueryRowContext(ctx, `SELECT count(*) FROM market.daily_observation`).Scan(&count)
	if count != 1 {
		t.Fatal("same-run replay", count)
	}
	if _, err = ExportValuationQuote(ctx, db, "sh600519", day, time.Now()); err == nil {
		t.Fatal("running acquisition accepted")
	}
	if err = FinishIngestRun(ctx, db, run, IngestRunCompleted, nil, nil); err != nil {
		t.Fatal(err)
	}
	var cutoff time.Time
	db.QueryRowContext(ctx, `SELECT finished_at FROM meta.ingest_run WHERE ingest_run_id=?`, run).Scan(&cutoff)
	first, err := ExportValuationQuote(ctx, db, "sh600519", day, cutoff)
	if err != nil {
		t.Fatal(err)
	}
	if first["quote"].(map[string]any)["close"] != "10.000000" || first["market_cap"] != nil {
		t.Fatal(first)
	}
	run, _ = StartIngestRun(ctx, db, "tdx", "daily_ohlcv", nil)
	bar.Close = 11
	write()
	bar.Close = 10
	write()
	db.QueryRowContext(ctx, `SELECT count(*) FROM market.daily_observation`).Scan(&count)
	if count != 3 {
		t.Fatal("A-B-A revisions lost", count)
	}
	FinishIngestRun(ctx, db, run, IngestRunCompleted, nil, nil)
	old, err := ExportValuationQuote(ctx, db, "sh600519", day, cutoff)
	if err != nil || old["quote"].(map[string]any)["observation_id"] != first["quote"].(map[string]any)["observation_id"] {
		t.Fatal("historical version changed", err)
	}
	latest, err := ExportValuationQuote(ctx, db, "sh600519", day, time.Now())
	if err != nil || latest["quote"].(map[string]any)["close"] != "10.000000" {
		t.Fatal("latest revision", err)
	}
	// An intraday capture must not become a final close simply as time passes.
	db.ExecContext(ctx, `UPDATE meta.ingest_run SET started_at=? WHERE ingest_run_id IN (SELECT ingest_run_id FROM market.daily_observation)`, day)
	if _, err = ExportValuationQuote(ctx, db, "sh600519", day, time.Now()); err == nil {
		t.Fatal("intraday capture promoted")
	}
	// Projection and observation history roll back together.
	before := count
	err = withDailyWriteTransaction(ctx, db, func(conn *sql.Conn) error {
		bar.Close = 12
		if err := mergeDailyBarsOnConn(ctx, conn, []domain.DailyBar{bar}, run); err != nil {
			return err
		}
		return errors.New("force rollback")
	})
	if err == nil {
		t.Fatal("rollback missing")
	}
	db.QueryRowContext(ctx, `SELECT count(*) FROM market.daily_observation`).Scan(&count)
	if count != before {
		t.Fatal("orphan observation")
	}
	var close float64
	db.QueryRowContext(ctx, `SELECT close FROM market.ohlcv_daily`).Scan(&close)
	if close != 10 {
		t.Fatal("projection rollback")
	}
}
