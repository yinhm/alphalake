package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"github.com/yinhm/alphalake/internal/domain"
)

// ReplaceCorporateActionSnapshotForRun atomically replaces one provider's full
// corporate-action/share-capital snapshot for one instrument. Snapshot replace
// is preferred over append-only upsert here because upstream GBBQ history may be
// corrected or remove an earlier event.
func ReplaceCorporateActionSnapshotForRun(
	ctx context.Context,
	db *sql.DB,
	ingestRunID int64,
	instrumentID int64,
	source string,
	actions []domain.CorporateAction,
	shareCapital []domain.ShareCapital,
) error {
	if db == nil {
		return errors.New("duckdb is nil")
	}
	if ingestRunID <= 0 {
		return errors.New("ingest run ID must be positive")
	}
	if instrumentID <= 0 {
		return errors.New("instrument ID must be positive")
	}
	source = strings.TrimSpace(source)
	if source == "" {
		return errors.New("source is required")
	}
	for i, action := range actions {
		if action.InstrumentID != instrumentID {
			return fmt.Errorf("action %d instrument ID %d does not match snapshot instrument %d", i, action.InstrumentID, instrumentID)
		}
		if action.ActionDate.IsZero() || strings.TrimSpace(action.ActionType) == "" || action.Source != source || strings.TrimSpace(action.SourceRecordID) == "" {
			return fmt.Errorf("action %d has incomplete identity/source metadata", i)
		}
	}
	for i, capital := range shareCapital {
		if capital.InstrumentID != instrumentID {
			return fmt.Errorf("share capital %d instrument ID %d does not match snapshot instrument %d", i, capital.InstrumentID, instrumentID)
		}
		if capital.EffectiveDate.IsZero() || capital.Source != source || strings.TrimSpace(capital.SourceRecordID) == "" {
			return fmt.Errorf("share capital %d has incomplete identity/source metadata", i)
		}
		if capital.FloatShares < 0 || capital.TotalShares < 0 {
			return fmt.Errorf("share capital %d contains negative share count", i)
		}
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin corporate action snapshot replace: %w", err)
	}
	defer tx.Rollback()

	if _, err := tx.ExecContext(ctx, `
		DELETE FROM market.corporate_action
		WHERE instrument_id = ? AND source = ?
	`, instrumentID, source); err != nil {
		return fmt.Errorf("delete prior corporate actions: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		DELETE FROM market.share_capital
		WHERE instrument_id = ? AND source = ?
	`, instrumentID, source); err != nil {
		return fmt.Errorf("delete prior share capital: %w", err)
	}

	for _, action := range actions {
		cashDividend, rightsPrice, bonusOrSplit, rightsPer10, scaleFactor := normalizedActionValues(action)
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO market.corporate_action (
				instrument_id, action_date, action_type, source_category,
				cash_dividend_per_10, rights_price, bonus_or_split_per_10,
				rights_per_10, scale_factor,
				raw_c1, raw_c2, raw_c3, raw_c4,
				source, source_record_id, ingest_run_id
			) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		`,
			action.InstrumentID, action.ActionDate, action.ActionType, action.SourceCategory,
			cashDividend, rightsPrice, bonusOrSplit, rightsPer10, scaleFactor,
			action.RawC1, action.RawC2, action.RawC3, action.RawC4,
			action.Source, action.SourceRecordID, ingestRunID,
		); err != nil {
			return fmt.Errorf("insert corporate action %s: %w", action.SourceRecordID, err)
		}
	}

	for _, capital := range shareCapital {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO market.share_capital (
				instrument_id, effective_date, float_shares, total_shares,
				source_category, source, source_record_id, ingest_run_id
			) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
		`,
			capital.InstrumentID, capital.EffectiveDate, capital.FloatShares, capital.TotalShares,
			capital.SourceCategory, capital.Source, capital.SourceRecordID, ingestRunID,
		); err != nil {
			return fmt.Errorf("insert share capital %s: %w", capital.SourceRecordID, err)
		}
	}

	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit corporate action snapshot replace: %w", err)
	}
	return nil
}

func normalizedActionValues(action domain.CorporateAction) (cashDividend, rightsPrice, bonusOrSplit, rightsPer10, scaleFactor any) {
	switch action.ActionType {
	case "distribution":
		cashDividend = action.CashDividendPer10
		rightsPrice = action.RightsPrice
		bonusOrSplit = action.BonusOrSplitPer10
		rightsPer10 = action.RightsPer10
	case "scale", "nontradable_share_scale":
		scaleFactor = action.ScaleFactor
	}
	return
}
