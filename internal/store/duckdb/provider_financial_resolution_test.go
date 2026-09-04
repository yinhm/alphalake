package duckdb

import (
	"context"
	"fmt"
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

	// An unresolved replay may discover a different machine explanation, but an
	// acknowledged row must preserve the exact machine reason that the operator
	// reviewed rather than mutating the meaning of the acknowledgement later.
	replayed := pending
	replayed.Reason = "different later machine explanation"
	state, err = ApplyProviderFinancialResolutions(ctx, db, 2, []ProviderFinancialResolutionInput{replayed})
	if err != nil {
		t.Fatal(err)
	}
	if state.Acknowledged != 1 || state.Pending != 0 {
		t.Fatalf("replay state=%#v, acknowledgement was not preserved", state)
	}
	ackRows, err := ListProviderFinancialResolutions(ctx, db, ProviderResolutionAcknowledged, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(ackRows) != 1 || ackRows[0].Reason != pending.Reason || ackRows[0].AcknowledgedReason != "manual historical review" {
		t.Fatalf("ack row=%#v, reviewed reason changed during replay", ackRows)
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
	if len(rows) != 1 || rows[0].InstrumentID == nil || *rows[0].InstrumentID != 42 || rows[0].IdentifierValue != "bj870001" || rows[0].Reason != "" || rows[0].AcknowledgedReason != "" || rows[0].AcknowledgedAt != nil {
		t.Fatalf("resolved rows=%#v", rows)
	}
}

func TestProviderFinancialResolutionCanBeUnacknowledged(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "unack.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	input := ProviderFinancialResolutionInput{
		ArtifactID: 201, Source: "tdx", SourceFile: "gpcw19991231.zip",
		ReportPeriod: time.Date(1999, 12, 31, 0, 0, 0, 0, time.UTC),
		ProviderCode: "430001", Reason: "no unique temporal provider identity",
	}
	if _, err := ApplyProviderFinancialResolutions(ctx, db, 1, []ProviderFinancialResolutionInput{input}); err != nil {
		t.Fatal(err)
	}
	if changed, err := AcknowledgeProviderFinancialResolution(ctx, db, 201, "430001", "manual review"); err != nil || !changed {
		t.Fatalf("ack changed=%v err=%v", changed, err)
	}
	if err := SetCheckpoint(ctx, db, "tdx", "professional_financial", "package:gpcw19991231.zip", "md5"); err != nil {
		t.Fatal(err)
	}
	changed, err := UnacknowledgeProviderFinancialResolution(ctx, db, 201, "430001")
	if err != nil || !changed {
		t.Fatalf("unack changed=%v err=%v", changed, err)
	}
	rows, err := ListProviderFinancialResolutions(ctx, db, ProviderResolutionPending, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || rows[0].Status != ProviderResolutionPending || rows[0].AcknowledgedReason != "" || rows[0].AcknowledgedAt != nil || rows[0].Reason != input.Reason {
		t.Fatalf("pending row after unack=%#v", rows)
	}
	if _, found, err := GetCheckpoint(ctx, db, "tdx", "professional_financial", "package:gpcw19991231.zip"); err != nil || found {
		t.Fatalf("checkpoint after unack found=%v err=%v, want invalidated", found, err)
	}
	if changed, err := UnacknowledgeProviderFinancialResolution(ctx, db, 201, "430001"); err != nil || changed {
		t.Fatalf("idempotent unack changed=%v err=%v", changed, err)
	}
}

func TestListProviderFinancialResolutionsPage(t *testing.T) {
	ctx := context.Background()
	db, err := OpenAndMigrate(ctx, filepath.Join(t.TempDir(), "resolution-page.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	period := time.Date(2000, 12, 31, 0, 0, 0, 0, time.UTC)
	inputs := make([]ProviderFinancialResolutionInput, 0, 5)
	for i := 1; i <= 5; i++ {
		inputs = append(inputs, ProviderFinancialResolutionInput{
			ArtifactID: 301, Source: "tdx", SourceFile: "gpcw20001231.zip",
			ReportPeriod: period, ProviderCode: fmt.Sprintf("%06d", i), Reason: "pending",
		})
	}
	if _, err := ApplyProviderFinancialResolutions(ctx, db, 1, inputs); err != nil {
		t.Fatal(err)
	}
	first, err := ListProviderFinancialResolutionsPage(ctx, db, ProviderResolutionPending, 2, 0)
	if err != nil {
		t.Fatal(err)
	}
	second, err := ListProviderFinancialResolutionsPage(ctx, db, ProviderResolutionPending, 2, 2)
	if err != nil {
		t.Fatal(err)
	}
	third, err := ListProviderFinancialResolutionsPage(ctx, db, ProviderResolutionPending, 2, 4)
	if err != nil {
		t.Fatal(err)
	}
	if len(first) != 2 || len(second) != 2 || len(third) != 1 {
		t.Fatalf("page sizes=%d/%d/%d, want 2/2/1", len(first), len(second), len(third))
	}
	if first[0].ProviderCode != "000001" || second[0].ProviderCode != "000003" || third[0].ProviderCode != "000005" {
		t.Fatalf("unexpected pagination order: first=%#v second=%#v third=%#v", first, second, third)
	}
}
