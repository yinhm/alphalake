package ingest

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/source/macro"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type MacroOptions struct {
	Source  string
	Offline bool
	Client  *macro.Client
}
type MacroDatasetResult struct {
	Dataset            string `json:"dataset"`
	Source             string `json:"source"`
	Status             string `json:"status"`
	RunID              int64  `json:"run_id"`
	ReleaseID          int64  `json:"release_id"`
	Inserted           bool   `json:"inserted"`
	Series             int    `json:"series"`
	Observations       int    `json:"observations"`
	NewObservations    int    `json:"new_observations"`
	FuturePlaceholders int    `json:"future_placeholders_skipped"`
	LatestPeriod       string `json:"latest_period"`
	URL                string `json:"url"`
	Error              string `json:"error,omitempty"`
}
type MacroSummary struct {
	Contract          string               `json:"contract"`
	Mode              string               `json:"source_mode"`
	Status            string               `json:"status"`
	StartedAt         time.Time            `json:"started_at"`
	FinishedAt        time.Time            `json:"finished_at"`
	Requested         int                  `json:"requested_datasets"`
	Completed         int                  `json:"completed_datasets"`
	NewObservations   int                  `json:"new_observations"`
	DiscoveryFailures map[string]string    `json:"discovery_failures"`
	Datasets          []MacroDatasetResult `json:"datasets"`
}

func SyncMacroData(ctx context.Context, db *sql.DB, root string, options MacroOptions) (MacroSummary, error) {
	out := MacroSummary{Contract: "alphalake-macro-sync-v1", Mode: "online", Status: "completed", StartedAt: time.Now().UTC(), DiscoveryFailures: map[string]string{}, Datasets: []MacroDatasetResult{}}
	if options.Source == "" {
		options.Source = "all"
	}
	if options.Source != "all" && options.Source != "nbs" && options.Source != "pbc" && options.Source != "mof" {
		return out, fmt.Errorf("macro source must be all, nbs, pbc or mof")
	}
	if options.Offline {
		out.Mode = "offline"
	}
	client := options.Client
	if client == nil {
		client = macro.NewClient()
	}
	if client.HTTP == nil {
		return out, fmt.Errorf("macro HTTP client missing")
	}
	addresses := map[string]string{}
	var failures []error
	if !options.Offline {
		for _, source := range []string{"nbs", "pbc", "mof"} {
			if options.Source != "all" && options.Source != source {
				continue
			}
			run, err := duckstore.StartIngestRun(ctx, db, source, "macro_catalogue", nil)
			if err != nil {
				return out, err
			}
			discovery := client.Discover(ctx, source)
			for _, page := range discovery.Pages {
				_, err = artifact.Persist(ctx, db, root, artifact.Input{Source: source, Dataset: "macro_catalogue", SourceLocator: page.URL, FetchedAt: page.FetchedAt, MediaType: "text/html", ParserVersion: macro.ParserVersion, IngestRunID: &run, Content: page.Raw})
				if err != nil {
					discovery.Failures[page.URL] = err.Error()
				}
			}
			for key, address := range discovery.URLs {
				addresses[key] = address
			}
			state := duckstore.IngestRunCompleted
			if len(discovery.Failures) > 0 {
				state = duckstore.IngestRunPartial
				diagnostics := []duckstore.IngestDiagnostic{}
				for key, reason := range discovery.Failures {
					out.DiscoveryFailures[source+"/"+key] = reason
					diagnostics = append(diagnostics, duckstore.IngestDiagnostic{RuleCode: "macro_catalogue_incomplete", Severity: "error", SubjectType: "dataset", SubjectKey: key, Details: reason})
				}
				if err = duckstore.RecordIngestDiagnostics(context.WithoutCancel(ctx), db, run, source, "macro_catalogue", diagnostics); err != nil {
					return out, err
				}
			}
			if err = duckstore.FinishIngestRun(context.WithoutCancel(ctx), db, run, state, nil, nil); err != nil {
				return out, err
			}
		}
	}
	for _, feed := range macro.Feeds() {
		if options.Source != "all" && options.Source != feed.Source {
			continue
		}
		out.Requested++
		row := MacroDatasetResult{Dataset: feed.Key, Source: feed.Source, Status: "failed"}
		address := addresses[feed.Key]
		if options.Offline {
			err := db.QueryRowContext(ctx, `SELECT a.source_locator FROM meta.dataset_release r JOIN meta.dataset_release_artifact link ON link.release_id=r.release_id AND link.role='data' JOIN meta.artifact a USING(artifact_id) WHERE r.source=? AND r.dataset=? ORDER BY r.recorded_at DESC,r.release_id DESC LIMIT 1`, feed.Source, feed.Dataset()).Scan(&address)
			if err != nil {
				row.Error = "no verified published macro archive: " + err.Error()
				out.Datasets = append(out.Datasets, row)
				failures = append(failures, errors.New(row.Error))
				continue
			}
		}
		if err := macro.ValidateURL(feed.Source, address); err != nil {
			row.Error = "official discovery/URL unavailable: " + err.Error()
			out.Datasets = append(out.Datasets, row)
			failures = append(failures, errors.New(row.Error))
			continue
		}
		row.URL = address
		result, err := syncReference(ctx, db, root, ReferenceOptions{Offline: options.Offline, Client: client.HTTP}, referenceFeed{feed.Source, feed.Dataset(), address, "text/html", macro.ParserVersion}, func(runID int64, stored artifact.Stored) (int64, bool, int, error) {
			raw, err := os.ReadFile(artifact.Resolve(root, stored))
			if err != nil {
				return 0, false, 0, err
			}
			snapshot, err := macro.Parse(raw, feed, address)
			if err != nil {
				return 0, false, 0, err
			}
			row.Series = len(snapshot.Series)
			row.Observations = len(snapshot.Observations)
			row.LatestPeriod = snapshot.ObservationDate
			row.FuturePlaceholders = snapshot.FuturePlaceholders
			id, inserted, err := duckstore.PublishMacroData(ctx, db, runID, stored.ArtifactID, snapshot)
			return id, inserted, len(snapshot.Observations), err
		})
		row.RunID = result.RunID
		row.ReleaseID = result.ReleaseID
		row.Inserted = result.Inserted
		if result.Inserted && err == nil {
			row.NewObservations = result.Observations
		}
		if err != nil {
			row.Error = err.Error()
			failures = append(failures, err)
		} else {
			row.Status = "completed"
			out.Completed++
			out.NewObservations += row.NewObservations
		}
		out.Datasets = append(out.Datasets, row)
	}
	out.FinishedAt = time.Now().UTC()
	if len(failures) > 0 || len(out.DiscoveryFailures) > 0 {
		out.Status = "partial"
		if len(failures) == 0 {
			failures = append(failures, errors.New("macro discovery incomplete; see source receipts"))
		}
	}
	return out, errors.Join(failures...)
}
