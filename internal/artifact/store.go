package artifact

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode"
)

type Input struct {
	Source        string
	Dataset       string
	SourceLocator string
	FetchedAt     time.Time
	MediaType     string
	ParserVersion string
	IngestRunID   *int64
	Content       []byte
}

type Stored struct {
	ArtifactID    int64
	SHA256        string
	ContentLength int64
	LocalPath     string // relative to the configured artifact root
}

// Persist stores raw provider bytes immutably under a content-addressed path and
// records their provenance in meta.artifact. Re-fetching the same locator/content
// pair is idempotent. The database path is root-relative so the raw lake can move
// together with its DuckDB database without rewriting metadata.
func Persist(ctx context.Context, db *sql.DB, root string, input Input) (Stored, error) {
	var out Stored
	if db == nil {
		return out, errors.New("duckdb is nil")
	}
	root = strings.TrimSpace(root)
	input.Source = strings.TrimSpace(input.Source)
	input.Dataset = strings.TrimSpace(input.Dataset)
	input.SourceLocator = strings.TrimSpace(input.SourceLocator)
	if root == "" || input.Source == "" || input.Dataset == "" || input.SourceLocator == "" {
		return out, errors.New("artifact root, source, dataset, and source locator are required")
	}
	if input.FetchedAt.IsZero() {
		return out, errors.New("artifact fetched_at is required")
	}
	if input.IngestRunID != nil && *input.IngestRunID <= 0 {
		return out, errors.New("artifact ingest run ID must be positive")
	}

	sum := sha256.Sum256(input.Content)
	out.SHA256 = hex.EncodeToString(sum[:])
	out.ContentLength = int64(len(input.Content))
	ext := safeExtension(input.SourceLocator)
	rel := filepath.Join(safeSegment(input.Source), safeSegment(input.Dataset), out.SHA256[:2], out.SHA256+ext)
	out.LocalPath = filepath.ToSlash(rel)
	full := filepath.Join(root, rel)
	if err := persistBytes(full, input.Content, out.SHA256); err != nil {
		return Stored{}, err
	}

	var runID any
	if input.IngestRunID != nil {
		runID = *input.IngestRunID
	}
	var artifactID int64
	err := db.QueryRowContext(ctx, `
		INSERT INTO meta.artifact (
			source, dataset, source_locator, fetched_at,
			sha256, content_length, media_type, local_path,
			parser_version, ingest_run_id
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(source, dataset, source_locator, sha256) DO NOTHING
		RETURNING artifact_id
	`,
		input.Source, input.Dataset, input.SourceLocator, input.FetchedAt,
		out.SHA256, out.ContentLength, nullableString(input.MediaType), out.LocalPath,
		nullableString(input.ParserVersion), runID,
	).Scan(&artifactID)
	if errors.Is(err, sql.ErrNoRows) {
		err = db.QueryRowContext(ctx, `
			SELECT artifact_id
			FROM meta.artifact
			WHERE source=? AND dataset=? AND source_locator=? AND sha256=?
		`, input.Source, input.Dataset, input.SourceLocator, out.SHA256).Scan(&artifactID)
	}
	if err != nil {
		return Stored{}, fmt.Errorf("record artifact metadata: %w", err)
	}
	out.ArtifactID = artifactID
	return out, nil
}

func Resolve(root string, stored Stored) string {
	return filepath.Join(root, filepath.FromSlash(stored.LocalPath))
}

func persistBytes(path string, content []byte, wantSHA string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("create artifact directory: %w", err)
	}
	if _, err := os.Stat(path); err == nil {
		ok, err := fileHasSHA(path, wantSHA)
		if err != nil {
			return err
		}
		if !ok {
			return fmt.Errorf("artifact content-addressed path %q contains unexpected bytes", path)
		}
		return nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("stat artifact path: %w", err)
	}

	tmp, err := os.CreateTemp(filepath.Dir(path), ".alphalake-artifact-*")
	if err != nil {
		return fmt.Errorf("create artifact temp file: %w", err)
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if _, err := tmp.Write(content); err != nil {
		tmp.Close()
		return fmt.Errorf("write artifact temp file: %w", err)
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return fmt.Errorf("sync artifact temp file: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("close artifact temp file: %w", err)
	}
	if err := os.Rename(tmpName, path); err != nil {
		if _, statErr := os.Stat(path); statErr == nil {
			ok, hashErr := fileHasSHA(path, wantSHA)
			if hashErr == nil && ok {
				return nil
			}
		}
		return fmt.Errorf("publish artifact: %w", err)
	}
	return nil
}

func fileHasSHA(path, want string) (bool, error) {
	f, err := os.Open(path)
	if err != nil {
		return false, fmt.Errorf("open existing artifact: %w", err)
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return false, fmt.Errorf("hash existing artifact: %w", err)
	}
	return hex.EncodeToString(h.Sum(nil)) == want, nil
}

func safeSegment(v string) string {
	v = strings.TrimSpace(v)
	var b strings.Builder
	for _, r := range v {
		if unicode.IsLetter(r) || unicode.IsDigit(r) || r == '-' || r == '_' || r == '.' {
			b.WriteRune(r)
		} else {
			b.WriteByte('_')
		}
	}
	if b.Len() == 0 {
		return "_"
	}
	return b.String()
}

func safeExtension(locator string) string {
	ext := strings.ToLower(filepath.Ext(locator))
	if len(ext) < 2 || len(ext) > 10 {
		return ""
	}
	for _, r := range ext[1:] {
		if !unicode.IsLetter(r) && !unicode.IsDigit(r) {
			return ""
		}
	}
	return ext
}

func nullableString(v string) any {
	if strings.TrimSpace(v) == "" {
		return nil
	}
	return v
}
