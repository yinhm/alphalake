package ingest

import (
	"context"
	"database/sql"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/source/chinabond"
	"github.com/yinhm/alphalake/internal/source/damodaran"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

// ReferenceOptions/ReferenceSummary retain the existing country-risk API aliases.
type ReferenceOptions = CountryRiskOptions
type ReferenceSummary = CountryRiskSummary

func SyncIndustryBeta(ctx context.Context, db *sql.DB, root string, options ReferenceOptions) (ReferenceSummary, error) {
	if options.Script == "" {
		options.Script = damodaran.BetaScript
	}
	feed := referenceFeed{damodaran.Source, damodaran.BetaDataset, damodaran.BetaURL, "application/vnd.ms-excel", damodaran.BetaParserVersion}
	return syncReference(ctx, db, root, options, feed, func(runID int64, stored artifact.Stored) (int64, bool, int, error) {
		s, hash, err := damodaran.ParseBeta(ctx, defaultPython(options.Python), options.Script, artifact.Resolve(root, stored))
		if err != nil {
			return 0, false, 0, err
		}
		id, inserted, err := duckstore.PublishIndustryBeta(ctx, db, runID, stored.ArtifactID, hash, s)
		return id, inserted, len(s.Observations), err
	})
}
func SyncCNYGovernmentYield(ctx context.Context, db *sql.DB, root string, options ReferenceOptions) (ReferenceSummary, error) {
	if options.Script == "" {
		options.Script = chinabond.Script
	}
	feed := referenceFeed{chinabond.Source, chinabond.Dataset, chinabond.URL, "text/html; charset=utf-8", chinabond.ParserVersion}
	return syncReference(ctx, db, root, options, feed, func(runID int64, stored artifact.Stored) (int64, bool, int, error) {
		s, hash, err := chinabond.Parse(ctx, defaultPython(options.Python), options.Script, artifact.Resolve(root, stored))
		if err != nil {
			return 0, false, 0, err
		}
		id, inserted, err := duckstore.PublishCNYGovernmentYield(ctx, db, runID, stored.ArtifactID, hash, s)
		return id, inserted, len(s.Observations), err
	})
}
