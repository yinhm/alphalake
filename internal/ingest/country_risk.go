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

func SyncCountryRisk(ctx context.Context, db *sql.DB, root string, options CountryRiskOptions) (out CountryRiskSummary, err error) {
	if options.Python == "" {
		options.Python = "python3"
	}
	if options.Script == "" {
		options.Script = damodaran.DefaultScript
	}
	out.RunID, err = duckstore.StartIngestRun(ctx, db, damodaran.Source, damodaran.Dataset, nil)
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
			diagnosticErr := duckstore.RecordIngestDiagnostics(cleanup, db, out.RunID, damodaran.Source, damodaran.Dataset, []duckstore.IngestDiagnostic{{RuleCode: "country_risk_publication_failed", Severity: "error", SubjectType: "dataset", SubjectKey: damodaran.URL, Details: err.Error()}})
			err = errors.Join(err, diagnosticErr)
		}
		finishErr := duckstore.FinishIngestRun(cleanup, db, out.RunID, status, nil, err)
		err = errors.Join(err, finishErr)
	}()
	var stored artifact.Stored
	if options.Offline {

		var id int64
		err = db.QueryRowContext(ctx, `SELECT a.artifact_id FROM meta.dataset_release r
          JOIN meta.dataset_release_artifact a ON a.release_id=r.release_id AND a.role='data'
          WHERE r.source=? AND r.dataset=? ORDER BY r.recorded_at DESC,r.release_id DESC LIMIT 1`, damodaran.Source, damodaran.Dataset).Scan(&id)
		if err != nil {
			return out, fmt.Errorf("no published country-risk archive: %w", err)
		}
		stored, _, err = artifact.LoadByID(ctx, db, root, id)
		if err != nil {
			return
		}

	} else {
		client := options.Client
		if client == nil {
			client = &http.Client{Timeout: 45 * time.Second}
		}
		req, requestErr := http.NewRequestWithContext(ctx, http.MethodGet, damodaran.URL, nil)
		if requestErr != nil {
			return out, requestErr
		}
		resp, fetchErr := client.Do(req)
		if fetchErr != nil {
			return out, fetchErr
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			return out, fmt.Errorf("country risk HTTP %d", resp.StatusCode)
		}
		const limit = 16 << 20
		body, readErr := io.ReadAll(io.LimitReader(resp.Body, limit+1))
		if readErr != nil {
			return out, readErr
		}
		if len(body) > limit || len(body) == 0 {
			return out, errors.New("empty/oversized country-risk response")
		}
		stored, err = artifact.Persist(ctx, db, root, artifact.Input{Source: damodaran.Source, Dataset: damodaran.Dataset, SourceLocator: damodaran.URL, FetchedAt: time.Now().UTC(), MediaType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ParserVersion: damodaran.ParserVersion, IngestRunID: &out.RunID, Content: body})
		if err != nil {
			return
		}
	}
	out.ArtifactID = stored.ArtifactID
	snapshot, parserHash, parseErr := damodaran.Parse(ctx, options.Python, options.Script, artifact.Resolve(root, stored))
	if parseErr != nil {
		return out, parseErr
	}
	out.ReleaseID, out.Inserted, err = duckstore.PublishCountryRisk(ctx, db, out.RunID, stored.ArtifactID, parserHash, snapshot)
	if err == nil {
		out.Observations = len(snapshot.Observations)
	}
	return
}
