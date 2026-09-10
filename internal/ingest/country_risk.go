package ingest

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/source/damodaran"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type CountryRiskOptions struct {
	Python  string
	Script  string
	Offline bool
	Client  *http.Client
}
type CountryRiskSummary struct {
	RunID        int64 `json:"run_id"`
	ArtifactID   int64 `json:"artifact_id"`
	ReleaseID    int64 `json:"release_id"`
	Inserted     bool  `json:"inserted"`
	Observations int   `json:"observations"`
}

type referenceFeed struct{ source, dataset, url, mediaType, parserVersion string }

func SyncCountryRisk(ctx context.Context, db *sql.DB, root string, options CountryRiskOptions) (CountryRiskSummary, error) {
	if options.Script == "" {
		options.Script = damodaran.DefaultScript
	}
	feed := referenceFeed{damodaran.Source, damodaran.Dataset, damodaran.URL, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", damodaran.ParserVersion}
	return syncReference(ctx, db, root, options, feed, func(runID int64, stored artifact.Stored) (int64, bool, int, error) {
		snapshot, hash, err := damodaran.Parse(ctx, defaultPython(options.Python), options.Script, artifact.Resolve(root, stored))
		if err != nil {
			return 0, false, 0, err
		}
		id, inserted, err := duckstore.PublishCountryRisk(ctx, db, runID, stored.ArtifactID, hash, snapshot)
		return id, inserted, len(snapshot.Observations), err
	})
}
func defaultPython(python string) string {
	if python == "" {
		return "python3"
	}
	return python
}

func syncReference(ctx context.Context, db *sql.DB, root string, options CountryRiskOptions, feed referenceFeed, publish func(int64, artifact.Stored) (int64, bool, int, error)) (out CountryRiskSummary, err error) {
	out.RunID, err = duckstore.StartIngestRun(ctx, db, feed.source, feed.dataset, nil)
	if err != nil {
		return
	}
	defer func() {
		status := duckstore.IngestRunCompleted
		if err != nil {
			status = duckstore.IngestRunFailed
		}
		if errors.Is(err, context.Canceled) {
			status = duckstore.IngestRunCanceled
		}
		cleanup, cancel := context.WithTimeout(context.WithoutCancel(ctx), 10*time.Second)
		defer cancel()
		if err != nil {
			diagnosticErr := duckstore.RecordIngestDiagnostics(cleanup, db, out.RunID, feed.source, feed.dataset, []duckstore.IngestDiagnostic{{RuleCode: "reference_publication_failed", Severity: "error", SubjectType: "dataset", SubjectKey: feed.url, Details: err.Error()}})
			err = errors.Join(err, diagnosticErr)
		}
		finishErr := duckstore.FinishIngestRun(cleanup, db, out.RunID, status, nil, err)
		err = errors.Join(err, finishErr)
	}()
	var stored artifact.Stored
	if options.Offline {

		var id int64
		err = db.QueryRowContext(ctx, `WITH latest AS (
          SELECT release_id FROM meta.dataset_release WHERE source=? AND dataset=?
          ORDER BY recorded_at DESC,release_id DESC LIMIT 1)
          SELECT a.artifact_id FROM latest r
          JOIN meta.dataset_release_artifact a ON a.release_id=r.release_id AND a.role='data'
          JOIN meta.artifact raw ON raw.artifact_id=a.artifact_id
          WHERE raw.source_locator=?`, feed.source, feed.dataset, feed.url).Scan(&id)
		if err != nil {
			return out, fmt.Errorf("no published reference archive: %w", err)
		}
		stored, _, err = artifact.LoadByID(ctx, db, root, id)
		if err != nil {
			return
		}

	} else {
		stored, err = fetchReferenceArtifact(ctx, db, root, options, feed, out.RunID)
		if err != nil {
			return
		}

	}
	out.ArtifactID = stored.ArtifactID
	out.ReleaseID, out.Inserted, out.Observations, err = publish(out.RunID, stored)
	if err != nil {
		out.Observations = 0
	}
	return
}

// fetchReferenceArtifact 只取得并归档单份原文；发布完整性由具体来源校验。
func fetchReferenceArtifact(ctx context.Context, db *sql.DB, root string, options ReferenceOptions, feed referenceFeed, runID int64) (artifact.Stored, error) {
	var empty artifact.Stored
	client := options.Client
	if client == nil {
		client = &http.Client{Timeout: 45 * time.Second}
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, feed.url, nil)
	if err != nil {
		return empty, err
	}
	if feed.source == "bse" {
		req.Header.Set("User-Agent", "AlphaLake/1.0")
		req.Header.Set("Accept", "text/html")
	}
	resp, err := client.Do(req)
	if err != nil {
		return empty, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return empty, fmt.Errorf("reference HTTP %d", resp.StatusCode)
	}
	limit := 16 << 20
	if feed.source == "hkex" || feed.source == damodaran.Source && feed.dataset == damodaran.CompanyIndustryDataset {
		limit = 64 << 20
	}
	if feed.source == "bse" {
		limit = 2 << 20
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, int64(limit)+1))
	if err != nil {
		return empty, err
	}
	if len(body) > limit || len(body) == 0 {
		return empty, errors.New("empty/oversized reference response")
	}
	return artifact.Persist(ctx, db, root, artifact.Input{Source: feed.source, Dataset: feed.dataset, SourceLocator: feed.url, FetchedAt: time.Now().UTC(), MediaType: feed.mediaType, ParserVersion: feed.parserVersion, IngestRunID: &runID, Content: body})
}
