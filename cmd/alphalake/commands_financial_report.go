package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"os"
	"time"

	"github.com/yinhm/alphalake/internal/ingest"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

// 来源维护收据供自动发布判断范围；不把源同步partial改成completed。
func writeFinancialSyncReport(ctx context.Context, db *sql.DB, path string, summary ingest.TDXProfessionalFinancialSummary, syncErr error, offline bool) error {
	pending := []map[string]any{}
	selected := []duckstore.ProviderFinancialResolutionRow{}
	for offset := 0; ; offset += 1000 {
		rows, err := duckstore.ListProviderFinancialResolutionsPage(ctx, db, duckstore.ProviderResolutionPending, 1000, offset)
		if err != nil {
			return err
		}
		for _, row := range rows {
			if row.LastIngestRunID != nil && *row.LastIngestRunID == summary.RunID {
				selected = append(selected, row)
			}
			pending = append(pending, map[string]any{"code": row.ProviderCode, "report_period": row.ReportPeriod.Format("2006-01-02"), "artifact_id": row.ArtifactID, "source_file": row.SourceFile, "reason": row.Reason})
		}
		if len(rows) < 1000 {
			break
		}
	}
	// 以报告期身份解析选中包的待解析项；不能只凭六位代码不同判定范围外。
	selectedPending := []map[string]any{}
	periods := map[time.Time][]duckstore.ProviderFinancialResolutionRow{}
	for _, row := range selected {
		periods[row.ReportPeriod] = append(periods[row.ReportPeriod], row)
	}
	for period, rows := range periods {
		codes := make([]string, len(rows))
		for i, row := range rows {
			codes[i] = row.ProviderCode
		}
		resolved, err := duckstore.ResolveProviderCodesAt(ctx, db, "tdx", codes, period)
		if err != nil {
			return err
		}
		for i, row := range rows {
			var id any
			if resolved[i].Resolved() {
				id = resolved[i].InstrumentID
			}
			selectedPending = append(selectedPending, map[string]any{"code": row.ProviderCode, "report_period": period.Format("2006-01-02"), "artifact_id": row.ArtifactID, "instrument_id": id, "reason": row.Reason})
		}
	}
	reason := ""
	if syncErr != nil {
		reason = syncErr.Error()
	}
	status := "completed"
	if syncErr != nil || summary.Unresolved > 0 || summary.CacheFallbacks > 0 || len(summary.MasterFailures) > 0 {
		status = "partial_or_failed"
	}
	out := map[string]any{
		"contract": "alphalake-financial-sync-v1", "run_id": summary.RunID, "status": status,
		"offline": offline, "cache_fallbacks": summary.CacheFallbacks, "error": reason,
		"package_failures": len(summary.Failures), "master_failures": len(summary.MasterFailures),
		"selected_packages": summary.Selected, "unresolved_selected": summary.Unresolved,
		"pending_all": pending, "pending_complete": true, "pending_selected": selectedPending,
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	err = json.NewEncoder(file).Encode(out)
	return errors.Join(err, file.Sync(), file.Close())
}
