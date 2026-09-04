package artifact

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"strings"
)

type Loaded struct {
	Stored  Stored
	Content []byte
}

// LoadLatest returns the newest retained artifact for one provider locator and
// verifies its content hash before use. A missing metadata row is not an error.
func LoadLatest(ctx context.Context, db *sql.DB, root, source, dataset, sourceLocator string) (Stored, []byte, bool, error) {
	versions, err := LoadVersions(ctx, db, root, source, dataset, sourceLocator, 1)
	if err != nil {
		return Stored{}, nil, false, err
	}
	if len(versions) == 0 {
		return Stored{}, nil, false, nil
	}
	return versions[0].Stored, versions[0].Content, true, nil
}

// LoadVersions returns retained versions for one provider locator newest first,
// verifying every selected content-addressed file. Any corrupt/missing retained
// file is an error because callers of this strict API requested verified history.
func LoadVersions(ctx context.Context, db *sql.DB, root, source, dataset, sourceLocator string, limit int) ([]Loaded, error) {
	rows, err := queryVersionMetadata(ctx, db, root, source, dataset, sourceLocator, limit)
	if err != nil {
		return nil, err
	}
	out := make([]Loaded, 0, len(rows))
	for _, stored := range rows {
		content, err := loadVerified(root, stored)
		if err != nil {
			return nil, err
		}
		out = append(out, Loaded{Stored: stored, Content: content})
	}
	return out, nil
}

// LoadHealthyVersions is the cache-oriented counterpart to LoadVersions. It
// verifies versions independently, returns every healthy revision, and reports
// corrupt/missing local revisions separately instead of allowing one bad cache
// entry to make all retained history unusable. A caller may then fall back to a
// provider re-download for the desired revision.
func LoadHealthyVersions(ctx context.Context, db *sql.DB, root, source, dataset, sourceLocator string, limit int) ([]Loaded, []error, error) {
	rows, err := queryVersionMetadata(ctx, db, root, source, dataset, sourceLocator, limit)
	if err != nil {
		return nil, nil, err
	}
	out := make([]Loaded, 0, len(rows))
	var failures []error
	for _, stored := range rows {
		content, err := loadVerified(root, stored)
		if err != nil {
			failures = append(failures, err)
			continue
		}
		out = append(out, Loaded{Stored: stored, Content: content})
	}
	return out, failures, nil
}

func queryVersionMetadata(ctx context.Context, db *sql.DB, root, source, dataset, sourceLocator string, limit int) ([]Stored, error) {
	if db == nil {
		return nil, errors.New("duckdb is nil")
	}
	root = strings.TrimSpace(root)
	source = strings.TrimSpace(source)
	dataset = strings.TrimSpace(dataset)
	sourceLocator = strings.TrimSpace(sourceLocator)
	if root == "" || source == "" || dataset == "" || sourceLocator == "" {
		return nil, errors.New("artifact root, source, dataset, and source locator are required")
	}
	query := `
		SELECT artifact_id, sha256, content_length, local_path
		FROM meta.artifact
		WHERE source=? AND dataset=? AND source_locator=?
		ORDER BY fetched_at DESC, artifact_id DESC
	`
	args := []any{source, dataset, sourceLocator}
	if limit > 0 {
		query += ` LIMIT ?`
		args = append(args, limit)
	}
	rows, err := db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("query artifact versions: %w", err)
	}
	defer rows.Close()
	var out []Stored
	for rows.Next() {
		var stored Stored
		if err := rows.Scan(&stored.ArtifactID, &stored.SHA256, &stored.ContentLength, &stored.LocalPath); err != nil {
			return nil, fmt.Errorf("scan artifact version: %w", err)
		}
		out = append(out, stored)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate artifact versions: %w", err)
	}
	return out, nil
}

func loadVerified(root string, stored Stored) ([]byte, error) {
	content, err := os.ReadFile(Resolve(root, stored))
	if err != nil {
		return nil, fmt.Errorf("read retained artifact %d: %w", stored.ArtifactID, err)
	}
	if int64(len(content)) != stored.ContentLength {
		return nil, fmt.Errorf("retained artifact %d length=%d, want %d", stored.ArtifactID, len(content), stored.ContentLength)
	}
	sum := sha256.Sum256(content)
	if got := hex.EncodeToString(sum[:]); !strings.EqualFold(got, stored.SHA256) {
		return nil, fmt.Errorf("retained artifact %d sha256=%s, want %s", stored.ArtifactID, got, stored.SHA256)
	}
	return content, nil
}
