package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/validate"
)

func TestApplyDailyIngestBatchForRunCommitsBarsValidationAndCheckpointTogether(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "atomic-daily.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	instrumentID, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Test"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"},
	)
	if err != nil {
		t.Fatal(err)
	}
	runID, err := StartIngestRun(ctx, db, "tdx", "daily_ohlcv", nil)
	if err != nil {
		t.Fatal(err)
	}
	day := time.Date(2026, 9, 4, 0, 0, 0, 0, time.UTC)
	bar := domain.DailyBar{
		InstrumentID: instrumentID, TradeDate: day,
		Open: 10, High: 11, Low: 9, Close: 10.5,
		Volume: 1000, Amount: 10500, Source: "tdx",
	}
	violations := []validate.Violation{{
		RuleCode: "daily.high_bound", Severity: "error",
		SubjectKey: "1:2026-09-03", Details: "quarantined test row",
	}}
	retry := day.AddDate(0, 0, -1)
	if err := ApplyDailyIngestBatchForRun(
		ctx, db, runID, "tdx", "daily_ohlcv", "daily_bar", "quarantine_from:test",
		[]domain.DailyBar{bar}, violations, &retry,
	); err != nil {
		t.Fatal(err)
	}

	var bars, validationRows int
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM market.ohlcv_daily WHERE instrument_id=?`, instrumentID).Scan(&bars); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM meta.validation_result WHERE ingest_run_id=?`, runID).Scan(&validationRows); err != nil {
		t.Fatal(err)
	}
	checkpoint, ok, err := GetCheckpoint(ctx, db, "tdx", "daily_ohlcv", "quarantine_from:test")
	if err != nil {
		t.Fatal(err)
	}
	if bars != 1 || validationRows != 1 || !ok || checkpoint != "2026-09-03" {
		t.Fatalf("bars=%d validations=%d checkpoint=%q/%v", bars, validationRows, checkpoint, ok)
	}
}

func TestDailyWriteTransactionRollsBackAllEffectsOnLateFailure(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "rollback-daily.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	instrumentID, err := UpsertInstrument(ctx, db,
		domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHG", Currency: "CNY", Name: "Test"},
		domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sh600001"},
	)
	if err != nil {
		t.Fatal(err)
	}
	runID, err := StartIngestRun(ctx, db, "tdx", "daily_ohlcv", nil)
	if err != nil {
		t.Fatal(err)
	}
	bar := domain.DailyBar{
		InstrumentID: instrumentID,
		TradeDate: time.Date(2026, 9, 4, 0, 0, 0, 0, time.UTC),
		Open: 10, High: 11, Low: 9, Close: 10.5,
		Volume: 1000, Amount: 10500, Source: "tdx",
	}
	violations := []validate.Violation{{RuleCode: "test.rule", Severity: "error", SubjectKey: "test", Details: "test"}}
	sentinel := errors.New("forced late failure")
	err = withDailyWriteTransaction(ctx, db, func(conn *sql.Conn) error {
		if err := mergeDailyBarsOnConn(ctx, conn, []domain.DailyBar{bar}, &runID); err != nil {
			return err
		}
		if err := insertValidationViolationsOnConn(ctx, conn, runID, "tdx", "daily_ohlcv", "daily_bar", violations); err != nil {
			return err
		}
		if _, err := conn.ExecContext(ctx, `
			INSERT INTO meta.checkpoint(source,dataset,checkpoint_key,checkpoint_value)
			VALUES ('tdx','daily_ohlcv','rollback-test','2026-09-03')
		`); err != nil {
			return err
		}
		return sentinel
	})
	if !errors.Is(err, sentinel) {
		t.Fatalf("error=%v, want sentinel", err)
	}

	for name, query := range map[string]string{
		"bars": `SELECT count(*) FROM market.ohlcv_daily WHERE instrument_id=` + fmt.Sprint(instrumentID),
		"validation": `SELECT count(*) FROM meta.validation_result WHERE ingest_run_id=` + fmt.Sprint(runID),
		"checkpoint": `SELECT count(*) FROM meta.checkpoint WHERE checkpoint_key='rollback-test'`,
	} {
		var count int
		if err := db.QueryRowContext(ctx, query).Scan(&count); err != nil {
			t.Fatalf("query %s: %v", name, err)
		}
		if count != 0 {
			t.Fatalf("%s count=%d, want rollback to zero", name, count)
		}
	}
}
