package ingest

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	tdx "github.com/yinhm/alphalake/internal/source/tdx"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
	"github.com/yinhm/alphalake/internal/validate"
)

type QuoteWindowSource interface {
	StockDailyBarsWindow(context.Context, int64, string, time.Time, time.Time) ([]domain.DailyBar, error)
}
type QuoteWindowSummary struct {
	RunID     int64    `json:"run_id"`
	Companies int      `json:"companies"`
	Written   int      `json:"observations_written"`
	Failures  []string `json:"failures"`
}

// SyncValuationQuoteWindow uses already-resolved identities and the native
// model's inclusive 14-day lookback. It does not advance full-history coverage.
func SyncValuationQuoteWindow(ctx context.Context, db *sql.DB, source QuoteWindowSource, symbols []string, end time.Time) (out QuoteWindowSummary, retErr error) {
	today := time.Now().In(time.FixedZone("China", 8*3600)).Format("2006-01-02")
	if db == nil || source == nil || len(symbols) == 0 || end.IsZero() || end.Format("2006-01-02") >= today {
		return out, fmt.Errorf("known SH/SZ symbols and a closed historical report date required")
	}
	seen := map[string]bool{}
	for _, s := range symbols {
		key, e := tdx.NormalizeSymbol(s)
		if e != nil || key.ProviderSymbol != s || (!strings.HasPrefix(s, "sh") && !strings.HasPrefix(s, "sz")) || seen[s] {
			return out, fmt.Errorf("invalid/duplicate SH/SZ symbol %q", s)
		}
		seen[s] = true
	}
	end = time.Date(end.Year(), end.Month(), end.Day(), 0, 0, 0, 0, time.UTC)
	start := end.AddDate(0, 0, -14)
	run, e := store.StartIngestRun(ctx, db, "tdx", "valuation_quote_window", nil)
	if e != nil {
		return out, e
	}
	out.RunID = run
	out.Companies = len(symbols)
	out.Failures = []string{}
	defer func() {
		status := store.IngestRunCompleted
		if retErr != nil {
			status = store.IngestRunFailed
			if out.Written > 0 {
				status = store.IngestRunPartial
			}
		}
		if ctx.Err() != nil {
			status = store.IngestRunCanceled
		}
		finalizeTrackedRun(ctx, db, run, status, &retErr)
	}()
	syncOne := func(symbol string) error {
		id, found, e := store.ResolveInstrumentIdentifierAt(ctx, db, "tdx", "symbol", symbol, end)
		if e != nil {
			return e
		}
		if !found {
			return fmt.Errorf("unresolved identity at report date; refresh identity evidence explicitly")
		}
		current, found, e := store.ResolveInstrumentIdentifierAt(ctx, db, "tdx", "symbol", symbol, time.Now())
		if e != nil {
			return e
		}
		if !found || current != id {
			return fmt.Errorf("current and historical source identity differ")
		}
		var kind, mic, currency string
		if e = db.QueryRowContext(ctx, `SELECT instrument_type,exchange_mic,currency FROM core.instrument WHERE instrument_id=?`, id).Scan(&kind, &mic, &currency); e != nil {
			return e
		}
		key, _ := tdx.NormalizeSymbol(symbol)
		if kind != "equity" || currency != "CNY" || mic != key.ExchangeMIC || (mic != "XSHG" && mic != "XSHE") {
			return fmt.Errorf("SH/SZ equity identity required")
		}
		bars, e := source.StockDailyBarsWindow(ctx, id, symbol, start, end)
		if e != nil {
			return e
		}
		if len(bars) == 0 {
			return fmt.Errorf("no observation in requested window")
		}
		days := map[string]bool{}
		for _, b := range bars {
			day := b.TradeDate.Format("2006-01-02")
			if b.InstrumentID != id || b.Source != "tdx" || b.TradeDate.Before(start) || b.TradeDate.After(end) || b.Close <= 0 || days[day] {
				return fmt.Errorf("invalid/duplicate observation identity, date or close")
			}
			days[day] = true
			at, found, e := store.ResolveInstrumentIdentifierAt(ctx, db, "tdx", "symbol", symbol, b.TradeDate)
			if e != nil {
				return e
			}
			if !found || at != id {
				return fmt.Errorf("observation predates known identity interval")
			}
		}
		if v := validate.DailyBars(bars); len(v) > 0 {
			return fmt.Errorf("invalid observation: %s: %s", v[0].RuleCode, v[0].Details)
		}
		if e = store.AppendQuoteWindow(ctx, db, run, bars); e != nil {
			return e
		}
		out.Written += len(bars)
		return nil
	}
	for _, symbol := range symbols {
		if e := ctx.Err(); e != nil {
			return out, e
		}
		if e := syncOne(symbol); e != nil {
			message := symbol + ": " + e.Error()
			out.Failures = append(out.Failures, message)
			if e = store.RecordIngestDiagnostics(ctx, db, run, "tdx", "valuation_quote_window", []store.IngestDiagnostic{{RuleCode: "valuation_quote.window_failed", Severity: "error", SubjectType: "symbol", SubjectKey: symbol, Details: message}}); e != nil {
				return out, e
			}
		}
	}
	if len(out.Failures) > 0 {
		return out, fmt.Errorf("%d quote windows failed; first %s", len(out.Failures), out.Failures[0])
	}
	return out, nil
}
