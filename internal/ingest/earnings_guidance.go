package ingest

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"sort"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/cninfo"
	"github.com/yinhm/alphalake/internal/source/eastmoney"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

type EastmoneyGuidanceSource interface {
	GuidancePage(context.Context, string, int, int) ([]byte, string, error)
}

type GuidanceOptions struct {
	Period       string
	Start        string
	End          string
	Scope        []string
	Offline      bool
	MetadataOnly bool
	Now          func() time.Time
}

type GuidanceSourceSummary struct {
	Start            string `json:"window_start"`
	End              string `json:"window_end"`
	Source           string `json:"source"`
	RunID            int64  `json:"run_id"`
	Status           string `json:"status"`
	Mode             string `json:"mode"`
	Pages            int    `json:"pages"`
	WindowSplits     int    `json:"window_splits"`
	Rows             int    `json:"source_rows"`
	Observations     int    `json:"observations"`
	Inserted         int    `json:"inserted"`
	InScope          int    `json:"in_scope"`
	PendingIdentity  int    `json:"pending_identity"`
	OutOfScope       int    `json:"out_of_scope"`
	Issues           int    `json:"issues"`
	Documents        int    `json:"documents"`
	DocumentFailures int    `json:"document_failures"`
	Error            string `json:"error,omitempty"`
}

type GuidanceSummary struct {
	Contract  string                  `json:"contract_version"`
	Period    string                  `json:"period"`
	Scope     []string                `json:"scope"`
	Sources   []GuidanceSourceSummary `json:"sources"`
	Relations int                     `json:"relations_inserted"`
}

func SyncEarningsGuidance(ctx context.Context, db *sql.DB, root string, em EastmoneyGuidanceSource, ci CNINFOFilingSource, options GuidanceOptions) (GuidanceSummary, error) {
	out := GuidanceSummary{Contract: "earnings-guidance-ingest-v1", Period: options.Period, Scope: append([]string{}, options.Scope...), Sources: []GuidanceSourceSummary{}}
	period, err := time.Parse("2006-01-02", options.Period)
	if err != nil {
		return out, err
	}
	start, e1 := time.Parse("2006-01-02", options.Start)
	end, e2 := time.Parse("2006-01-02", options.End)
	if db == nil || root == "" || e1 != nil || e2 != nil || start.After(end) || len(options.Scope) == 0 {
		return out, fmt.Errorf("database, workspace, guidance period/window and explicit nonfinancial scope required")
	}
	if period.Month() != 3 && period.Month() != 6 && period.Month() != 9 && period.Month() != 12 {
		return out, fmt.Errorf("guidance target must be a fiscal quarter end")
	}
	if period.Day() != time.Date(period.Year(), period.Month()+1, 0, 0, 0, 0, 0, time.UTC).Day() {
		return out, fmt.Errorf("guidance target must be a fiscal quarter end")
	}
	identityAt := time.Now().UTC()
	if options.Now != nil {
		identityAt = options.Now().UTC()
	}
	if err = duckstore.ValidateGuidanceScope(ctx, db, options.Scope, identityAt); err != nil {
		return out, err
	}
	options.Scope = append([]string{}, options.Scope...)
	sort.Strings(options.Scope)
	out.Scope = append([]string{}, options.Scope...)
	var joined error
	requests := []struct {
		source  string
		options GuidanceOptions
	}{{eastmoney.Source, options}}
	for _, window := range filingWindows(start, end, cninfoDefaultWindowDays) {
		windowOptions := options
		windowOptions.Start = window.start.Format("2006-01-02")
		windowOptions.End = window.end.Format("2006-01-02")
		requests = append(requests, struct {
			source  string
			options GuidanceOptions
		}{cninfo.Source, windowOptions})
	}
	for _, request := range requests {
		summary, err := syncGuidanceSource(ctx, db, root, request.source, em, ci, request.options)
		out.Sources = append(out.Sources, summary)
		joined = errors.Join(joined, err)
	}
	if ctx.Err() == nil {
		n, err := linkGuidanceDisclosures(ctx, db, options.Period, out.Sources)
		out.Relations = n
		joined = errors.Join(joined, err)
	}
	return out, joined
}

func syncGuidanceSource(ctx context.Context, db *sql.DB, root, source string, em EastmoneyGuidanceSource, ci CNINFOFilingSource, options GuidanceOptions) (summary GuidanceSourceSummary, retErr error) {
	summary.Source, summary.Status, summary.Mode = source, duckstore.IngestRunFailed, "online"
	summary.Start, summary.End = options.Start, options.End
	key := duckstore.GuidanceCheckpointKey(options.Period, options.Start, options.End, options.Scope)
	previous, found, err := duckstore.GetCheckpoint(ctx, db, source, duckstore.GuidanceDataset, key)
	if err != nil {
		return summary, err
	}
	var prior duckstore.GuidanceReceipt
	if found {
		if err = json.Unmarshal([]byte(previous), &prior); err != nil {
			return summary, err
		}
	}
	var before *string
	if found {
		before = &previous
	}
	runID, err := duckstore.StartIngestRun(ctx, db, source, duckstore.GuidanceDataset, before)
	if err != nil {
		return summary, err
	}
	summary.RunID = runID
	defer func() {
		if retErr != nil {
			summary.Error = retErr.Error()
		}
		finalizeTrackedRun(ctx, db, runID, summary.Status, &retErr)
	}()
	now := time.Now().UTC()
	if options.Now != nil {
		now = options.Now().UTC()
	}
	if now.IsZero() {
		return summary, fmt.Errorf("zero guidance observation time")
	}
	receipt := duckstore.GuidanceReceipt{Contract: "earnings-guidance-source-v1", Source: source, Period: options.Period, Start: options.Start, End: options.End, Scope: options.Scope, ScopeHash: strings.Split(key, "|")[4], RunID: runID, ObservedAt: now, Mode: "online", ArtifactIDs: []int64{}, ObservationIDs: []string{}}
	if options.Offline {
		if !found || prior.Source != source || prior.Period != options.Period || prior.ScopeHash != receipt.ScopeHash || len(prior.ArtifactIDs) == 0 {
			return summary, fmt.Errorf("no complete archived %s guidance request set", source)
		}
		receipt.ObservedAt = prior.ObservedAt
		receipt.Mode = "archived_snapshot_not_latest"
		summary.Mode = receipt.Mode
	}
	var gs []domain.EarningsGuidance
	var diagnostics []duckstore.IngestDiagnostic
	if source == eastmoney.Source {
		gs, diagnostics, err = acquireEastmoneyGuidance(ctx, db, root, em, options, prior, &receipt, &summary)
	} else {
		gs, diagnostics, err = acquireCNINFOGuidance(ctx, db, root, ci, options, prior, &receipt, &summary)
	}
	if err != nil {
		return summary, err
	}
	if err = duckstore.ResolveGuidanceIdentities(ctx, db, gs, receipt.ObservedAt); err != nil {
		return summary, err
	}
	wanted := map[string]bool{}
	for _, ticker := range options.Scope {
		wanted[ticker] = true
	}
	fetchedByID := map[int64]time.Time{}
	archiveRows, err := db.QueryContext(ctx, `SELECT artifact_id,fetched_at FROM meta.artifact WHERE artifact_id IN (SELECT unnest(?::BIGINT[]))`, receipt.ArtifactIDs)
	if err != nil {
		return summary, err
	}
	for archiveRows.Next() {
		var id int64
		var fetched time.Time
		if err = archiveRows.Scan(&id, &fetched); err != nil {
			archiveRows.Close()
			return summary, err
		}
		fetchedByID[id] = fetched
	}
	err = archiveRows.Err()
	archiveRows.Close()
	if err != nil {
		return summary, err
	}
	selected := make([]domain.EarningsGuidance, 0, len(gs))
	for i := range gs {
		g := &gs[i]
		if !wanted[g.Ticker] {
			summary.OutOfScope++
		} else {
			receipt.ObservationIDs = append(receipt.ObservationIDs, g.ID)
			summary.InScope++
			if g.IdentityStatus != "resolved" {
				summary.PendingIdentity++
			}
		}
		g.FirstObservedAt = time.Time{}
		// Every referenced page must exist before the complete content is observable.
		for _, ref := range g.SourceRefs {
			fetched, exists := fetchedByID[ref.ArtifactID]
			if !exists {
				return summary, fmt.Errorf("missing guidance source page")
			}
			if fetched.After(g.FirstObservedAt) {
				g.FirstObservedAt = fetched
			}
		}

	}
	for _, g := range gs {
		if wanted[g.Ticker] {
			selected = append(selected, g)
		}
	}
	gs = selected
	summary.Observations = len(gs)
	summary.Issues = len(diagnostics)
	receipt.Issues = summary.Issues
	receipt.Rows = summary.Rows
	// 离线重解释可更新成员，原始取得时点沿 prior 保留；相同成员不推进检查点。
	sort.Strings(receipt.ObservationIDs)
	summary.Inserted, err = duckstore.PublishGuidance(ctx, db, key, receipt, gs, diagnostics,
		!options.Offline || !found || !slices.Equal(prior.ObservationIDs, receipt.ObservationIDs))
	if err != nil {
		return summary, err
	}
	summary.Status = duckstore.IngestRunCompleted
	if summary.DocumentFailures > 0 {
		summary.Status = duckstore.IngestRunPartial
		return summary, fmt.Errorf("%d CNINFO guidance document downloads failed; catalogue and structured data remain available", summary.DocumentFailures)
	}
	return summary, nil
}

func acquireEastmoneyGuidance(ctx context.Context, db *sql.DB, root string, source EastmoneyGuidanceSource, options GuidanceOptions, prior duckstore.GuidanceReceipt, receipt *duckstore.GuidanceReceipt, summary *GuidanceSourceSummary) ([]domain.EarningsGuidance, []duckstore.IngestDiagnostic, error) {
	rows := []eastmoney.Row{}
	seen := map[string]bool{}
	count := -1
	for pageNumber := 1; pageNumber <= 500; pageNumber++ {
		var raw []byte
		var stored artifact.Stored
		var err error
		if options.Offline {
			if pageNumber > len(prior.ArtifactIDs) {
				return nil, nil, fmt.Errorf("incomplete archived Eastmoney pages")
			}
			stored, raw, err = artifact.LoadByID(ctx, db, root, prior.ArtifactIDs[pageNumber-1])
		} else {
			if source == nil {
				return nil, nil, fmt.Errorf("Eastmoney source is nil")
			}
			var locator string
			raw, locator, err = source.GuidancePage(ctx, options.Period, pageNumber, 100)
			acquireErr := err
			if len(raw) > 0 {
				stored, err = artifact.Persist(ctx, db, root, artifact.Input{Source: eastmoney.Source, Dataset: duckstore.GuidanceDataset, SourceLocator: locator, FetchedAt: receipt.ObservedAt, MediaType: "application/json", ParserVersion: eastmoney.GuidanceVersion, IngestRunID: &receipt.RunID, Content: raw})
			}
			err = errors.Join(acquireErr, err)
		}
		if err != nil {
			return nil, nil, err
		}
		receipt.ArtifactIDs = append(receipt.ArtifactIDs, stored.ArtifactID)
		summary.Pages++
		page, err := eastmoney.ParsePage(raw)
		if err != nil {
			return nil, nil, err
		}
		if count < 0 {
			count = page.Count
		} else if count != page.Count {
			return nil, nil, fmt.Errorf("Eastmoney count changed during pagination")
		}
		for i, row := range page.Rows {
			encoded, _ := json.Marshal(row)
			sum := sha256.Sum256(encoded)
			signature := hex.EncodeToString(sum[:])
			if seen[signature] {
				return nil, nil, fmt.Errorf("Eastmoney pagination repeats/overlaps source rows")
			}
			seen[signature] = true
			row.Ref = domain.GuidanceRef{ArtifactID: stored.ArtifactID, Row: i + 1}
			rows = append(rows, row)
		}
		summary.Rows = len(rows)
		if len(rows) > count || (len(rows) < count && len(page.Rows) == 0) {
			return nil, nil, fmt.Errorf("Eastmoney incomplete pagination")
		}
		if len(rows) == count {
			if options.Offline && len(receipt.ArtifactIDs) != len(prior.ArtifactIDs) {
				return nil, nil, fmt.Errorf("archived Eastmoney page membership mismatch")
			}
			gs, issues, err := eastmoney.Normalize(rows, options.Period)
			ids := map[string]string{}
			for _, g := range gs {
				ids[g.EventKey] = g.ID
			}
			diagnostics := []duckstore.IngestDiagnostic{}
			seenIssues := map[string]bool{}
			for _, issue := range issues {
				key := issue.Subject + "|" + issue.Reason
				if seenIssues[key] {
					continue
				}
				seenIssues[key] = true
				diagnostics = append(diagnostics, duckstore.IngestDiagnostic{RuleCode: "guidance.semantic_unresolved", Severity: "warning", SubjectType: "guidance", SubjectKey: ids[issue.Subject], Details: issue.Reason})
			}
			return gs, diagnostics, err
		}
	}
	return nil, nil, fmt.Errorf("Eastmoney guidance pagination exceeded safety limit")
}

func acquireCNINFOGuidance(ctx context.Context, db *sql.DB, root string, source CNINFOFilingSource, options GuidanceOptions, prior duckstore.GuidanceReceipt, receipt *duckstore.GuidanceReceipt, summary *GuidanceSourceSummary) ([]domain.EarningsGuidance, []duckstore.IngestDiagnostic, error) {

	start, _ := time.Parse("2006-01-02", options.Start)
	end, _ := time.Parse("2006-01-02", options.End)
	windows := []duckstore.GuidanceCatalogueWindow{{Start: options.Start, End: options.End}}
	if options.Offline || len(prior.CatalogueWindows) > 0 {
		windows = prior.CatalogueWindows
		cursor := start
		flattened := []int64{}
		for _, w := range windows {
			a, e1 := time.Parse("2006-01-02", w.Start)
			b, e2 := time.Parse("2006-01-02", w.End)
			if e1 != nil || e2 != nil || !a.Equal(cursor) || a.After(b) || b.After(end) || len(w.ArtifactIDs) == 0 {
				return nil, nil, fmt.Errorf("invalid archived CNINFO window coverage")
			}
			cursor = b.AddDate(0, 0, 1)
			flattened = append(flattened, w.ArtifactIDs...)
		}
		a, _ := json.Marshal(flattened)
		b, _ := json.Marshal(prior.ArtifactIDs)
		if !cursor.Equal(end.AddDate(0, 0, 1)) || string(a) != string(b) {
			return nil, nil, fmt.Errorf("incomplete archived CNINFO window coverage")
		}
	}
	filings := []domain.FilingObservation{}
	positions := map[string]int{}
	diagnostics := []duckstore.IngestDiagnostic{}
	for i := 0; i < len(windows); i++ {
		w := windows[i]
		partOptions := options
		partOptions.Start = w.Start
		partOptions.End = w.End
		partReceipt := *receipt
		partReceipt.ArtifactIDs = nil
		partPrior := prior
		partPrior.ArtifactIDs = w.ArtifactIDs
		acceptedRows := summary.Rows
		fs, ps, ds, err := acquireCNINFOGuidancePages(ctx, db, root, source, partOptions, partPrior, &partReceipt, summary)
		if err != nil {
			summary.Rows = acceptedRows
			a, _ := time.Parse("2006-01-02", w.Start)
			b, _ := time.Parse("2006-01-02", w.End)
			if !options.Offline && errors.Is(err, errCNINFOIncompletePages) && a.Before(b) {
				middle := a.AddDate(0, 0, int(b.Sub(a).Hours()/24)/2)
				windows = append(windows, duckstore.GuidanceCatalogueWindow{Start: w.Start, End: middle.Format("2006-01-02")}, duckstore.GuidanceCatalogueWindow{Start: middle.AddDate(0, 0, 1).Format("2006-01-02"), End: w.End})
				summary.WindowSplits++
				diagnostics = append(diagnostics, duckstore.IngestDiagnostic{RuleCode: "guidance.catalogue_window_split", Severity: "warning", SubjectType: "date_window", SubjectKey: w.Start + "~" + w.End, Details: err.Error()})
				continue
			}
			return nil, nil, err
		}
		for key, row := range ps {
			if _, exists := positions[key]; exists {
				return nil, nil, fmt.Errorf("CNINFO duplicate announcement across disjoint windows")
			}
			positions[key] = row
		}
		filings = append(filings, fs...)
		diagnostics = append(diagnostics, ds...)
		w.ArtifactIDs = partReceipt.ArtifactIDs
		receipt.CatalogueWindows = append(receipt.CatalogueWindows, w)
	}
	sort.Slice(receipt.CatalogueWindows, func(i, j int) bool { return receipt.CatalogueWindows[i].Start < receipt.CatalogueWindows[j].Start })
	for _, w := range receipt.CatalogueWindows {
		receipt.ArtifactIDs = append(receipt.ArtifactIDs, w.ArtifactIDs...)
	}
	resolved, err := duckstore.ResolveFilingObservations(ctx, db, filings)
	if err != nil {
		return nil, nil, err
	}
	wanted := map[string]bool{}
	for _, ticker := range options.Scope {
		wanted[ticker] = true
	}
	gs := []domain.EarningsGuidance{}
	for i := range resolved {
		filing := &resolved[i]
		kind, start, period, basis := cninfo.GuidanceTitle(filing.Title)
		ticker := ""
		switch filing.ExchangeMIC {
		case "XSHG":
			ticker = "SHSE:" + filing.ProviderCode
		case "XSHE":
			ticker = "SZSE:" + filing.ProviderCode
		}
		if filing.ExchangeMIC == "" && filing.InstrumentID > 0 {
			if err = db.QueryRowContext(ctx, `SELECT exchange_mic FROM core.instrument WHERE instrument_id=?`, filing.InstrumentID).Scan(&filing.ExchangeMIC); err != nil {
				return nil, nil, err
			}
			if filing.ExchangeMIC == "XSHG" {
				ticker = "SHSE:" + filing.ProviderCode
			}
			if filing.ExchangeMIC == "XSHE" {
				ticker = "SZSE:" + filing.ProviderCode
			}
		}
		filing.IngestRunID = receipt.RunID
		if wanted[ticker] && !options.MetadataOnly {
			if options.Offline {
				err = db.QueryRowContext(ctx, `SELECT source_url FROM fundamental.filing WHERE source='cninfo' AND source_filing_id=?`, filing.SourceFilingID).Scan(&filing.SourceURL)
			} else {
				filing.SourceURL, err = source.FilingDocumentURL(filing.DocumentLocator)
			}
			docSummary := CNINFOFilingSummary{}
			if err == nil {
				docSource := source
				if options.Offline {
					docSource = nil
				}
				err = archiveCNINFODocument(ctx, db, docSource, root, receipt.RunID, receipt.ObservedAt, filing, &docSummary)
			}
			if err != nil {
				summary.DocumentFailures++
				diagnostics = append(diagnostics, duckstore.IngestDiagnostic{RuleCode: "guidance.document_failed", Severity: "warning", SubjectType: "filing", SubjectKey: filing.SourceFilingID, Details: err.Error()})
				err = nil
			} else {
				summary.Documents += docSummary.Documents + docSummary.ReusedDocs
			}
		}
		g := domain.EarningsGuidance{Source: cninfo.Source, EventKey: filing.SourceFilingID, NormalizationVersion: cninfo.GuidanceClassifierVersion, Ticker: ticker, ProviderCode: filing.ProviderCode, ExchangeMIC: filing.ExchangeMIC, Name: filing.SecurityName, Kind: kind, PeriodStart: start, PeriodEnd: period, PeriodBasis: basis, AnnouncementDate: filing.AnnouncementDate.Format("2006-01-02"), AnnouncementPrecision: "date", SourceFilingID: filing.SourceFilingID, Title: filing.Title, Contents: []string{filing.Title}, Reasons: []string{}, Metrics: map[string]domain.GuidanceMetric{}, SemanticStatus: "valid", Verification: "unlinked", SourceRefs: []domain.GuidanceRef{{ArtifactID: filing.CatalogueArtifactID, Row: positions[filing.SourceFilingID]}}}
		if basis == "unknown" {
			g.SemanticStatus = "partial"
			diagnostics = append(diagnostics, duckstore.IngestDiagnostic{RuleCode: "guidance.operating_period_unresolved", Severity: "warning", SubjectType: "filing", SubjectKey: filing.SourceFilingID, Details: "季度标题不能区分累计与单季；保留截止期，经营起始日未知"})
		}
		if err = g.Seal(); err != nil {
			return nil, nil, err
		}
		gs = append(gs, g)
	}
	if _, err = duckstore.UpsertFilings(ctx, db, receipt.RunID, resolved); err != nil {
		return nil, nil, err
	}
	for i := range gs {
		if err = db.QueryRowContext(ctx, `SELECT filing_id FROM fundamental.filing WHERE source='cninfo' AND source_filing_id=?`, gs[i].SourceFilingID).Scan(&gs[i].FilingID); err != nil {
			return nil, nil, err
		}
	}
	return gs, diagnostics, nil
}

func linkGuidanceDisclosures(ctx context.Context, db *sql.DB, period string, sources []GuidanceSourceSummary) (int, error) {
	var runID int64
	for _, source := range sources {
		if source.Source == cninfo.Source && (source.Status == duckstore.IngestRunCompleted || source.Status == duckstore.IngestRunPartial) {
			runID = source.RunID
		}
	}
	if runID == 0 {
		return 0, nil
	}
	rows, err := db.QueryContext(ctx, `WITH candidates AS (
 SELECT e.observation_id AS from_id,c.observation_id AS to_id,
 count(*) OVER(PARTITION BY e.observation_id) AS ec,count(*) OVER(PARTITION BY c.observation_id) AS cc
 FROM fundamental.current_earnings_guidance e JOIN fundamental.current_earnings_guidance c
 ON e.instrument_id=c.instrument_id AND e.period_start=c.period_start AND e.period_end=c.period_end
 AND e.period_basis=c.period_basis AND e.announcement_date=c.announcement_date
 WHERE e.source='eastmoney' AND c.source='cninfo' AND c.guidance_kind='forecast'
 AND e.identity_status='resolved' AND c.identity_status='resolved' AND e.period_end=CAST(? AS DATE)
 ) SELECT from_id,to_id FROM candidates WHERE ec=1 AND cc=1`, period)
	if err != nil {
		return 0, err
	}
	relations := []domain.GuidanceRelation{}
	for rows.Next() {
		var from, to string
		if err = rows.Scan(&from, &to); err != nil {
			rows.Close()
			return 0, err
		}
		relations = append(relations, domain.GuidanceRelation{FromID: from, ToID: to, Kind: "same_disclosure", Evidence: "unique matching issuer, period, period basis and source announcement date; identity only, amounts not verified", Rule: "guidance-link-v1"})
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return 0, err
	}
	return duckstore.PublishGuidanceRelations(ctx, db, runID, relations)
}

func acquireCNINFOGuidancePages(ctx context.Context, db *sql.DB, root string, source CNINFOFilingSource, options GuidanceOptions, prior duckstore.GuidanceReceipt, receipt *duckstore.GuidanceReceipt, summary *GuidanceSourceSummary) ([]domain.FilingObservation, map[string]int, []duckstore.IngestDiagnostic, error) {
	start, _ := time.Parse("2006-01-02", options.Start)
	end, _ := time.Parse("2006-01-02", options.End)
	filings := []domain.FilingObservation{}
	positions := map[string]int{}
	seen := map[string]bool{}
	count := -1
	acquired := 0
	diagnostics := []duckstore.IngestDiagnostic{}
	complete := false
	for number := 1; number <= 500; number++ {
		var raw []byte
		var page cninfo.CataloguePage
		var stored artifact.Stored
		var err error
		if options.Offline {
			if number > len(prior.ArtifactIDs) {
				return nil, nil, nil, fmt.Errorf("incomplete archived CNINFO pages")
			}
			stored, raw, err = artifact.LoadByID(ctx, db, root, prior.ArtifactIDs[number-1])
			if err == nil {
				page, err = cninfo.ParseCataloguePage(raw)
			}
		} else {
			if source == nil {
				return nil, nil, nil, fmt.Errorf("CNINFO source is nil")
			}
			page, raw, err = source.CataloguePage(ctx, cninfo.CatalogueRequest{Kind: cninfo.CatalogueGuidance, Page: number, PageSize: cninfoDefaultPageSize, StartDate: start, EndDate: end})
			acquireErr := err
			if len(raw) > 0 {
				stored, err = artifact.Persist(ctx, db, root, artifact.Input{Source: cninfo.Source, Dataset: "earnings_guidance_catalogue", SourceLocator: fmt.Sprintf("earnings-guidance/%s/%s~%s/page-%d.json", options.Period, options.Start, options.End, number), FetchedAt: receipt.ObservedAt, MediaType: "application/json", ParserVersion: cninfo.CatalogueParserVersion, IngestRunID: &receipt.RunID, Content: raw})
			}
			err = errors.Join(acquireErr, err)
		}
		if err != nil {
			return nil, nil, nil, err
		}
		var fields map[string]json.RawMessage
		if err = json.Unmarshal(raw, &fields); err != nil || fields["announcements"] == nil || (fields["totalAnnouncement"] == nil && fields["totalRecordNum"] == nil) {
			return nil, nil, nil, fmt.Errorf("CNINFO missing guidance catalogue schema")
		}
		receipt.ArtifactIDs = append(receipt.ArtifactIDs, stored.ArtifactID)
		summary.Pages++
		if count < 0 {
			count = page.TotalRecords
		} else if count != page.TotalRecords {
			return nil, nil, nil, fmt.Errorf("%w: CNINFO count changed during pagination", errCNINFOIncompletePages)
		}
		acquired += len(page.Filings) + len(page.Issues)
		summary.Rows += len(page.Filings) + len(page.Issues)
		for _, issue := range page.Issues {
			diagnostics = append(diagnostics, duckstore.IngestDiagnostic{RuleCode: "guidance.catalogue_invalid", Severity: "warning", SubjectType: "filing", SubjectKey: issue.SourceFilingID, Details: issue.Reason})
		}
		for _, filing := range page.Filings {
			if filing.AnnouncementDate.Before(start) || filing.AnnouncementDate.After(end) {
				return nil, nil, nil, fmt.Errorf("CNINFO announcement outside requested date window: %s", filing.SourceFilingID)
			}
			if seen[filing.SourceFilingID] {
				return nil, nil, nil, fmt.Errorf("%w: CNINFO guidance repeated/overlapping page", errCNINFOIncompletePages)
			}
			seen[filing.SourceFilingID] = true
			kind, _, period, _ := cninfo.GuidanceTitle(filing.Title)
			if kind == "other" {
				continue
			}
			if period == "" {
				diagnostics = append(diagnostics, duckstore.IngestDiagnostic{RuleCode: "guidance.period_unresolved", Severity: "warning", SubjectType: "filing", SubjectKey: filing.SourceFilingID, Details: filing.Title})
				continue
			}
			if period != options.Period {
				continue
			}
			filing.CatalogueArtifactID = stored.ArtifactID
			positions[filing.SourceFilingID] = page.RowPositions[filing.SourceFilingID]
			filings = append(filings, filing)
		}
		if acquired > count || (acquired < count && len(page.Filings)+len(page.Issues) == 0) {
			return nil, nil, nil, fmt.Errorf("%w: CNINFO guidance incomplete pagination", errCNINFOIncompletePages)
		}
		if acquired == count {
			complete = true
			break
		}
	}
	if !complete {
		return nil, nil, nil, fmt.Errorf("CNINFO guidance pagination safety limit")
	}
	if options.Offline && len(receipt.ArtifactIDs) != len(prior.ArtifactIDs) {
		return nil, nil, nil, fmt.Errorf("archived CNINFO page membership mismatch")
	}
	return filings, positions, diagnostics, nil
}
