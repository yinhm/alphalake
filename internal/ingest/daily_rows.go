package ingest

import (
	"context"
	"database/sql"
	"fmt"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
	"github.com/yinhm/alphalake/internal/validate"
)

const dailyRetryCheckpointPrefix = "quarantine_from:"

type dailyApplyResult struct {
	Written     int
	Quarantined int
	RetryFrom   *time.Time
}

func dailyRetryCheckpointKey(instrumentID int64) string {
	return fmt.Sprintf("%s%d", dailyRetryCheckpointPrefix, instrumentID)
}

func dailyFetchBoundary(ctx context.Context, db *sql.DB, source string, instrumentID int64) (time.Time, bool, error) {
	latest, hasLatest, err := duckstore.LatestDailyDate(ctx, db, instrumentID, source)
	if err != nil {
		return time.Time{}, false, err
	}
	value, hasRetry, err := duckstore.GetCheckpoint(ctx, db, source, tdxDailyDataset, dailyRetryCheckpointKey(instrumentID))
	if err != nil {
		return time.Time{}, false, err
	}
	if !hasRetry {
		return latest, hasLatest, nil
	}
	retry, err := time.Parse("2006-01-02", value)
	if err != nil {
		return time.Time{}, false, fmt.Errorf("parse daily quarantine checkpoint %q: %w", value, err)
	}
	if !hasLatest || retry.Before(latest) {
		return retry, true, nil
	}
	return latest, true, nil
}

func applyDailyRows(ctx context.Context, db *sql.DB, runID int64, source string, instrumentID int64, bars []domain.DailyBar) (dailyApplyResult, error) {
	var result dailyApplyResult
	valid, quarantined, violations := validate.PartitionDailyBars(bars)
	if len(violations) != 0 {
		if err := duckstore.RecordValidationViolations(ctx, db, &runID, source, tdxDailyDataset, "daily_bar", violations); err != nil {
			return result, fmt.Errorf("persist daily validation failures: %w", err)
		}
	}
	if err := duckstore.UpsertDailyBarsForRun(ctx, db, runID, valid); err != nil {
		return result, err
	}
	result.Written = len(valid)
	result.Quarantined = len(quarantined)

	checkpointKey := dailyRetryCheckpointKey(instrumentID)
	if len(quarantined) == 0 {
		if err := duckstore.DeleteCheckpoint(ctx, db, source, tdxDailyDataset, checkpointKey); err != nil {
			return result, err
		}
		return result, nil
	}

	var earliest time.Time
	for _, bar := range quarantined {
		if bar.TradeDate.IsZero() {
			// A row without a date cannot provide a useful retry boundary. The
			// validation record remains persisted, but do not poison the durable
			// checkpoint with an unparseable value.
			continue
		}
		if earliest.IsZero() || bar.TradeDate.Before(earliest) {
			earliest = bar.TradeDate
		}
	}
	if !earliest.IsZero() {
		day := time.Date(earliest.Year(), earliest.Month(), earliest.Day(), 0, 0, 0, 0, time.UTC)
		if err := duckstore.SetCheckpoint(ctx, db, source, tdxDailyDataset, checkpointKey, day.Format("2006-01-02")); err != nil {
			return result, err
		}
		result.RetryFrom = &day
	}
	return result, nil
}
