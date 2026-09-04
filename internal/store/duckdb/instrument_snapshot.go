package duckdb

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

type InstrumentSnapshotApplyResult struct {
	InstrumentIDs []int64
	Closed        int
}

type openProviderIdentifier struct {
	rowID          int64
	instrumentID   int64
	identifierType string
	value          string
	exchangeMIC    string
	validFrom      sql.NullTime
}

// ApplyInstrumentMasterSnapshot atomically refreshes one provider's current
// security master. Current observations are upserted first. Only a complete,
// plausibly-sized snapshot may close identifiers that disappeared; partial or
// suspiciously truncated snapshots can never erase current identity state.
func ApplyInstrumentMasterSnapshot(ctx context.Context, db *sql.DB, snapshot domain.InstrumentMasterSnapshot) (InstrumentSnapshotApplyResult, error) {
	var result InstrumentSnapshotApplyResult
	if db == nil {
		return result, errors.New("duckdb is nil")
	}
	snapshot.Source = strings.TrimSpace(snapshot.Source)
	if snapshot.Source == "" || snapshot.AsOfDate.IsZero() {
		return result, errors.New("snapshot source and as-of date are required")
	}
	snapshot.AsOfDate = dateUTC(snapshot.AsOfDate)
	if len(snapshot.Observations) == 0 {
		return result, errors.New("instrument snapshot is empty")
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return result, fmt.Errorf("begin instrument snapshot: %w", err)
	}
	defer tx.Rollback()

	current := make(map[string]string, len(snapshot.Observations)) // identifier key -> exchange MIC
	result.InstrumentIDs = make([]int64, len(snapshot.Observations))
	for i, observation := range snapshot.Observations {
		identifier := normalizedIdentifier(observation.Identifier)
		if identifier.Provider != snapshot.Source {
			return result, fmt.Errorf("instrument %d provider %q does not match snapshot source %q", i, identifier.Provider, snapshot.Source)
		}
		if identifier.ValidTo != nil {
			return result, fmt.Errorf("instrument %d current snapshot identifier is already closed", i)
		}
		if err := validateInstrumentInput(observation.Instrument, identifier); err != nil {
			return result, fmt.Errorf("instrument %d: %w", i, err)
		}
		key := providerIdentifierKey(identifier.Provider, identifier.Type, identifier.Value)
		if _, exists := current[key]; exists {
			return result, fmt.Errorf("duplicate identifier %s/%s/%s in instrument snapshot", identifier.Provider, identifier.Type, identifier.Value)
		}
		current[key] = strings.TrimSpace(observation.Instrument.ExchangeMIC)
		instrumentID, err := upsertInstrumentTx(ctx, tx, observation.Instrument, identifier)
		if err != nil {
			return result, fmt.Errorf("upsert snapshot instrument %s: %w", identifier.Value, err)
		}
		result.InstrumentIDs[i] = instrumentID
	}

	if snapshot.Complete {
		open, err := loadOpenPrimaryProviderIdentifiers(ctx, tx, snapshot.Source)
		if err != nil {
			return result, err
		}
		if err := validateInstrumentSnapshotSize(open, current); err != nil {
			return result, err
		}
		for _, item := range open {
			key := providerIdentifierKey(snapshot.Source, item.identifierType, item.value)
			if _, stillPresent := current[key]; stillPresent {
				continue
			}
			if item.validFrom.Valid && !dateUTC(item.validFrom.Time).Before(snapshot.AsOfDate) {
				return result, fmt.Errorf("refuse zero/negative identifier interval for %s/%s at %s", item.identifierType, item.value, snapshot.AsOfDate.Format("2006-01-02"))
			}
			if _, err := tx.ExecContext(ctx, `
				UPDATE ref.instrument_identifier
				SET valid_to=?
				WHERE instrument_identifier_id=? AND valid_to IS NULL
			`, snapshot.AsOfDate, item.rowID); err != nil {
				return result, fmt.Errorf("close missing provider identifier %s/%s: %w", item.identifierType, item.value, err)
			}
			result.Closed++
		}
	}

	if err := tx.Commit(); err != nil {
		return result, fmt.Errorf("commit instrument snapshot: %w", err)
	}
	return result, nil
}

func loadOpenPrimaryProviderIdentifiers(ctx context.Context, tx *sql.Tx, provider string) ([]openProviderIdentifier, error) {
	rows, err := tx.QueryContext(ctx, `
		SELECT x.instrument_identifier_id, x.instrument_id,
		       x.identifier_type, x.identifier_value,
		       COALESCE(i.exchange_mic, ''), x.valid_from
		FROM ref.instrument_identifier x
		JOIN ref.instrument i ON i.instrument_id=x.instrument_id
		WHERE x.provider=? AND x.is_primary=true AND x.valid_to IS NULL
	`, provider)
	if err != nil {
		return nil, fmt.Errorf("query open provider identifiers: %w", err)
	}
	defer rows.Close()
	var out []openProviderIdentifier
	for rows.Next() {
		var item openProviderIdentifier
		if err := rows.Scan(&item.rowID, &item.instrumentID, &item.identifierType, &item.value, &item.exchangeMIC, &item.validFrom); err != nil {
			return nil, fmt.Errorf("scan open provider identifier: %w", err)
		}
		out = append(out, item)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate open provider identifiers: %w", err)
	}
	return out, nil
}

// validateInstrumentSnapshotSize is deliberately conservative. A complete
// exchange universe should not suddenly disappear or shrink by >20% in one
// observation. Such a response is treated as provider truncation and may update
// nothing because the enclosing transaction is rolled back.
func validateInstrumentSnapshotSize(open []openProviderIdentifier, current map[string]string) error {
	previousByMIC := map[string]int{}
	for _, item := range open {
		previousByMIC[strings.TrimSpace(item.exchangeMIC)]++
	}
	currentByMIC := map[string]int{}
	for _, mic := range current {
		currentByMIC[strings.TrimSpace(mic)]++
	}
	for mic, previous := range previousByMIC {
		currentCount := currentByMIC[mic]
		if previous >= 20 && currentCount == 0 {
			return fmt.Errorf("refuse instrument snapshot that drops exchange %q from %d identifiers to zero", mic, previous)
		}
		if previous >= 100 && previous-currentCount >= 20 && currentCount*100 < previous*80 {
			return fmt.Errorf("refuse suspiciously truncated instrument snapshot for exchange %q: %d -> %d", mic, previous, currentCount)
		}
	}
	return nil
}

func providerIdentifierKey(provider, typ, value string) string {
	return strings.TrimSpace(provider) + "\x00" + strings.TrimSpace(typ) + "\x00" + strings.TrimSpace(value)
}

func snapshotDateAt(t time.Time) time.Time {
	return dateUTC(t)
}
