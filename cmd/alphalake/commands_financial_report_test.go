package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/ingest"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestFinancialReportKeepsPendingScopeAndFailure(t *testing.T) {
	ctx := t.Context()
	db, err := duckstore.OpenInitialized(ctx, filepath.Join(t.TempDir(), "source.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	from := time.Date(2000, 1, 1, 0, 0, 0, 0, time.UTC)
	id, err := duckstore.UpsertInstrument(ctx, db, domain.InstrumentRef{Type: domain.InstrumentEquity, ExchangeMIC: "XSHE", Currency: "CNY", Name: "scope test"}, domain.Identifier{Provider: "tdx", Type: "symbol", Value: "sz300750", ValidFrom: &from})
	if err != nil {
		t.Fatal(err)
	}

	_, err = duckstore.ApplyProviderFinancialResolutions(ctx, db, 7, []duckstore.ProviderFinancialResolutionInput{
		{ArtifactID: 1, Source: "tdx", SourceFile: "gpcw20251231.zip", ReportPeriod: time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC), ProviderCode: "300750", Reason: "conflicting duplicate source records"},
	})
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "report.json")
	limit, offline, report, err := parseFinancialLimit([]string{"--latest", "6", "--offline", "--report", path})
	if err != nil || limit != 6 || !offline || report != path {
		t.Fatal(limit, offline, report, err)
	}
	summary := ingest.TDXProfessionalFinancialSummary{RunID: 7, Selected: 6, Unresolved: 1, CacheFallbacks: 1}
	if err := writeFinancialSyncReport(ctx, db, path, summary, errors.New("download failed"), true); err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var out struct {
		Error    string `json:"error"`
		Status   string `json:"status"`
		Complete bool   `json:"pending_complete"`
		Pending  []struct {
			Code   string `json:"code"`
			Reason string `json:"reason"`
		} `json:"pending_all"`
		Selected []struct {
			InstrumentID int64 `json:"instrument_id"`
		} `json:"pending_selected"`
	}
	if err := json.Unmarshal(content, &out); err != nil {
		t.Fatal(err)
	}
	if len(out.Selected) != 1 || out.Selected[0].InstrumentID != id {
		t.Fatalf("missing temporal identity: %s", content)
	}
	if out.Error != "download failed" || out.Status != "partial_or_failed" || !out.Complete || len(out.Pending) != 1 || out.Pending[0].Code != "300750" || out.Pending[0].Reason == "" {
		t.Fatalf("report lost failure/scope: %s", content)
	}
	if err := writeFinancialSyncReport(ctx, db, path, summary, nil, true); err == nil {
		t.Fatal("overwrote source report")
	}
	again, _ := os.ReadFile(path)
	if string(again) != string(content) {
		t.Fatal("changed old evidence")
	}
}
