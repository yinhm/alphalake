package domain

import "time"

// FilingType identifies the periodic report whose financial period is described.
// Values are intentionally provider-neutral and limited to report forms that can
// anchor canonical point-in-time fundamentals.
type FilingType string

const (
	FilingTypeUnknown FilingType = "unknown"
	FilingTypeQ1      FilingType = "quarterly_q1"
	FilingTypeH1      FilingType = "semiannual"
	FilingTypeQ3      FilingType = "quarterly_q3"
	FilingTypeAnnual  FilingType = "annual"
)

// FilingVariant distinguishes a full periodic report from related documents.
// Summaries are retained as evidence but cannot anchor canonical statement facts.
type FilingVariant string

const (
	FilingVariantOther            FilingVariant = "other"
	FilingVariantFull             FilingVariant = "full"
	FilingVariantSummary          FilingVariant = "summary"
	FilingVariantCorrectionNotice FilingVariant = "correction_notice"
	FilingVariantCorrectedReport  FilingVariant = "corrected_report"
	FilingVariantRevision         FilingVariant = "revision"
)

const (
	FilingResolutionResolved     = "resolved"
	FilingResolutionPending      = "pending"
	FilingResolutionAcknowledged = "acknowledged"
)

// FilingObservation is one authoritative disclosure-platform observation before
// or after canonical instrument resolution. ProviderCode and ExchangeMIC remain
// explicit evidence; InstrumentID is zero until resolution succeeds.
type FilingObservation struct {
	FilingID        int64
	InstrumentID    int64
	Source          string
	SourceFilingID  string
	ProviderCode    string
	ExchangeMIC     string
	SecurityName    string
	Title           string
	FilingType      FilingType
	FilingVariant   FilingVariant
	ReportPeriod    *time.Time
	AnnouncementTime time.Time
	DocumentLocator string
	SourceURL       string
	RawCategory     string
	ClassifierVersion string
	IsCorrection    bool

	CatalogueArtifactID int64
	DocumentArtifactID  int64
	DocumentSHA256      string
	IngestRunID         int64

	ResolutionStatus string
	ResolutionReason string
}

// EligiblePITAnchor reports whether the filing form is authoritative disclosure
// evidence for a complete periodic report or an explicit correction/revision.
// Report summaries and unrelated documents are retained but excluded.
func (f FilingObservation) EligiblePITAnchor() bool {
	if f.ReportPeriod == nil || f.AnnouncementTime.IsZero() {
		return false
	}
	switch f.FilingType {
	case FilingTypeQ1, FilingTypeH1, FilingTypeQ3, FilingTypeAnnual:
	default:
		return false
	}
	switch f.FilingVariant {
	case FilingVariantFull, FilingVariantCorrectionNotice, FilingVariantCorrectedReport, FilingVariantRevision:
		return true
	default:
		return false
	}
}
