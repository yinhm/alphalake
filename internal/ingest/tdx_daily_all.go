package ingest

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
	"github.com/yinhm/alphalake/internal/validate"
)

// TDXIncrementalDailySource extends the initial source boundary with an
// inclusive incremental fetch used by all-market synchronization.
type TDXIncrementalDailySource interface {
	TDXDailySource
	StockDailyBarsSince(context.Context, int64, string, time.Time) ([]domain.DailyBar, error)
}

type TDXDailySyncFailure struct {
	Symbol string
	Err    error
}

type TDXDailySyncSummary struct {
	Instruments int
	Attempted   int
	Synced      int
	Skipped     int
	Bars        int
	Failures    []TDXDailySyncFailure
}

type TDXDailyBatchError struct {
	Failures []TDXDailySyncFailure
}

func (e *TDXDailyBatchError) Error() string {
	if e == nil || len(e.Failures) == 0 {
		return ""
	}
	first := e.Failures[0]
	return fmt.Sprintf("%d TDX instruments failed; first %s: %v", len(e.Failures), first.Symbol, first.Err)
}

// SyncAllTDXDaily refreshes the TDX instrument master, then synchronizes
// canonical daily history for equities and ETFs. Each instrument resumes from
// its own latest stored day; that boundary day is fetched again and upserted.
// Per-instrument failures are collected so one bad symbol does not discard the
// rest of the market update.
func SyncAllTDXDaily(ctx context.Context, db *sql.DB, source TDXIncrementalDailySource) (TDXDailySyncSummary, error) {
	var summary TDXDailySyncSummary
	if db == nil {
		return summary, fmt.Errorf("duckdb is nil")
	}
	if source == nil {
		return summary, fmt.Errorf("TDX source is nil")
	}

	observations, err := source.Instruments(ctx)
	if err != nil {
		return summary, fmt.Errorf("list TDX instruments: %w", err)
	}
	summary.Instruments = len(observations)

	instrumentIDs, err := duckstore.UpsertInstruments(ctx, db, observations)
	if err != nil {
		return summary, fmt.Errorf("refresh canonical instrument master: %w", err)
	}

	for i, observation := range observations {
		if err := ctx.Err(); err != nil {
			return summary, err
		}
		if !dailyEligible(observation.Instrument.Type) {
			summary.Skipped++
			continue
		}

		summary.Attempted++
		symbol := observation.Identifier.Value
		instrumentID := instrumentIDs[i]
		latest, hasLatest, err := duckstore.LatestDailyDate(ctx, db, instrumentID, observation.Identifier.Provider)
		if err != nil {
			summary.Failures = append(summary.Failures, TDXDailySyncFailure{Symbol: symbol, Err: err})
			continue
		}

		var bars []domain.DailyBar
		if hasLatest {
			bars, err = source.StockDailyBarsSince(ctx, instrumentID, symbol, latest)
		} else {
			bars, err = source.StockDailyBars(ctx, instrumentID, symbol)
		}
		if err != nil {
			summary.Failures = append(summary.Failures, TDXDailySyncFailure{Symbol: symbol, Err: err})
			continue
		}

		if violations := validate.DailyBars(bars); len(violations) != 0 {
			validationErr := fmt.Errorf("daily validation failed: %s", summarizeViolations(violations))
			if err := duckstore.RecordValidationViolations(ctx, db, nil, observation.Identifier.Provider, "daily_ohlcv", "daily_bar", violations); err != nil {
				validationErr = fmt.Errorf("%v; persist validation failures: %w", validationErr, err)
			}
			summary.Failures = append(summary.Failures, TDXDailySyncFailure{Symbol: symbol, Err: validationErr})
			continue
		}
		if err := duckstore.UpsertDailyBars(ctx, db, bars); err != nil {
			summary.Failures = append(summary.Failures, TDXDailySyncFailure{Symbol: symbol, Err: err})
			continue
		}
		summary.Synced++
		summary.Bars += len(bars)
	}

	if len(summary.Failures) != 0 {
		return summary, &TDXDailyBatchError{Failures: summary.Failures}
	}
	return summary, nil
}

func dailyEligible(t domain.InstrumentType) bool {
	return t == domain.InstrumentEquity || t == domain.InstrumentETF
}

func summarizeViolations(violations []validate.Violation) string {
	const max = 3
	parts := make([]string, 0, minInt(len(violations), max))
	for i, violation := range violations {
		if i == max {
			break
		}
		parts = append(parts, violation.RuleCode+"("+violation.SubjectKey+")")
	}
	if len(violations) > max {
		parts = append(parts, fmt.Sprintf("+%d more", len(violations)-max))
	}
	return strings.Join(parts, ", ")
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
