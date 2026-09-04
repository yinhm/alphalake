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

// ClassificationApplyResult describes changes made by one provider taxonomy
// snapshot. Membership intervals use [effective_from, effective_to) semantics.
type ClassificationApplyResult struct {
	TaxonomyID int64
	Nodes      int
	Members    int
	Opened     int
	Closed     int
}

// ApplyClassificationSnapshotForRun resolves provider member identifiers and
// atomically applies one taxonomy snapshot. If any member cannot be resolved to
// a canonical instrument, the entire snapshot is rejected so an apparently
// complete-but-truncated snapshot cannot close valid historical memberships.
func ApplyClassificationSnapshotForRun(
	ctx context.Context,
	db *sql.DB,
	ingestRunID int64,
	snapshotDate time.Time,
	observedAt time.Time,
	snapshot domain.ClassificationSnapshot,
) (ClassificationApplyResult, error) {
	var result ClassificationApplyResult
	if db == nil {
		return result, errors.New("duckdb is nil")
	}
	if ingestRunID <= 0 {
		return result, errors.New("ingest run ID must be positive")
	}
	if snapshotDate.IsZero() || observedAt.IsZero() {
		return result, errors.New("snapshot date and observed_at are required")
	}
	if strings.TrimSpace(snapshot.Taxonomy.Source) == "" || strings.TrimSpace(snapshot.Taxonomy.Code) == "" || strings.TrimSpace(snapshot.Taxonomy.Name) == "" || strings.TrimSpace(snapshot.Taxonomy.Type) == "" {
		return result, errors.New("classification taxonomy metadata is incomplete")
	}
	snapshotDate = dateUTC(snapshotDate)

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return result, fmt.Errorf("begin classification snapshot: %w", err)
	}
	defer tx.Rollback()

	taxonomyID, err := upsertClassificationTaxonomy(ctx, tx, snapshot.Taxonomy)
	if err != nil {
		return result, err
	}
	result.TaxonomyID = taxonomyID

	nodeIDs := make(map[string]int64, len(snapshot.Nodes))
	for i, original := range snapshot.Nodes {
		if original.Taxonomy.Source != snapshot.Taxonomy.Source || original.Taxonomy.Code != snapshot.Taxonomy.Code {
			return result, fmt.Errorf("node %d taxonomy does not match snapshot", i)
		}
		node := original
		node.SourceNodeCode = strings.TrimSpace(node.SourceNodeCode)
		node.Name = strings.TrimSpace(node.Name)
		node.ParentNodeCode = strings.TrimSpace(node.ParentNodeCode)
		node.SourceSymbol = strings.TrimSpace(node.SourceSymbol)
		if node.SourceNodeCode == "" || node.Name == "" {
			return result, fmt.Errorf("node %d has incomplete identity", i)
		}
		if _, exists := nodeIDs[node.SourceNodeCode]; exists {
			return result, fmt.Errorf("duplicate source node code %q", node.SourceNodeCode)
		}
		nodeID, err := upsertClassificationNode(ctx, tx, taxonomyID, node, nil)
		if err != nil {
			return result, err
		}
		nodeIDs[node.SourceNodeCode] = nodeID
	}

	// Resolve parent references only after every current node has an ID. Parents
	// outside the current snapshot are resolved from the existing taxonomy.
	for _, node := range snapshot.Nodes {
		nodeCode := strings.TrimSpace(node.SourceNodeCode)
		parentCode := strings.TrimSpace(node.ParentNodeCode)
		if parentCode == "" {
			continue
		}
		parentID, ok := nodeIDs[parentCode]
		if !ok {
			if err := tx.QueryRowContext(ctx, `
				SELECT node_id FROM classification.node
				WHERE taxonomy_id=? AND source_node_code=?
			`, taxonomyID, parentCode).Scan(&parentID); err != nil {
				if errors.Is(err, sql.ErrNoRows) {
					return result, fmt.Errorf("parent node %q not found", parentCode)
				}
				return result, fmt.Errorf("resolve parent node %q: %w", parentCode, err)
			}
		}
		if _, err := tx.ExecContext(ctx, `
			UPDATE classification.node SET parent_node_id=?
			WHERE node_id=?
		`, parentID, nodeIDs[nodeCode]); err != nil {
			return result, fmt.Errorf("update parent for node %q: %w", nodeCode, err)
		}
	}

	var memberIdentifiers []domain.Identifier
	for _, node := range snapshot.Nodes {
		memberIdentifiers = append(memberIdentifiers, node.Members...)
	}
	identifierMap, err := resolveInstrumentIdentifiersAtTx(ctx, tx, memberIdentifiers, snapshotDate)
	if err != nil {
		return result, err
	}

	type membershipKey struct {
		instrumentID int64
		nodeID       int64
	}
	current := make(map[membershipKey]struct{})
	for _, node := range snapshot.Nodes {
		nodeCode := strings.TrimSpace(node.SourceNodeCode)
		nodeID := nodeIDs[nodeCode]
		for _, member := range node.Members {
			key := providerIdentifierKey(member.Provider, member.Type, member.Value)
			instrumentID, ok := identifierMap[key]
			if !ok {
				return result, fmt.Errorf("unresolved classification member %s/%s/%s in node %q", member.Provider, member.Type, member.Value, nodeCode)
			}
			current[membershipKey{instrumentID: instrumentID, nodeID: nodeID}] = struct{}{}
		}
	}
	result.Nodes = len(snapshot.Nodes)
	result.Members = len(current)

	type openMembership struct {
		key           membershipKey
		effectiveFrom time.Time
	}
	rows, err := tx.QueryContext(ctx, `
		SELECT m.instrument_id, m.node_id, m.effective_from
		FROM classification.membership m
		JOIN classification.node n ON n.node_id=m.node_id
		WHERE n.taxonomy_id=? AND m.source=? AND m.effective_to IS NULL
	`, taxonomyID, snapshot.Taxonomy.Source)
	if err != nil {
		return result, fmt.Errorf("query open classification memberships: %w", err)
	}
	var open []openMembership
	openMap := make(map[membershipKey]time.Time)
	for rows.Next() {
		var item openMembership
		if err := rows.Scan(&item.key.instrumentID, &item.key.nodeID, &item.effectiveFrom); err != nil {
			rows.Close()
			return result, fmt.Errorf("scan open classification membership: %w", err)
		}
		open = append(open, item)
		openMap[item.key] = dateUTC(item.effectiveFrom)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return result, fmt.Errorf("iterate open classification memberships: %w", err)
	}
	rows.Close()

	for key := range current {
		if _, exists := openMap[key]; exists {
			continue
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO classification.membership (
				instrument_id, node_id, effective_from, effective_to,
				source, observed_at, ingest_run_id
			) VALUES (?, ?, ?, NULL, ?, ?, ?)
		`, key.instrumentID, key.nodeID, snapshotDate, snapshot.Taxonomy.Source, observedAt, ingestRunID); err != nil {
			return result, fmt.Errorf("open classification membership: %w", err)
		}
		result.Opened++
	}

	if snapshot.Complete {
		for _, item := range open {
			if _, stillPresent := current[item.key]; stillPresent {
				continue
			}
			from := dateUTC(item.effectiveFrom)
			if snapshotDate.Before(from) {
				return result, fmt.Errorf("snapshot date %s precedes open membership %s", snapshotDate.Format("2006-01-02"), from.Format("2006-01-02"))
			}
			if snapshotDate.Equal(from) {
				// Classification history is date-granular. If a later snapshot on
				// the same day disagrees with a membership opened that day, treat
				// it as a correction to today's snapshot rather than persisting a
				// meaningless zero-length [date,date) interval.
				if _, err := tx.ExecContext(ctx, `
					DELETE FROM classification.membership
					WHERE instrument_id=? AND node_id=? AND effective_from=? AND source=? AND effective_to IS NULL
				`, item.key.instrumentID, item.key.nodeID, item.effectiveFrom, snapshot.Taxonomy.Source); err != nil {
					return result, fmt.Errorf("remove same-day classification membership: %w", err)
				}
				result.Closed++
				continue
			}
			if _, err := tx.ExecContext(ctx, `
				UPDATE classification.membership
				SET effective_to=?, ingest_run_id=?
				WHERE instrument_id=? AND node_id=? AND effective_from=? AND source=? AND effective_to IS NULL
			`, snapshotDate, ingestRunID, item.key.instrumentID, item.key.nodeID, item.effectiveFrom, snapshot.Taxonomy.Source); err != nil {
				return result, fmt.Errorf("close classification membership: %w", err)
			}
			result.Closed++
		}
	}

	if err := tx.Commit(); err != nil {
		return result, fmt.Errorf("commit classification snapshot: %w", err)
	}
	return result, nil
}

func upsertClassificationTaxonomy(ctx context.Context, tx *sql.Tx, taxonomy domain.ClassificationTaxonomy) (int64, error) {
	var taxonomyID int64
	err := tx.QueryRowContext(ctx, `
		SELECT taxonomy_id FROM classification.taxonomy
		WHERE source=? AND taxonomy_code=?
	`, taxonomy.Source, taxonomy.Code).Scan(&taxonomyID)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		if err := tx.QueryRowContext(ctx, `
			INSERT INTO classification.taxonomy (source, taxonomy_code, name, taxonomy_type)
			VALUES (?, ?, ?, ?) RETURNING taxonomy_id
		`, taxonomy.Source, taxonomy.Code, taxonomy.Name, taxonomy.Type).Scan(&taxonomyID); err != nil {
			return 0, fmt.Errorf("insert classification taxonomy %q: %w", taxonomy.Code, err)
		}
	case err != nil:
		return 0, fmt.Errorf("resolve classification taxonomy %q: %w", taxonomy.Code, err)
	default:
		if _, err := tx.ExecContext(ctx, `
			UPDATE classification.taxonomy SET name=?, taxonomy_type=? WHERE taxonomy_id=?
		`, taxonomy.Name, taxonomy.Type, taxonomyID); err != nil {
			return 0, fmt.Errorf("update classification taxonomy %q: %w", taxonomy.Code, err)
		}
	}
	return taxonomyID, nil
}

func upsertClassificationNode(ctx context.Context, tx *sql.Tx, taxonomyID int64, node domain.ClassificationNodeObservation, parentID *int64) (int64, error) {
	var nodeID int64
	err := tx.QueryRowContext(ctx, `
		SELECT node_id FROM classification.node
		WHERE taxonomy_id=? AND source_node_code=?
	`, taxonomyID, node.SourceNodeCode).Scan(&nodeID)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		if err := tx.QueryRowContext(ctx, `
			INSERT INTO classification.node (
				taxonomy_id, source_node_code, name, parent_node_id, level, source_symbol
			) VALUES (?, ?, ?, ?, ?, ?) RETURNING node_id
		`, taxonomyID, node.SourceNodeCode, node.Name, parentID, nullableInt(node.Level), nullableString(node.SourceSymbol)).Scan(&nodeID); err != nil {
			return 0, fmt.Errorf("insert classification node %q: %w", node.SourceNodeCode, err)
		}
	case err != nil:
		return 0, fmt.Errorf("resolve classification node %q: %w", node.SourceNodeCode, err)
	default:
		if _, err := tx.ExecContext(ctx, `
			UPDATE classification.node
			SET name=?, level=?, source_symbol=?
			WHERE node_id=?
		`, node.Name, nullableInt(node.Level), nullableString(node.SourceSymbol), nodeID); err != nil {
			return 0, fmt.Errorf("update classification node %q: %w", node.SourceNodeCode, err)
		}
	}
	return nodeID, nil
}

func nullableInt(v int) any {
	if v == 0 {
		return nil
	}
	return v
}

func dateUTC(t time.Time) time.Time {
	y, m, d := t.Date()
	return time.Date(y, m, d, 0, 0, 0, 0, time.UTC)
}
