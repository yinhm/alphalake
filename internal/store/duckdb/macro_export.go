package duckdb

import (
	"context"
	"database/sql"
	"time"

	"github.com/yinhm/alphalake/internal/source/macro"
)

type MacroReadPoint struct {
	Start     string  `json:"period_start"`
	End       string  `json:"period_end"`
	Value     *string `json:"value"`
	Status    string  `json:"value_status"`
	ReleaseID int64   `json:"release_id"`
	Locator   string  `json:"source_locator"`
}
type MacroReadSeries struct {
	macro.Series
	Points []MacroReadPoint `json:"points"`
}
type MacroReadRelease struct {
	ReleaseID       int64      `json:"release_id"`
	ArtifactID      int64      `json:"artifact_id"`
	Source          string     `json:"source"`
	Dataset         string     `json:"dataset"`
	URL             string     `json:"url"`
	SHA256          string     `json:"sha256"`
	PublishedAt     *time.Time `json:"source_published_at"`
	Precision       string     `json:"publication_precision"`
	FirstAcquiredAt time.Time  `json:"first_acquired_at"`
	RecordedAt      time.Time  `json:"recorded_at"`
}
type MacroReadSnapshot struct {
	Contract   string             `json:"contract"`
	ExportedAt time.Time          `json:"exported_at"`
	Vintage    string             `json:"vintage"`
	Series     []MacroReadSeries  `json:"series"`
	Releases   []MacroReadRelease `json:"releases"`
}

// Freeze definitions and current observations in one transaction; share publication metadata once.
func ExportMacroReadSnapshot(ctx context.Context, db *sql.DB) (MacroReadSnapshot, error) {
	out := MacroReadSnapshot{Contract: "alphalake-macro-snapshot-v1", ExportedAt: time.Now().UTC(),
		Vintage: "latest_source_vintage_not_historical_pit", Series: []MacroReadSeries{}, Releases: []MacroReadRelease{}}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return out, err
	}
	defer tx.Rollback()
	series, err := ListMacroSeries(ctx, tx)
	if err != nil {
		return out, err
	}
	rows, err := QueryMacroData(ctx, tx, nil, "", "", false)
	if err != nil {
		return out, err
	}
	indices := map[string]int{}
	for _, s := range series {
		indices[s.Code] = len(out.Series)
		out.Series = append(out.Series, MacroReadSeries{Series: s.Series, Points: []MacroReadPoint{}})
	}
	seen := map[int64]bool{}
	for _, r := range rows {
		s := &out.Series[indices[r.SeriesCode]]
		e := r.Evidence
		s.Points = append(s.Points, MacroReadPoint{r.PeriodStart, r.PeriodEnd, r.Value, r.Status, e.ReleaseID, e.Locator})
		if !seen[e.ReleaseID] {
			seen[e.ReleaseID] = true
			out.Releases = append(out.Releases, MacroReadRelease{e.ReleaseID, e.ArtifactID, s.Source, s.Dataset, e.URL, e.SHA256, e.PublishedAt, e.PublicationPrecision, e.FirstAcquiredAt, e.RecordedAt})
		}
	}
	return out, tx.Commit()
}
