package ingest

import (
	"context"
	"database/sql"
	"fmt"
	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/source/damodaran"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func SyncNativeReference(ctx context.Context, db *sql.DB, root, stem string, options ReferenceOptions) (ReferenceSummary, error) {
	valid := false
	for _, s := range damodaran.NativeReferenceFiles {
		if s == stem {
			valid = true
		}
	}
	if !valid {
		return ReferenceSummary{}, fmt.Errorf("unsupported native reference file: %s", stem)
	}
	if options.Script == "" {
		options.Script = damodaran.NativeReferenceScript
	}
	feed := referenceFeed{damodaran.Source, damodaran.NativeReferenceDataset(stem), damodaran.NativeReferenceURL(stem), "application/vnd.ms-excel", damodaran.NativeReferenceVersion}
	return syncReference(ctx, db, root, options, feed, func(runID int64, stored artifact.Stored) (int64, bool, int, error) {
		s, hash, err := damodaran.ParseNativeReference(ctx, defaultPython(options.Python), options.Script, artifact.Resolve(root, stored))
		if err != nil {
			return 0, false, 0, err
		}
		if s.FileStem != stem {
			return 0, false, 0, fmt.Errorf("reference filename mismatch")
		}
		id, inserted, err := duckstore.PublishNativeReference(ctx, db, runID, stored.ArtifactID, hash, s)
		return id, inserted, len(s.Observations), err
	})
}
