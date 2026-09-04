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

const instrumentMasterCheckpointDataset = "instrument_master"
const missingIdentifierCheckpointPrefix = "missing:"

type InstrumentSnapshotApplyResult struct {
	InstrumentIDs []int64
	Closed        int
	PendingClose  int
	DeferredClose int
}

type openProviderIdentifier struct {
	rowID          int64
	instrumentID   int64
	identifierType string
	value          string
	exchangeMIC    string
	validFrom      sql.NullTime
}

// ApplyInstrumentMasterSnapshot atomically refreshes usable provider partitions.
// Destructive authority is partition-scoped: only a complete partition can
// advance missing-identifier evidence. A provider identifier is closed only
// after it is absent from two distinct complete observations; a one-off absence
// is recorded durably and cleared if the identifier returns.
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

	authority, err := snapshotPartitionAuthority(snapshot)
	if err != nil {
		return result, err
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
		mic := strings.TrimSpace(observation.Instrument.ExchangeMIC)
		key := providerIdentifierKey(identifier.Provider, identifier.Type, identifier.Value)
		if _, exists := current[key]; exists {
			return result, fmt.Errorf("duplicate identifier %s/%s/%s in instrument snapshot", identifier.Provider, identifier.Type, identifier.Value)
		}
		current[key] = mic
		instrumentID, err := upsertInstrumentTx(ctx, tx, observation.Instrument, identifier)
		if err != nil {
			return result, fmt.Errorf("upsert snapshot instrument %s: %w", identifier.Value, err)
		}
		result.InstrumentIDs[i] = instrumentID
	}

	open, err := loadOpenPrimaryProviderIdentifiers(ctx, tx, snapshot.Source)
	if err != nil {
		return result, err
	}
	for mic, complete := range authority {
		if !complete {
			continue
		}
		if err := validateInstrumentPartitionSize(open, current, mic); err != nil {
			return result, err
		}
	}

	for _, item := range open {
		mic := strings.TrimSpace(item.exchangeMIC)
		if !authority[mic] {
			continue
		}
		key := providerIdentifierKey(snapshot.Source, item.identifierType, item.value)
		checkpointKey := missingIdentifierCheckpointKey(item.identifierType, item.value)
		if _, stillPresent := current[key]; stillPresent {
			if err := deleteCheckpointTx(ctx, tx, snapshot.Source, instrumentMasterCheckpointDataset, checkpointKey); err != nil {
				return result, err
			}
			continue
		}

		firstMissing, found, err := getCheckpointDateTx(ctx, tx, snapshot.Source, instrumentMasterCheckpointDataset, checkpointKey)
		if err != nil {
			return result, err
		}
		if !found {
			if err := setCheckpointTx(ctx, tx, snapshot.Source, instrumentMasterCheckpointDataset, checkpointKey, snapshot.AsOfDate.Format("2006-01-02")); err != nil {
				return result, err
			}
			result.PendingClose++
			continue
		}
		if !firstMissing.Before(snapshot.AsOfDate) {
			// Re-running a complete snapshot on the same date is not additional
			// evidence and must not convert one observation into two.
			result.PendingClose++
			continue
		}
		if item.validFrom.Valid && !dateUTC(item.validFrom.Time).Before(firstMissing) {
			// A malformed/provider-inconsistent interval must not roll back valid
			// partitions. Leave the identifier open and evidence intact for later
			// authoritative repair instead of inventing a zero-length interval.
			result.DeferredClose++
			continue
		}
		if _, err := tx.ExecContext(ctx, `
			UPDATE ref.instrument_identifier
			SET valid_to=?
			WHERE instrument_identifier_id=? AND valid_to IS NULL
		`, firstMissing, item.rowID); err != nil {
			return result, fmt.Errorf("close missing provider identifier %s/%s: %w", item.identifierType, item.value, err)
		}
		if err := deleteCheckpointTx(ctx, tx, snapshot.Source, instrumentMasterCheckpointDataset, checkpointKey); err != nil {
			return result, err
		}
		result.Closed++
	}

	if err := tx.Commit(); err != nil {
		return result, fmt.Errorf("commit instrument snapshot: %w", err)
	}
	return result, nil
}

func snapshotPartitionAuthority(snapshot domain.InstrumentMasterSnapshot) (map[string]bool, error) {
	authority := make(map[string]bool)
	if len(snapshot.Partitions) == 0 {
		for _, observation := range snapshot.Observations {
			mic := strings.TrimSpace(observation.Instrument.ExchangeMIC)
			if mic != "" {
				authority[mic] = snapshot.Complete
			}
		}
		return authority, nil
	}
	for i, partition := range snapshot.Partitions {
		mic := strings.TrimSpace(partition.ExchangeMIC)
		if mic == "" {
			return nil, fmt.Errorf("instrument snapshot partition %d has no exchange MIC", i)
		}
		if _, exists := authority[mic]; exists {
			return nil, fmt.Errorf("duplicate instrument snapshot partition for exchange %q", mic)
		}
		if partition.Complete && len(partition.Observations) == 0 {
			return nil, fmt.Errorf("complete instrument snapshot partition %q is empty", mic)
		}
		authority[mic] = partition.Complete
	}
	return authority, nil
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

func validateInstrumentPartitionSize(open []openProviderIdentifier, current map[string]string, mic string) error {
	previous := 0
	for _, item := range open {
		if strings.TrimSpace(item.exchangeMIC) == mic {
			previous++
		}
	}
	currentCount := 0
	for _, currentMIC := range current {
		if strings.TrimSpace(currentMIC) == mic {
			currentCount++
		}
	}
	if previous >= 20 && currentCount == 0 {
		return fmt.Errorf("refuse instrument partition %q that drops from %d identifiers to zero", mic, previous)
	}
	if previous >= 100 && previous-currentCount >= 20 && currentCount*100 < previous*80 {
		return fmt.Errorf("refuse suspiciously truncated instrument partition %q: %d -> %d", mic, previous, currentCount)
	}
	return nil
}

func missingIdentifierCheckpointKey(typ, value string) string {
	return missingIdentifierCheckpointPrefix + strings.TrimSpace(typ) + ":" + strings.TrimSpace(value)
}

func getCheckpointDateTx(ctx context.Context, tx *sql.Tx, source, dataset, key string) (time.Time, bool, error) {
	var value string
	err := tx.QueryRowContext(ctx, `
		SELECT checkpoint_value FROM meta.checkpoint
		WHERE source=? AND dataset=? AND checkpoint_key=?
	`, source, dataset, key).Scan(&value)
	if errors.Is(err, sql.ErrNoRows) {
		return time.Time{}, false, nil
	}
	if err != nil {
		return time.Time{}, false, fmt.Errorf("get checkpoint %s/%s/%s: %w", source, dataset, key, err)
	}
	day, err := time.Parse("2006-01-02", value)
	if err != nil {
		return time.Time{}, false, fmt.Errorf("parse checkpoint %s/%s/%s value %q: %w", source, dataset, key, value, err)
	}
	return dateUTC(day), true, nil
}

func setCheckpointTx(ctx context.Context, tx *sql.Tx, source, dataset, key, value string) error {
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO meta.checkpoint(source,dataset,checkpoint_key,checkpoint_value)
		VALUES (?, ?, ?, ?)
		ON CONFLICT(source,dataset,checkpoint_key) DO UPDATE SET
			checkpoint_value=excluded.checkpoint_value, updated_at=now()
	`, source, dataset, key, value); err != nil {
		return fmt.Errorf("set checkpoint %s/%s/%s: %w", source, dataset, key, err)
	}
	return nil
}

func deleteCheckpointTx(ctx context.Context, tx *sql.Tx, source, dataset, key string) error {
	if _, err := tx.ExecContext(ctx, `
		DELETE FROM meta.checkpoint WHERE source=? AND dataset=? AND checkpoint_key=?
	`, source, dataset, key); err != nil {
		return fmt.Errorf("delete checkpoint %s/%s/%s: %w", source, dataset, key, err)
	}
	return nil
}

func providerIdentifierKey(provider, typ, value string) string {
	return strings.TrimSpace(provider) + "\x00" + strings.TrimSpace(typ) + "\x00" + strings.TrimSpace(value)
}
