package duckdb

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/source/damodaran"
)

func TestCountryRiskPublication(t *testing.T) {
	ctx := t.Context()
	dir := t.TempDir()
	db, err := OpenInitialized(ctx, filepath.Join(dir, "risk.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	body, err := os.ReadFile("../../source/damodaran/testdata/expected.json")
	if err != nil {
		t.Fatal(err)
	}
	var s damodaran.Snapshot
	if err := json.Unmarshal(body, &s); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile("../../source/damodaran/testdata/ctrypremJuly26.xlsx")
	if err != nil {
		t.Fatal(err)
	}
	run, err := StartIngestRun(ctx, db, damodaran.Source, damodaran.Dataset, nil)
	if err != nil {
		t.Fatal(err)
	}
	a, err := artifact.Persist(ctx, db, filepath.Join(dir, "raw"), artifact.Input{Source: damodaran.Source, Dataset: damodaran.Dataset, SourceLocator: damodaran.URL, FetchedAt: time.Now().UTC(), Content: raw})
	if err != nil {
		t.Fatal(err)
	}
	parserHash := strings.Repeat("a", 64)
	for _, badID := range []int64{0, a.ArtifactID + 100} {
		if _, _, err := PublishCountryRisk(ctx, db, run, badID, parserHash, s); err == nil {
			t.Fatal("orphan artifact accepted")
		}
	}
	// Force failure after release/link insertion: the observation unique key must
	// roll the entire transaction back, including its checkpoint and release row.
	if _, err := db.ExecContext(ctx, `INSERT INTO reference.equity_risk_premium VALUES (999,1,1,'orphan','0','fraction','market_group','mature','2026-07-01','mature_market_erp','implied_mature',0,'reported')`); err != nil {
		t.Fatal(err)
	}
	if _, _, err := PublishCountryRisk(ctx, db, run, a.ArtifactID, parserHash, s); err == nil {
		t.Fatal("injected conflict not caught")
	}
	var count int
	if err := db.QueryRowContext(ctx, `SELECT (SELECT count(*) FROM meta.dataset_release)+(SELECT count(*) FROM meta.dataset_release_artifact)+(SELECT count(*) FROM meta.checkpoint WHERE source='damodaran')`).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatal("partial publication survived", count)
	}
	if _, err := db.ExecContext(ctx, `DELETE FROM reference.equity_risk_premium WHERE observation_id=999`); err != nil {
		t.Fatal(err)
	}
	id, inserted, err := PublishCountryRisk(ctx, db, run, a.ArtifactID, parserHash, s)
	if err != nil || !inserted {
		t.Fatal(id, inserted, err)
	}
	id2, inserted, err := PublishCountryRisk(ctx, db, run, a.ArtifactID, parserHash, s)
	if err != nil || inserted || id2 != id {
		t.Fatal("replay", id2, inserted, err)
	}
	if err := db.QueryRowContext(ctx, `SELECT count(*) FROM reference.risk_observation`).Scan(&count); err != nil || count != 10 {
		t.Fatal(count, err)
	}
	if err := FinishIngestRun(ctx, db, run, IngestRunCompleted, nil, nil); err != nil {
		t.Fatal(err)
	}
	if _, _, err := PublishCountryRisk(ctx, db, run, a.ArtifactID, parserHash, s); err == nil {
		t.Fatal("terminal run accepted")
	}
}
