package ingest

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
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

// Real archived inputs through production publication/export; source parsing has
// separate mandatory CI coverage. Optional export feeds the Python HTTP test.
func TestWACCReferenceExport(t *testing.T) {
	ctx := t.Context()
	dir := t.TempDir()
	db, err := duckstore.OpenAndMigrate(ctx, filepath.Join(dir, "wacc.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	ids := []int64{}
	firstSeen := time.Date(2026, 9, 9, 12, 0, 0, 123456000, time.UTC)
	for _, kind := range []string{"country", "beta", "yield", "credit"} {
		source, dataset, url, rawFile, jsonFile := damodaran.Source, damodaran.Dataset, damodaran.URL, "../source/damodaran/testdata/ctrypremJuly26.xlsx", "../source/damodaran/testdata/expected.json"
		if kind == "beta" {
			dataset, url, rawFile, jsonFile = damodaran.BetaDataset, damodaran.BetaURL, "../source/damodaran/testdata/betaGlobal.xls", "../source/damodaran/testdata/beta-expected.json"
		}
		if kind == "yield" {
			source, dataset, url, rawFile, jsonFile = chinabond.Source, chinabond.Dataset, chinabond.URL, "../source/chinabond/testdata/curve.html", "../source/chinabond/testdata/expected.json"
		}
		if kind == "credit" {
			source, dataset, url, rawFile, jsonFile = damodaran.Source, damodaran.CreditDataset, damodaran.CreditURL, "../source/damodaran/testdata/ratings.html", "../source/damodaran/testdata/ratings-expected.json"
		}
		raw, err := os.ReadFile(rawFile)
		if err != nil {
			t.Fatal(err)
		}
		body, err := os.ReadFile(jsonFile)
		if err != nil {
			t.Fatal(err)
		}
		run, err := duckstore.StartIngestRun(ctx, db, source, dataset, nil)
		if err != nil {
			t.Fatal(err)
		}
		a, err := artifact.Persist(ctx, db, filepath.Join(dir, "raw"), artifact.Input{Source: source, Dataset: dataset, SourceLocator: url, FetchedAt: firstSeen, Content: raw})
		if err != nil {
			t.Fatal(err)
		}
		var id int64
		var inserted bool
		switch kind {
		case "country":
			var s damodaran.Snapshot
			if err = json.Unmarshal(body, &s); err == nil {
				id, inserted, err = duckstore.PublishCountryRisk(ctx, db, run, a.ArtifactID, strings.Repeat("a", 64), s)
			}
		case "beta":
			var s damodaran.BetaSnapshot
			if err = json.Unmarshal(body, &s); err == nil {
				id, inserted, err = duckstore.PublishIndustryBeta(ctx, db, run, a.ArtifactID, strings.Repeat("b", 64), s)
			}
		case "credit":
			var s damodaran.CreditSnapshot
			if err = json.Unmarshal(body, &s); err == nil {
				id, inserted, err = duckstore.PublishCreditSpreads(ctx, db, run, a.ArtifactID, strings.Repeat("d", 64), s)
			}
		case "yield":
			var s chinabond.Snapshot
			if err = json.Unmarshal(body, &s); err == nil {
				id, inserted, err = duckstore.PublishCNYGovernmentYield(ctx, db, run, a.ArtifactID, strings.Repeat("c", 64), s)
			}
		}
		if err != nil || !inserted {
			t.Fatal(kind, err)
		}
		ids = append(ids, id)
		if err := duckstore.FinishIngestRun(ctx, db, run, duckstore.IngestRunCompleted, nil, nil); err != nil {
			t.Fatal(err)
		}
	}
	// Equality at the public-availability boundary is inclusive; system-time
	// knowledge remains independently filterable.
	packet, err := duckstore.ExportWACCReferences(ctx, db, firstSeen, nil, ids[0], ids[1], ids[2])
	if err != nil {
		t.Fatal(err)
	}
	financial, err := duckstore.ExportValuationData(ctx, db, "999999", time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC), firstSeen)
	if err != nil || financial["information_as_of"] != packet["information_as_of"] {
		t.Fatalf("financial/reference cutoff precision mismatch: %v", err)
	}
	if _, err := duckstore.ExportWACCReferences(ctx, db, firstSeen.Add(-time.Microsecond), nil, ids[0], ids[1], ids[2]); err == nil {
		t.Fatal("future releases leaked")
	}
	early := firstSeen.Add(-time.Second)
	if _, err := duckstore.ExportWACCReferences(ctx, db, firstSeen, &early, ids[0], ids[1], ids[2]); err == nil {
		t.Fatal("recorded cutoff ignored")
	}
	if _, err := duckstore.ExportWACCReferences(ctx, db, firstSeen, nil, ids[1], ids[0], ids[2]); err == nil {
		t.Fatal("wrong dataset binding accepted")
	}
	// Repeated export of explicit releases is stable.
	again, err := duckstore.ExportWACCReferences(ctx, db, firstSeen, nil, ids[0], ids[1], ids[2])
	if err != nil {
		t.Fatal(err)
	}
	want, _ := json.Marshal(packet)
	got, _ := json.Marshal(again)
	if string(want) != string(got) {
		t.Fatal("unstable pinned export")
	}
	creditPacket, err := duckstore.ExportWACCReferences(ctx, db, firstSeen, nil, ids[0], ids[1], ids[2], ids[3])
	if err != nil {
		t.Fatal(err)
	}
	creditJSON, _ := json.Marshal(creditPacket)
	if output := os.Getenv("ALPHALAKE_WACC_EXPORT_DIR"); output != "" {
		if err := os.MkdirAll(output, 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(output, "credit-references.json"), creditJSON, 0644); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(output, "references.json"), want, 0644); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := db.ExecContext(ctx, `DELETE FROM market.yield_curve_point WHERE tenor_months=120`); err != nil {
		t.Fatal(err)
	}
	if _, err := duckstore.ExportWACCReferences(ctx, db, firstSeen, nil, ids[0], ids[1], ids[2]); err == nil {
		t.Fatal("incomplete curve exported")
	}
}
