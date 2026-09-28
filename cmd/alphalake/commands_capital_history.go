package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"

	store "github.com/yinhm/alphalake/internal/store/duckdb"
)

// Review and rebuild an unpublished copy using existing filing links. The
// caller compares the completed file before atomically replacing the source.
func runCapitalHistory(ctx context.Context, args []string) error {
	if len(args) != 2 {
		return usageError("extend-capital-history <source-db> <new-candidate-db>")
	}
	source, err := store.OpenReadOnly(ctx, args[0])
	if err != nil {
		return err
	}
	defer source.Close()
	version, err := store.CurrentSchemaVersion(ctx, source)
	if err != nil {
		return err
	}
	if version != store.SchemaVersion {
		return fmt.Errorf("capital history requires current schema %d", store.SchemaVersion)
	}
	if _, err = os.Stat(args[0] + ".wal"); !os.IsNotExist(err) {
		return fmt.Errorf("source must be checkpointed and have no WAL before copying")
	}
	input, err := os.Open(args[0])
	if err != nil {
		return err
	}
	defer input.Close()
	output, err := os.OpenFile(args[1], os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	hash := sha256.New()
	_, err = io.Copy(io.MultiWriter(output, hash), input)
	if err == nil {
		err = output.Sync()
	}
	closeErr := output.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if err = source.Close(); err != nil {
		return err
	}
	candidate, err := store.Open(ctx, args[1])
	if err != nil {
		return err
	}
	defer candidate.Close()
	run, result, err := store.RebuildCapitalHistoryCopy(ctx, candidate)
	if err != nil {
		return fmt.Errorf("unpublished candidate %s failed: %w", args[1], err)
	}
	if err = candidate.Close(); err != nil {
		return err
	}
	fmt.Printf("unpublished capital history candidate: %s source_sha256=%s run=%d inserted=%d updated=%d removed=%d rejected=%d; existing filing links retained; compare before publication\n", args[1], hex.EncodeToString(hash.Sum(nil)), run, result.Inserted, result.Updated, result.Removed, result.Rejected)
	return nil
}
