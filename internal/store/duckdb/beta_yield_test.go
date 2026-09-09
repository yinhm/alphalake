package duckdb

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/source/chinabond"
	"github.com/yinhm/alphalake/internal/source/damodaran"
)

func TestBetaYieldAtomicPublication(t *testing.T) {
	for _, kind := range []string{"beta", "yield"} {
		t.Run(kind, func(t *testing.T) {
			ctx := t.Context()
			dir := t.TempDir()
			db, err := OpenAndMigrate(ctx, filepath.Join(dir, "reference.duckdb"))
			if err != nil {
				t.Fatal(err)
			}
			defer db.Close()
			source, dataset, url, fixture := damodaran.Source, damodaran.BetaDataset, damodaran.BetaURL, "../../source/damodaran/testdata/betaGlobal.xls"
			if kind == "yield" {
				source, dataset, url, fixture = chinabond.Source, chinabond.Dataset, chinabond.URL, "../../source/chinabond/testdata/curve.html"
			}
			run, err := StartIngestRun(ctx, db, source, dataset, nil)
			if err != nil {
				t.Fatal(err)
			}
			raw, err := os.ReadFile(fixture)
			if err != nil {
				t.Fatal(err)
			}
			a, err := artifact.Persist(ctx, db, filepath.Join(dir, "raw"), artifact.Input{Source: source, Dataset: dataset, SourceLocator: url, FetchedAt: time.Now().UTC(), Content: raw})
			if err != nil {
				t.Fatal(err)
			}
			var publish func() (int64, bool, error)
			var table string
			if kind == "beta" {
				body, err := os.ReadFile("../../source/damodaran/testdata/beta-expected.json")
				if err != nil {
					t.Fatal(err)
				}
				var s damodaran.BetaSnapshot
				if err := json.Unmarshal(body, &s); err != nil {
					t.Fatal(err)
				}
				publish = func() (int64, bool, error) {
					return PublishIndustryBeta(ctx, db, run, a.ArtifactID, strings.Repeat("a", 64), s)
				}
				_, err = db.ExecContext(ctx, `INSERT INTO reference.industry_stat VALUES (999,1,1,'orphan','0','dimensionless',1,'global','2026-01-05','beta_unlevered','provider_marginal_tax_0.253700000000','provider_estimate',1,0,'reported')`)
				table = "reference.industry_stat"
			} else {
				body, err := os.ReadFile("../../source/chinabond/testdata/expected.json")
				if err != nil {
					t.Fatal(err)
				}
				var s chinabond.Snapshot
				if err := json.Unmarshal(body, &s); err != nil {
					t.Fatal(err)
				}
				publish = func() (int64, bool, error) {
					return PublishCNYGovernmentYield(ctx, db, run, a.ArtifactID, strings.Repeat("a", 64), s)
				}
				_, err = db.ExecContext(ctx, `INSERT INTO market.yield_curve_point VALUES (999,1,1,'orphan','0','percent','chinabond_government_pbc','2026-09-09','CNY',3,'yield_to_maturity','unknown','unknown',0)`)
				table = "market.yield_curve_point"
			}
			if err != nil {
				t.Fatal(err)
			}
			if _, _, err := publish(); err == nil {
				t.Fatal("injected observation conflict accepted")
			}
			var n int
			err = db.QueryRowContext(ctx, `SELECT (SELECT count(*) FROM meta.dataset_release)+(SELECT count(*) FROM meta.dataset_release_artifact)+(SELECT count(*) FROM meta.checkpoint)+(SELECT count(*) FROM classification.node)+(SELECT count(*) FROM classification.taxonomy)`).Scan(&n)
			if err != nil || n != 0 {
				t.Fatal("partial release/catalog/checkpoint survived rollback", n, err)
			}
			if _, err := db.ExecContext(ctx, `DELETE FROM `+table+` WHERE observation_id=999`); err != nil {
				t.Fatal(err)
			}
			id, inserted, err := publish()
			if err != nil || !inserted {
				t.Fatal(id, inserted, err)
			}
			id2, inserted, err := publish()
			if err != nil || inserted || id2 != id {
				t.Fatal(id2, inserted, err)
			}
			if _, err := db.ExecContext(ctx, `UPDATE `+table+` SET value=value+0.01 WHERE observation_id=(SELECT min(observation_id) FROM `+table+`)`); err != nil {
				t.Fatal(err)
			}
			if _, _, err := publish(); err == nil {
				t.Fatal("changed published observation silently accepted")
			}
		})
	}
}
