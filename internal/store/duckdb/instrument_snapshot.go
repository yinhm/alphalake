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

type InstrumentSnapshotPartitionFailure struct {
	Partition string
	Err       error
}

type InstrumentSnapshotApplyResult struct {
	InstrumentIDs    []int64
	Closed           int
	PendingClose     int
	DeferredClose    int
	PartitionFailures []InstrumentSnapshotPartitionFailure
}

type openProviderIdentifier struct {
	rowID          int64
	instrumentID   int64
	identifierType string
	value          string
	exchangeMIC    string
	validFrom      sql.NullTime
}

type instrumentPartitionApplyResult struct {
	InstrumentIDs []int64
	Closed        int
	PendingClose  int
	DeferredClose int
}

// ApplyInstrumentMasterSnapshot applies verified provider partitions
// independently. A failed/truncated partition cannot roll back another healthy
// exchange. Destructive authority remains partition-scoped, and identifiers are
// closed only after absence from two distinct complete observations.
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
	result.InstrumentIDs = make([]int64, len(snapshot.Observations))

	// Legacy/compatibility snapshots without explicit partitions retain one
	// transaction and global Complete semantics.
	if len(snapshot.Partitions) == 0 {
		part, err := applyInstrumentPartition(ctx, db, snapshot.Source, snapshot.AsOfDate, "", snapshot.Complete, snapshot.Observations)
		if err != nil {
			return result, err
		}
		copy(result.InstrumentIDs, part.InstrumentIDs)
		result.Closed = part.Closed
		result.PendingClose = part.PendingClose
		result.DeferredClose = part.DeferredClose
		return result, nil
	}

	flatIndex := make(map[string]int, len(snapshot.Observations))
	for i, observation := range snapshot.Observations {
		key := providerIdentifierKey(observation.Identifier.Provider, observation.Identifier.Type, observation.Identifier.Value)
		if _, exists := flatIndex[key]; exists {
			return result, fmt.Errorf("duplicate flat instrument snapshot identifier %s/%s/%s", observation.Identifier.Provider, observation.Identifier.Type, observation.Identifier.Value)
		}
		flatIndex[key] = i
	}

	applied := 0
	seenMIC := make(map[string]struct{}, len(snapshot.Partitions))
	for i, partition := range snapshot.Partitions {
		mic := strings.TrimSpace(partition.ExchangeMIC)
		label := strings.TrimSpace(partition.Key)
		if label == "" {
			label = mic
		}
		if mic == "" {
			result.PartitionFailures = append(result.PartitionFailures, InstrumentSnapshotPartitionFailure{Partition: label, Err: errors.New("missing exchange MIC")})
			continue
		}
		if _, exists := seenMIC[mic]; exists {
			return result, fmt.Errorf("duplicate instrument snapshot partition %d for exchange %q", i, mic)
		}
		seenMIC[mic] = struct{}{}
		if len(partition.Observations) == 0 {
			if partition.Complete {
				result.PartitionFailures = append(result.PartitionFailures, InstrumentSnapshotPartitionFailure{Partition: label, Err: errors.New("complete partition is empty")})
			}
			continue
		}
		part, err := applyInstrumentPartition(ctx, db, snapshot.Source, snapshot.AsOfDate, mic, partition.Complete, partition.Observations)
		if err != nil {
			result.PartitionFailures = append(result.PartitionFailures, InstrumentSnapshotPartitionFailure{Partition: label, Err: err})
			continue
		}
		applied++
		result.Closed += part.Closed
		result.PendingClose += part.PendingClose
		result.DeferredClose += part.DeferredClose
		for j, observation := range partition.Observations {
			key := providerIdentifierKey(observation.Identifier.Provider, observation.Identifier.Type, observation.Identifier.Value)
			flat, ok := flatIndex[key]
			if !ok {
				return result, fmt.Errorf("partition %q identifier %s is absent from flat snapshot", label, observation.Identifier.Value)
			}
			result.InstrumentIDs[flat] = part.InstrumentIDs[j]
		}
	}
	if applied == 0 {
		if len(result.PartitionFailures) != 0 {
			return result, fmt.Errorf("no instrument master partitions applied; first %s: %w", result.PartitionFailures[0].Partition, result.PartitionFailures[0].Err)
		}
		return result, errors.New("no instrument master partitions applied")
	}
	return result, nil
}

func applyInstrumentPartition(ctx context.Context, db *sql.DB, source string, asOf time.Time, exchangeMIC string, complete bool, observations []domain.InstrumentObservation) (instrumentPartitionApplyResult, error) {
	var result instrumentPartitionApplyResult
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return result, fmt.Errorf("begin instrument partition %q: %w", exchangeMIC, err)
	}
	defer tx.Rollback()

	current := make(map[string]string, len(observations))
	result.InstrumentIDs = make([]int64, len(observations))
	for i, observation := range observations {
		identifier := normalizedIdentifier(observation.Identifier)
		if identifier.Provider != source {
			return result, fmt.Errorf("instrument %d provider %q does not match snapshot source %q", i, identifier.Provider, source)
		}
		if identifier.ValidTo != nil {
			return result, fmt.Errorf("instrument %d current snapshot identifier is already closed", i)
		}
		if err := validateInstrumentInput(observation.Instrument, identifier); err != nil {
			return result, fmt.Errorf("instrument %d: %w", i, err)
		}
		mic := strings.TrimSpace(observation.Instrument.ExchangeMIC)
		if exchangeMIC != "" && mic != exchangeMIC {
			return result, fmt.Errorf("instrument %s exchange %q does not match partition %q", identifier.Value, mic, exchangeMIC)
		}
		key := providerIdentifierKey(identifier.Provider, identifier.Type, identifier.Value)
		if _, exists := current[key]; exists {
			return result, fmt.Errorf("duplicate identifier %s/%s/%s in instrument partition", identifier.Provider, identifier.Type, identifier.Value)
		}
		current[key] = mic
		instrumentID, err := upsertInstrumentTx(ctx, tx, observation.Instrument, identifier)
		if err != nil {
			return result, fmt.Errorf("upsert partition instrument %s: %w", identifier.Value, err)
		}
		result.InstrumentIDs[i] = instrumentID
	}

	if complete {
		open, err := loadOpenPrimaryProviderIdentifiers(ctx, tx, source)
		if err != nil {
			return result, err
		}
		if exchangeMIC != "" {
			if err := validateInstrumentPartitionSize(open, current, exchangeMIC); err != nil {
				return result, err
			}
		}
		for _, item := range open {
			mic := strings.TrimSpace(item.exchangeMIC)
			if exchangeMIC != "" && mic != exchangeMIC {
				continue
			}
			key := providerIdentifierKey(source, item.identifierType, item.value)
			checkpointKey := missingIdentifierCheckpointKey(item.identifierType, item.value)
			if _, stillPresent := current[key]; stillPresent {
				if err := deleteCheckpointTx(ctx, tx, source, instrumentMasterCheckpointDataset, checkpointKey); err != nil {
					return result, err
				}
				continue
			}
			firstMissing, found, err := getCheckpointDateTx(ctx, tx, source, instrumentMasterCheckpointDataset, checkpointKey)
			if err != nil {
				return result, err
			}
			if !found {
				if err := setCheckpointTx(ctx, tx, source, instrumentMasterCheckpointDataset, checkpointKey, asOf.Format("2006-01-02")); err != nil {
					return result, err
				}
				result.PendingClose++
				continue
			}
			if !firstMissing.Before(asOf) {
				result.PendingClose++
				continue
			}
			if item.validFrom.Valid && !dateUTC(item.validFrom.Time).Before(firstMissing) {
				result.DeferredClose++
				continue
			}
			if _, err := tx.ExecContext(ctx, `
				UPDATE ref.instrument_identifier SET valid_to=?
				WHERE instrument_identifier_id=? AND valid_to IS NULL
			`, firstMissing, item.rowID); err != nil {
				return result, fmt.Errorf("close missing provider identifier %s/%s: %w", item.identifierType, item.value, err)
			}
			if err := deleteCheckpointTx(ctx, tx, source, instrumentMasterCheckpointDataset, checkpointKey); err != nil {
				return result, err
			}
			result.Closed++
		}
	}

	if err := tx.Commit(); err != nil {
		return result, fmt.Errorf("commit instrument partition %q: %w", exchangeMIC, err)
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
	err := tx.QueryRowContext(ctx, `SELECT checkpoint_value FROM meta.checkpoint WHERE source=? AND dataset=? AND checkpoint_key=?`, source, dataset, key).Scan(&value)
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
		ON CONFLICT(source,dataset,checkpoint_key) DO UPDATE SET checkpoint_value=excluded.checkpoint_value, updated_at=now()
	`, source, dataset, key, value); err != nil {
		return fmt.Errorf("set checkpoint %s/%s/%s: %w", source, dataset, key, err)
	}
	return nil
}

func deleteCheckpointTx(ctx context.Context, tx *sql.Tx, source, dataset, key string) error {
	if _, err := tx.ExecContext(ctx, `DELETE FROM meta.checkpoint WHERE source=? AND dataset=? AND checkpoint_key=?`, source, dataset, key); err != nil {
		return fmt.Errorf("delete checkpoint %s/%s/%s: %w", source, dataset, key, err)
	}
	return nil
}

func providerIdentifierKey(provider, typ, value string) string {
	return strings.TrimSpace(provider) + "\x00" + strings.TrimSpace(typ) + "\x00" + strings.TrimSpace(value)
}
