package ingest

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/cninfo"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

const (
	cninfoFilingDataset         = "filing"
	cninfoCatalogueArtifactData = "filing_catalogue"
	cninfoDocumentArtifactData  = "filing_document"
	cninfoDefaultPageSize       = 50
	cninfoDefaultWindowDays     = 90
	cninfoRecentRescanDays      = 180
)

type CNINFOFilingSource interface {
	CataloguePage(context.Context, cninfo.CatalogueRequest) (cninfo.CataloguePage, []byte, error)
	FilingDocumentURL(string) (string, error)
	FilingDocument(context.Context, string) ([]byte, string, string, error)
}

type CNINFOFilingOptions struct {
	StartDate    time.Time
	EndDate      time.Time
	PageSize     int
	WindowDays   int
	MetadataOnly bool
	Rescan       bool
	Now          func() time.Time
	OnProgress   func(CNINFOFilingProgress)
}

type CNINFOFilingProgress struct {
	RunID      int64
	Windows    int
	Window     string
	Page       int
	Pages      int
	Filings    int
	Inserted   int
	Updated    int
	Resolved   int
	Pending    int
	Documents  int
	ReusedDocs int
	Issues     int
	Failures   int
}

type CNINFOFilingFailure struct {
	Window         string
	Page           int
	SourceFilingID string
	Err            error
}

type CNINFOFilingSummary struct {
	RunID          int64
	Windows        int
	SkippedWindows int
	Pages          int
	Filings        int
	Inserted       int
	Updated        int
	Resolved       int
	Pending        int
	Documents      int
	ReusedDocs     int
	Issues         int
	Failures       []CNINFOFilingFailure
}

type CNINFOFilingBatchError struct {
	Failures []CNINFOFilingFailure
}

func (e *CNINFOFilingBatchError) Error() string {
	if e == nil || len(e.Failures) == 0 {
		return ""
	}
	first := e.Failures[0]
	return fmt.Sprintf("%d CNINFO filing operations failed; first window=%s page=%d filing=%s: %v", len(e.Failures), first.Window, first.Page, first.SourceFilingID, first.Err)
}

func SyncCNINFOFilings(ctx context.Context, db *sql.DB, source CNINFOFilingSource, artifactRoot string) (CNINFOFilingSummary, error) {
	return SyncCNINFOFilingsWithOptions(ctx, db, source, artifactRoot, CNINFOFilingOptions{})
}

func SyncCNINFOFilingsWithOptions(ctx context.Context, db *sql.DB, source CNINFOFilingSource, artifactRoot string, options CNINFOFilingOptions) (summary CNINFOFilingSummary, retErr error) {
	if db == nil {
		return summary, errors.New("duckdb is nil")
	}
	if source == nil {
		return summary, errors.New("CNINFO filing source is nil")
	}
	artifactRoot = strings.TrimSpace(artifactRoot)
	if artifactRoot == "" {
		return summary, errors.New("artifact root is required")
	}
	now := time.Now().UTC()
	if options.Now != nil {
		now = options.Now().UTC()
	}
	if now.IsZero() {
		return summary, errors.New("filing observation time is zero")
	}
	start, end, pageSize, windowDays, err := normalizeCNINFOFilingOptions(options, now)
	if err != nil {
		return summary, err
	}

	runID, err := duckstore.StartIngestRun(ctx, db, cninfo.Source, cninfoFilingDataset, nil)
	if err != nil {
		return summary, fmt.Errorf("start CNINFO filing ingest run: %w", err)
	}
	summary.RunID = runID
	defer func() {
		finalizeTrackedRun(ctx, db, runID, cninfoFilingRunStatus(summary, retErr), &retErr)
	}()

	for _, window := range filingWindows(start, end, windowDays) {
		if err := ctx.Err(); err != nil {
			return summary, err
		}
		summary.Windows++
		windowName := filingWindowName(window.start, window.end)
		// Legacy checkpoints did not distinguish metadata-only acquisition.
		// Use mode-specific keys so they cannot suppress required documents.
		checkpointKey := fmt.Sprintf("catalogue-window:v2:metadata-only=%t:%s", options.MetadataOnly, windowName)
		if !options.Rescan && window.end.Before(dateUTCIngest(now.AddDate(0, 0, -cninfoRecentRescanDays))) {
			if _, found, err := duckstore.GetCheckpoint(ctx, db, cninfo.Source, cninfoFilingDataset, checkpointKey); err != nil {
				summary.Failures = append(summary.Failures, CNINFOFilingFailure{Window: windowName, Err: err})
				reportCNINFOFilingProgress(options, summary, windowName, 0)
				continue
			} else if found {
				summary.SkippedWindows++
				reportCNINFOFilingProgress(options, summary, windowName, 0)
				continue
			}
		}

		// Any failure recorded while processing this window, including failure to
		// persist catalogue diagnostics, blocks its completion checkpoint. This
		// keeps checkpoint state an honest assertion of a fully processed window.
		windowFailureStart := len(summary.Failures)
		windowFilings, pageSHAs, windowFailures, windowIssues := acquireCNINFOFilingWindow(
			ctx, db, source, artifactRoot, runID, now, window.start, window.end, pageSize, &summary, options,
		)
		summary.Failures = append(summary.Failures, windowFailures...)
		summary.Issues += len(windowIssues)
		if err := persistCNINFOCatalogueIssues(ctx, db, runID, windowName, windowIssues); err != nil {
			summary.Failures = append(summary.Failures, CNINFOFilingFailure{Window: windowName, Err: err})
		}

		sort.SliceStable(windowFilings, func(i, j int) bool {
			if windowFilings[i].AnnouncementTime.Equal(windowFilings[j].AnnouncementTime) {
				return windowFilings[i].SourceFilingID < windowFilings[j].SourceFilingID
			}
			return windowFilings[i].AnnouncementTime.Before(windowFilings[j].AnnouncementTime)
		})
		for i := range windowFilings {
			windowFilings[i].IngestRunID = runID
			if err := attachCNINFOFilingDocument(ctx, db, source, artifactRoot, runID, now, options.MetadataOnly, &windowFilings[i], &summary); err != nil {
				summary.Failures = append(summary.Failures, CNINFOFilingFailure{
					Window: windowName, SourceFilingID: windowFilings[i].SourceFilingID, Err: err,
				})
			}
		}

		resolved, err := duckstore.ResolveFilingObservations(ctx, db, windowFilings)
		if err != nil {
			summary.Failures = append(summary.Failures, CNINFOFilingFailure{Window: windowName, Err: err})
			reportCNINFOFilingProgress(options, summary, windowName, 0)
			continue
		}
		writeResult, err := duckstore.UpsertFilings(ctx, db, runID, resolved)
		if err != nil {
			summary.Failures = append(summary.Failures, CNINFOFilingFailure{Window: windowName, Err: err})
			reportCNINFOFilingProgress(options, summary, windowName, 0)
			continue
		}
		summary.Filings += writeResult.Attempted
		summary.Inserted += writeResult.Inserted
		summary.Updated += writeResult.Updated
		summary.Resolved += writeResult.Resolved
		summary.Pending += writeResult.Pending

		// Unresolved identity remains locally replayable and does not force source
		// re-fetch. Acquisition, artifact, diagnostic, resolution or write failures
		// do block the window checkpoint.
		if len(summary.Failures) == windowFailureStart {
			if err := duckstore.SetCheckpoint(ctx, db, cninfo.Source, cninfoFilingDataset, checkpointKey, catalogueWindowSignature(pageSHAs)); err != nil {
				summary.Failures = append(summary.Failures, CNINFOFilingFailure{Window: windowName, Err: err})
			}
		}
		reportCNINFOFilingProgress(options, summary, windowName, 0)
	}

	if len(summary.Failures) != 0 {
		return summary, &CNINFOFilingBatchError{Failures: summary.Failures}
	}
	return summary, nil
}

type filingWindow struct {
	start time.Time
	end   time.Time
}

func normalizeCNINFOFilingOptions(options CNINFOFilingOptions, now time.Time) (time.Time, time.Time, int, int, error) {
	end := options.EndDate
	if end.IsZero() {
		end = now
	}
	start := options.StartDate
	if start.IsZero() {
		start = end.AddDate(0, 0, -89)
	}
	start = dateUTCIngest(start)
	end = dateUTCIngest(end)
	if end.Before(start) {
		return time.Time{}, time.Time{}, 0, 0, errors.New("CNINFO filing end date precedes start date")
	}
	pageSize := options.PageSize
	if pageSize == 0 {
		pageSize = cninfoDefaultPageSize
	}
	if pageSize < 1 || pageSize > 100 {
		return time.Time{}, time.Time{}, 0, 0, errors.New("CNINFO filing page size must be in [1,100]")
	}
	windowDays := options.WindowDays
	if windowDays == 0 {
		windowDays = cninfoDefaultWindowDays
	}
	if windowDays < 1 || windowDays > 366 {
		return time.Time{}, time.Time{}, 0, 0, errors.New("CNINFO filing window days must be in [1,366]")
	}
	return start, end, pageSize, windowDays, nil
}

func filingWindows(start, end time.Time, days int) []filingWindow {
	var out []filingWindow
	for cursor := start; !cursor.After(end); {
		windowEnd := cursor.AddDate(0, 0, days-1)
		if windowEnd.After(end) {
			windowEnd = end
		}
		out = append(out, filingWindow{start: cursor, end: windowEnd})
		cursor = windowEnd.AddDate(0, 0, 1)
	}
	return out
}

func acquireCNINFOFilingWindow(
	ctx context.Context,
	db *sql.DB,
	source CNINFOFilingSource,
	artifactRoot string,
	runID int64,
	now, start, end time.Time,
	pageSize int,
	summary *CNINFOFilingSummary,
	options CNINFOFilingOptions,
) ([]domain.FilingObservation, []string, []CNINFOFilingFailure, []cninfo.CatalogueIssue) {
	windowName := filingWindowName(start, end)
	var filings []domain.FilingObservation
	var pageSHAs []string
	var failures []CNINFOFilingFailure
	var issues []cninfo.CatalogueIssue
	for pageNumber := 1; pageNumber <= 10000; pageNumber++ {
		page, raw, err := source.CataloguePage(ctx, cninfo.CatalogueRequest{
			Page: pageNumber, PageSize: pageSize, StartDate: start, EndDate: end,
		})
		if err != nil {
			failures = append(failures, CNINFOFilingFailure{Window: windowName, Page: pageNumber, Err: err})
			break
		}
		locator := fmt.Sprintf("periodic/%s/page-%05d-size-%d.json", windowName, pageNumber, pageSize)
		stored, err := artifact.Persist(ctx, db, artifactRoot, artifact.Input{
			Source: cninfo.Source, Dataset: cninfoCatalogueArtifactData,
			SourceLocator: locator, FetchedAt: now, MediaType: "application/json",
			ParserVersion: cninfo.CatalogueParserVersion, IngestRunID: &runID, Content: raw,
		})
		if err != nil {
			failures = append(failures, CNINFOFilingFailure{Window: windowName, Page: pageNumber, Err: err})
			break
		}
		pageSHAs = append(pageSHAs, stored.SHA256)
		for i := range page.Filings {
			page.Filings[i].CatalogueArtifactID = stored.ArtifactID
		}
		filings = append(filings, page.Filings...)
		issues = append(issues, page.Issues...)
		summary.Pages++
		reportCNINFOFilingProgress(options, *summary, windowName, pageNumber)

		observedRows := len(page.Filings) + len(page.Issues)
		if page.Page != pageNumber || (observedRows == 0 && (page.HasMore || page.TotalRecords > 0 || page.TotalPages > 1)) {
			failures = append(failures, CNINFOFilingFailure{Window: windowName, Page: pageNumber, Err: fmt.Errorf("inconsistent CNINFO pagination: requested page %d, received page %d with %d rows", pageNumber, page.Page, observedRows)})
			break
		}
		if page.TotalPages > 10000 {
			failures = append(failures, CNINFOFilingFailure{Window: windowName, Page: pageNumber, Err: fmt.Errorf("CNINFO total pages %d exceeds safety limit", page.TotalPages)})
			break
		}
		if page.TotalPages > 0 {
			if pageNumber >= page.TotalPages {
				break
			}
			continue
		}
		if !page.HasMore && observedRows < pageSize {
			break
		}
		if pageNumber == 10000 {
			failures = append(failures, CNINFOFilingFailure{Window: windowName, Page: pageNumber, Err: errors.New("CNINFO pagination exhausted safety limit before completion")})
		}
	}
	return filings, pageSHAs, failures, issues
}

func attachCNINFOFilingDocument(
	ctx context.Context,
	db *sql.DB,
	source CNINFOFilingSource,
	artifactRoot string,
	runID int64,
	now time.Time,
	metadataOnly bool,
	filing *domain.FilingObservation,
	summary *CNINFOFilingSummary,
) error {
	if strings.TrimSpace(filing.DocumentLocator) == "" {
		if filing.EligiblePITAnchor() && !metadataOnly {
			return errors.New("eligible filing has no document locator")
		}
		return nil
	}
	sourceURL, err := source.FilingDocumentURL(filing.DocumentLocator)
	if err != nil {
		return err
	}
	filing.SourceURL = sourceURL
	if metadataOnly || !filing.EligiblePITAnchor() {
		return nil
	}

	// Hash-valid bytes can still be a cached HTML anti-bot page from an earlier
	// response. Search retained revisions lazily and reuse only a payload whose
	// media semantics are compatible with the authoritative document URL.
	healthy, _, err := artifact.LoadHealthyVersions(ctx, db, artifactRoot, cninfo.Source, cninfoDocumentArtifactData, sourceURL, 0)
	if err != nil {
		return err
	}
	for _, version := range healthy {
		if err := validateCNINFOFilingDocument(sourceURL, "", version.Content); err != nil {
			continue
		}
		filing.DocumentArtifactID = version.Stored.ArtifactID
		filing.DocumentSHA256 = version.Stored.SHA256
		summary.ReusedDocs++
		return nil
	}

	content, downloadedURL, mediaType, err := source.FilingDocument(ctx, filing.DocumentLocator)
	if err != nil {
		return err
	}
	if downloadedURL != sourceURL {
		return fmt.Errorf("CNINFO document URL changed from %s to %s", sourceURL, downloadedURL)
	}
	if err := validateCNINFOFilingDocument(sourceURL, mediaType, content); err != nil {
		return err
	}
	if strings.TrimSpace(mediaType) == "" {
		mediaType = "application/octet-stream"
	}
	stored, err := artifact.Persist(ctx, db, artifactRoot, artifact.Input{
		Source: cninfo.Source, Dataset: cninfoDocumentArtifactData,
		SourceLocator: sourceURL, FetchedAt: now, MediaType: mediaType,
		ParserVersion: "raw-document-v1", IngestRunID: &runID, Content: content,
	})
	if err != nil {
		return err
	}
	filing.DocumentArtifactID = stored.ArtifactID
	filing.DocumentSHA256 = stored.SHA256
	summary.Documents++
	return nil
}

func validateCNINFOFilingDocument(sourceURL, mediaType string, content []byte) error {
	trimmed := bytes.TrimSpace(content)
	if len(trimmed) == 0 {
		return errors.New("CNINFO filing document is empty")
	}
	mediaType = strings.ToLower(strings.TrimSpace(mediaType))
	prefixLength := len(trimmed)
	if prefixLength > 512 {
		prefixLength = 512
	}
	lowerPrefix := strings.ToLower(string(trimmed[:prefixLength]))
	if strings.Contains(mediaType, "text/html") || strings.HasPrefix(lowerPrefix, "<!doctype html") || strings.HasPrefix(lowerPrefix, "<html") {
		return fmt.Errorf("CNINFO filing document returned HTML instead of authoritative bytes")
	}
	cleanURL := strings.ToLower(sourceURL)
	if i := strings.IndexByte(cleanURL, '?'); i >= 0 {
		cleanURL = cleanURL[:i]
	}
	pdfExpected := strings.Contains(mediaType, "application/pdf") || strings.HasSuffix(cleanURL, ".pdf")
	if pdfExpected && !bytes.HasPrefix(trimmed, []byte("%PDF-")) {
		return fmt.Errorf("CNINFO PDF document lacks %%PDF- header")
	}
	return nil
}

func persistCNINFOCatalogueIssues(ctx context.Context, db *sql.DB, runID int64, window string, issues []cninfo.CatalogueIssue) error {
	if len(issues) == 0 {
		return nil
	}
	diagnostics := make([]duckstore.IngestDiagnostic, 0, len(issues))
	for _, issue := range issues {
		subject := issue.SourceFilingID
		if subject == "" {
			subject = issue.ProviderCode
		}
		diagnostics = append(diagnostics, duckstore.IngestDiagnostic{
			RuleCode: "cninfo.catalogue_record_invalid", Severity: "error",
			SubjectType: "filing", SubjectKey: subject,
			Details: window + ": " + issue.Reason,
		})
	}
	return duckstore.RecordIngestDiagnostics(ctx, db, runID, cninfo.Source, cninfoFilingDataset, diagnostics)
}

func catalogueWindowSignature(pageSHAs []string) string {
	h := sha256.New()
	for _, sum := range pageSHAs {
		_, _ = h.Write([]byte(sum))
		_, _ = h.Write([]byte{'\n'})
	}
	return "catalogue-v1:" + hex.EncodeToString(h.Sum(nil))
}

func filingWindowName(start, end time.Time) string {
	return start.Format("2006-01-02") + "_" + end.Format("2006-01-02")
}

func reportCNINFOFilingProgress(options CNINFOFilingOptions, summary CNINFOFilingSummary, window string, page int) {
	if options.OnProgress == nil {
		return
	}
	options.OnProgress(CNINFOFilingProgress{
		RunID: summary.RunID, Windows: summary.Windows, Window: window,
		Page: page, Pages: summary.Pages, Filings: summary.Filings,
		Inserted: summary.Inserted, Updated: summary.Updated,
		Resolved: summary.Resolved, Pending: summary.Pending,
		Documents: summary.Documents, ReusedDocs: summary.ReusedDocs,
		Issues: summary.Issues, Failures: len(summary.Failures),
	})
}

func cninfoFilingRunStatus(summary CNINFOFilingSummary, runErr error) string {
	if errors.Is(runErr, context.Canceled) || errors.Is(runErr, context.DeadlineExceeded) {
		return duckstore.IngestRunCanceled
	}
	if runErr == nil && summary.Pending == 0 && summary.Issues == 0 {
		return duckstore.IngestRunCompleted
	}
	if summary.Pages > 0 || summary.Filings > 0 || summary.SkippedWindows > 0 {
		return duckstore.IngestRunPartial
	}
	return duckstore.IngestRunFailed
}

func dateUTCIngest(value time.Time) time.Time {
	y, m, d := value.Date()
	return time.Date(y, m, d, 0, 0, 0, 0, time.UTC)
}
