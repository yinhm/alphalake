package ingest

import (
	"context"
	"crypto/md5"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/domain"
	tdxfinancial "github.com/yinhm/alphalake/internal/source/tdx/financial"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

const (
	tdxProfessionalFinancialDataset = "professional_financial"
	gpcwParserVersion              = "gpcw-v1"
)

type TDXProfessionalFinancialSource interface {
	instrumentListSource
	ProfessionalFinancialFileList(context.Context) ([]tdxfinancial.FileEntry, []byte, error)
	ProfessionalFinancialPackage(context.Context, tdxfinancial.FileEntry) ([]byte, error)
	NormalizeProfessionalFinancialPackage(tdxfinancial.FileEntry, []byte, int64) ([]domain.ProviderFinancialRecord, error)
}

type TDXProfessionalFinancialOptions struct {
	MaxPackages int // zero means every listed package; positive processes newest N
	Now         func() time.Time
	OnProgress  func(TDXProfessionalFinancialProgress)
}

type TDXProfessionalFinancialProgress struct {
	RunID       int64
	Processed   int
	Total       int
	Package     string
	Packages    int
	Skipped     int
	Facts       int
	Unresolved  int
	Failures    int
}

type TDXProfessionalFinancialFailure struct {
	Package string
	Err     error
}

type TDXProfessionalFinancialSummary struct {
	RunID      int64
	Listed     int
	Selected   int
	Packages   int
	Skipped    int
	Facts      int
	Unresolved int
	Failures   []TDXProfessionalFinancialFailure
}

type TDXProfessionalFinancialBatchError struct {
	Failures []TDXProfessionalFinancialFailure
}

func (e *TDXProfessionalFinancialBatchError) Error() string {
	if e == nil || len(e.Failures) == 0 {
		return ""
	}
	return fmt.Sprintf("%d TDX professional-financial packages failed; first %s: %v", len(e.Failures), e.Failures[0].Package, e.Failures[0].Err)
}

func SyncTDXProfessionalFinancial(ctx context.Context, db *sql.DB, source TDXProfessionalFinancialSource, artifactRoot string) (TDXProfessionalFinancialSummary, error) {
	return SyncTDXProfessionalFinancialWithOptions(ctx, db, source, artifactRoot, TDXProfessionalFinancialOptions{})
}

func SyncTDXProfessionalFinancialWithOptions(
	ctx context.Context,
	db *sql.DB,
	source TDXProfessionalFinancialSource,
	artifactRoot string,
	options TDXProfessionalFinancialOptions,
) (summary TDXProfessionalFinancialSummary, retErr error) {
	if db == nil {
		return summary, errors.New("duckdb is nil")
	}
	if source == nil {
		return summary, errors.New("TDX professional-financial source is nil")
	}
	artifactRoot = strings.TrimSpace(artifactRoot)
	if artifactRoot == "" {
		return summary, errors.New("artifact root is required")
	}
	if options.MaxPackages < 0 {
		return summary, errors.New("max packages must be non-negative")
	}
	now := time.Now()
	if options.Now != nil {
		now = options.Now()
	}
	if now.IsZero() {
		return summary, errors.New("financial observation time is zero")
	}

	runID, err := duckstore.StartIngestRun(ctx, db, "tdx", tdxProfessionalFinancialDataset, nil)
	if err != nil {
		return summary, fmt.Errorf("start TDX professional-financial ingest run: %w", err)
	}
	summary.RunID = runID
	defer func() {
		finalizeTrackedRun(ctx, db, runID, professionalFinancialRunStatus(summary, retErr), &retErr)
	}()

	if _, _, err := refreshInstrumentMaster(ctx, db, source); err != nil {
		return summary, fmt.Errorf("refresh TDX instrument master: %w", err)
	}
	entries, manifestRaw, err := source.ProfessionalFinancialFileList(ctx)
	if err != nil {
		return summary, err
	}
	summary.Listed = len(entries)
	if _, err := artifact.Persist(ctx, db, artifactRoot, artifact.Input{
		Source: "tdx", Dataset: tdxProfessionalFinancialDataset,
		SourceLocator: "tdxfin/gpcw.txt", FetchedAt: now,
		MediaType: "text/plain", ParserVersion: "gpcw-list-v1",
		IngestRunID: &runID, Content: manifestRaw,
	}); err != nil {
		return summary, fmt.Errorf("persist gpcw file-list artifact: %w", err)
	}

	sort.Slice(entries, func(i, j int) bool { return entries[i].Filename > entries[j].Filename })
	if options.MaxPackages > 0 && len(entries) > options.MaxPackages {
		entries = entries[:options.MaxPackages]
	}
	summary.Selected = len(entries)

	for i, entry := range entries {
		if err := ctx.Err(); err != nil {
			return summary, err
		}
		checkpointKey := "package:" + entry.Filename
		checkpoint, done, err := duckstore.GetCheckpoint(ctx, db, "tdx", tdxProfessionalFinancialDataset, checkpointKey)
		if err != nil {
			summary.Failures = append(summary.Failures, TDXProfessionalFinancialFailure{Package: entry.Filename, Err: err})
			reportProfessionalFinancialProgress(options, summary, i+1, entry.Filename)
			continue
		}
		if done && strings.EqualFold(checkpoint, entry.MD5) {
			summary.Skipped++
			reportProfessionalFinancialProgress(options, summary, i+1, entry.Filename)
			continue
		}

		stored, raw, found, err := artifact.LoadLatest(ctx, db, artifactRoot, "tdx", tdxProfessionalFinancialDataset, "tdxfin/"+entry.Filename)
		if err != nil {
			summary.Failures = append(summary.Failures, TDXProfessionalFinancialFailure{Package: entry.Filename, Err: err})
			reportProfessionalFinancialProgress(options, summary, i+1, entry.Filename)
			continue
		}
		if !found || !md5Matches(raw, entry.MD5) {
			raw, err = source.ProfessionalFinancialPackage(ctx, entry)
			if err != nil {
				summary.Failures = append(summary.Failures, TDXProfessionalFinancialFailure{Package: entry.Filename, Err: err})
				reportProfessionalFinancialProgress(options, summary, i+1, entry.Filename)
				continue
			}
			stored, err = artifact.Persist(ctx, db, artifactRoot, artifact.Input{
				Source: "tdx", Dataset: tdxProfessionalFinancialDataset,
				SourceLocator: "tdxfin/" + entry.Filename, FetchedAt: now,
				MediaType: "application/zip", ParserVersion: gpcwParserVersion,
				IngestRunID: &runID, Content: raw,
			})
			if err != nil {
				summary.Failures = append(summary.Failures, TDXProfessionalFinancialFailure{Package: entry.Filename, Err: err})
				reportProfessionalFinancialProgress(options, summary, i+1, entry.Filename)
				continue
			}
		}

		records, err := source.NormalizeProfessionalFinancialPackage(entry, raw, stored.ArtifactID)
		if err != nil {
			summary.Failures = append(summary.Failures, TDXProfessionalFinancialFailure{Package: entry.Filename, Err: err})
			reportProfessionalFinancialProgress(options, summary, i+1, entry.Filename)
			continue
		}
		resolved, unresolved, err := resolveProviderFinancialRecords(ctx, db, records)
		if err != nil {
			summary.Failures = append(summary.Failures, TDXProfessionalFinancialFailure{Package: entry.Filename, Err: err})
			reportProfessionalFinancialProgress(options, summary, i+1, entry.Filename)
			continue
		}
		facts, err := duckstore.InsertProviderFinancialRecordsForArtifact(ctx, db, runID, stored.SHA256, resolved)
		if err != nil {
			summary.Failures = append(summary.Failures, TDXProfessionalFinancialFailure{Package: entry.Filename, Err: err})
			reportProfessionalFinancialProgress(options, summary, i+1, entry.Filename)
			continue
		}
		summary.Packages++
		summary.Facts += facts
		summary.Unresolved += unresolved
		if unresolved == 0 {
			if err := duckstore.SetCheckpoint(ctx, db, "tdx", tdxProfessionalFinancialDataset, checkpointKey, entry.MD5); err != nil {
				summary.Failures = append(summary.Failures, TDXProfessionalFinancialFailure{Package: entry.Filename, Err: err})
			}
		}
		reportProfessionalFinancialProgress(options, summary, i+1, entry.Filename)
	}

	if len(summary.Failures) != 0 {
		return summary, &TDXProfessionalFinancialBatchError{Failures: summary.Failures}
	}
	return summary, nil
}

func resolveProviderFinancialRecords(ctx context.Context, db *sql.DB, records []domain.ProviderFinancialRecord) ([]domain.ProviderFinancialRecord, int, error) {
	if len(records) == 0 {
		return nil, 0, nil
	}
	period := records[0].ReportPeriod
	identifiers := make([]domain.Identifier, len(records))
	for i, record := range records {
		if !record.ReportPeriod.Equal(period) {
			return nil, 0, fmt.Errorf("gpcw package mixes report periods %s and %s", period.Format("2006-01-02"), record.ReportPeriod.Format("2006-01-02"))
		}
		identifiers[i] = record.Identifier
	}
	ids, err := duckstore.ResolveInstrumentIdentifiersAt(ctx, db, identifiers, period)
	if err != nil {
		return nil, 0, err
	}
	resolved := make([]domain.ProviderFinancialRecord, 0, len(records))
	unresolved := 0
	for i, record := range records {
		if ids[i] == 0 {
			unresolved++
			continue
		}
		record.InstrumentID = ids[i]
		resolved = append(resolved, record)
	}
	return resolved, unresolved, nil
}

func md5Matches(content []byte, want string) bool {
	sum := md5.Sum(content)
	return strings.EqualFold(hex.EncodeToString(sum[:]), strings.TrimSpace(want))
}

func reportProfessionalFinancialProgress(options TDXProfessionalFinancialOptions, summary TDXProfessionalFinancialSummary, processed int, name string) {
	if options.OnProgress == nil {
		return
	}
	options.OnProgress(TDXProfessionalFinancialProgress{
		RunID: summary.RunID, Processed: processed, Total: summary.Selected,
		Package: name, Packages: summary.Packages, Skipped: summary.Skipped,
		Facts: summary.Facts, Unresolved: summary.Unresolved, Failures: len(summary.Failures),
	})
}

func professionalFinancialRunStatus(summary TDXProfessionalFinancialSummary, runErr error) string {
	if errors.Is(runErr, context.Canceled) || errors.Is(runErr, context.DeadlineExceeded) {
		return duckstore.IngestRunCanceled
	}
	if runErr == nil && summary.Unresolved == 0 {
		return duckstore.IngestRunCompleted
	}
	if summary.Packages > 0 || summary.Unresolved > 0 {
		return duckstore.IngestRunPartial
	}
	return duckstore.IngestRunFailed
}
