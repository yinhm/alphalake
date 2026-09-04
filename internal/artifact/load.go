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

// LoadLatest returns the newest retained artifact for one provider locator and
// verifies its content hash before use. A missing metadata row is not an error.
func LoadLatest(ctx context.Context, db *sql.DB, root, source, dataset, sourceLocator string) (Stored, []byte, bool, error) {
	var stored Stored
	if db == nil {
		return stored, nil, false, errors.New("duckdb is nil")
	}
	root = strings.TrimSpace(root)
	source = strings.TrimSpace(source)
	dataset = strings.TrimSpace(dataset)
	sourceLocator = strings.TrimSpace(sourceLocator)
	if root == "" || source == "" || dataset == "" || sourceLocator == "" {
		return stored, nil, false, errors.New("artifact root, source, dataset, and source locator are required")
	}
	err := db.QueryRowContext(ctx, `
		SELECT artifact_id, sha256, content_length, local_path
		FROM meta.artifact
		WHERE source=? AND dataset=? AND source_locator=?
		ORDER BY fetched_at DESC, artifact_id DESC
		LIMIT 1
	`, source, dataset, sourceLocator).Scan(
		&stored.ArtifactID, &stored.SHA256, &stored.ContentLength, &stored.LocalPath,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return Stored{}, nil, false, nil
	}
	if err != nil {
		return Stored{}, nil, false, fmt.Errorf("query latest artifact: %w", err)
	}
	content, err := os.ReadFile(Resolve(root, stored))
	if err != nil {
		return Stored{}, nil, false, fmt.Errorf("read retained artifact %d: %w", stored.ArtifactID, err)
	}
	if int64(len(content)) != stored.ContentLength {
		return Stored{}, nil, false, fmt.Errorf("retained artifact %d length=%d, want %d", stored.ArtifactID, len(content), stored.ContentLength)
	}
	sum := sha256.Sum256(content)
	if got := hex.EncodeToString(sum[:]); !strings.EqualFold(got, stored.SHA256) {
		return Stored{}, nil, false, fmt.Errorf("retained artifact %d sha256=%s, want %s", stored.ArtifactID, got, stored.SHA256)
	}
	return stored, content, true, nil
}
