package domain

import "time"

// ProviderFloat32 preserves the exact 32-bit provider representation while also
// exposing its exactly representable float64 value for analytical storage.
type ProviderFloat32 struct {
	Bits  uint32
	Value float64
}

// ProviderFinancialRecord is one provider report-period row before semantic FN
// mapping. AnnouncementTime is intentionally optional: it must come from an
// authoritative provider/filing observation, never be inferred from fetch time.
type ProviderFinancialRecord struct {
	Identifier       Identifier
	ReportPeriod     time.Time
	AnnouncementTime *time.Time
	ProviderFields   []ProviderFloat32 // index 0 is FN1, index n-1 is FNn
	SourceFile       string
	ArtifactID       int64
}
