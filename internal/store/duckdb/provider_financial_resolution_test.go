package duckdb

import (
	"context"
	"path/filepath"
	"testing"
	"time"
)

func TestProviderFinancialResolutionAcknowledgementSurvivesReplayAndYieldsToResolution(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "resolution.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	period := time.Date(2001, 12, 31, 0, 0, 0, 0, time.UTC)
	pending := ProviderFinancialResolutionInput{
		ArtifactID: 101, Source: "tdx", SourceFile: "gpcw20011231.zip",
		ReportPeriod: period, ProviderCode: "870001", MarketMarker: 7,
		Reason: "no historical identity evidence",
	}
	state, err := ApplyProviderFinancialResolutions(ctx, db, 1, []ProviderFinancialResolutionInput{pending})
	if err != nil {
		t.Fatal(err)
	}
	if state.Pending != 1 {
		t.Fatalf("state=%#v, want pending=1", state)
	}
	changed, err := AcknowledgeProviderFinancialResolution(ctx, db, 101, "870001", "manual historical review")
	if err != nil || !changed {
		t.Fatalf("ack changed=%v err=%v", changed, err)
	}
	state, err = ApplyProviderFinancialResolutions(ctx, db, 2, []ProviderFinancialResolutionInput{pending})
	if err != nil {
		t.Fatal(err)
	}
	if state.Acknowledged != 1 || state.Pending != 0 {
		t.Fatalf("replay state=%#v, acknowledgement was not preserved", state)
	}

	resolved := pending
	resolved.InstrumentID = 42
	resolved.IdentifierValue = "bj870001"
	resolved.Reason = ""
	state, err = ApplyProviderFinancialResolutions(ctx, db, 3, []ProviderFinancialResolutionInput{resolved})
	if err != nil {
		t.Fatal(err)
	}
	if state.Resolved != 1 || state.Acknowledged != 0 {
		t.Fatalf("resolved state=%#v, resolved evidence must supersede acknowledgement", state)
	}
	rows, err := ListProviderFinancialResolutions(ctx, db, ProviderResolutionResolved, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || rows[0].InstrumentID == nil || *rows[0].InstrumentID != 42 || rows[0].IdentifierValue != "bj870001" {
		t.Fatalf("resolved rows=%#v", rows)
	}
}
