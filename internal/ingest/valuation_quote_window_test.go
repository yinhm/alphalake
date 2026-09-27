package ingest

import (
	"context"
	"github.com/yinhm/alphalake/internal/domain"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
	"path/filepath"
	"testing"
	"time"
)

type windowSource struct {
	bad   bool
	calls int
}

func (f *windowSource) StockDailyBarsWindow(_ context.Context, id int64, _ string, start, end time.Time) ([]domain.DailyBar, error) {
	f.calls++
	day := end
	if f.bad {
		day = start.AddDate(0, 0, -1)
	}
	return []domain.DailyBar{{InstrumentID: id, TradeDate: day, Open: 10, High: 11, Low: 9, Close: 10, Volume: 100, Amount: 1000, Source: "tdx"}}, nil
}
func TestQuoteWindowDoesNotAdvanceDailyHistoryAndRejectsInvalidScope(t *testing.T) {
	ctx := t.Context()
	db, e := store.OpenInitialized(ctx, filepath.Join(t.TempDir(), "test.duckdb"))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	id, e := store.UpsertInstrument(ctx, db, domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "test"}, domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600004"})
	if e != nil {
		t.Fatal(e)
	}
	day := time.Date(2020, 6, 30, 0, 0, 0, 0, time.UTC)
	_, e = db.Exec(`INSERT INTO meta.checkpoint(source,dataset,checkpoint_key,checkpoint_value) VALUES('tdx','daily_ohlcv','sentinel','2019-01-01')`)
	if e != nil {
		t.Fatal(e)
	}
	f := &windowSource{}
	before := time.Now()
	r, e := SyncValuationQuoteWindow(ctx, db, f, []string{"sh600004"}, day)
	if e != nil || r.Written != 1 {
		t.Fatal(r, e)
	}
	if _, found, e := store.LatestDailyDate(ctx, db, id, "tdx"); e != nil || found {
		t.Fatal("window falsely advanced full history", e)
	}
	var checkpoint string
	if e = db.QueryRow(`SELECT checkpoint_value FROM meta.checkpoint WHERE checkpoint_key='sentinel'`).Scan(&checkpoint); e != nil || checkpoint != "2019-01-01" {
		t.Fatal(checkpoint, e)
	}
	if _, e = store.ExportValuationQuote(ctx, db, "sh600004", day, before); e == nil {
		t.Fatal("new observation leaked into old cutoff")
	}
	if _, e = store.ExportValuationQuote(ctx, db, "sh600004", day, time.Now()); e != nil {
		t.Fatal(e)
	}
	f.bad = true
	r, e = SyncValuationQuoteWindow(ctx, db, f, []string{"sh600004", "sh999999"}, day)
	if e == nil || len(r.Failures) != 2 || r.Written != 0 {
		t.Fatal(r, e)
	}
	var n int
	if e = db.QueryRow(`SELECT count(*) FROM market.daily_observation`).Scan(&n); e != nil || n != 1 {
		t.Fatal(n, e)
	}
	if _, e = SyncValuationQuoteWindow(ctx, db, f, []string{"bj920001"}, day); e == nil {
		t.Fatal("BSE accepted")
	}
	if _, e = SyncValuationQuoteWindow(ctx, db, f, []string{"sh600004", "sh600004"}, day); e == nil {
		t.Fatal("duplicate symbol accepted")
	}
}
