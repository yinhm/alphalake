package domain

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"time"
)

// GuidanceMetrics are business columns, not provider field numbers.
var GuidanceMetrics = []string{"forecast_parent_net_income", "forecast_adjusted_net_income", "forecast_eps", "forecast_revenue"}

type GuidanceMetric struct {
	Scope          string  `json:"scope"`
	PredictType    string  `json:"predict_type"`
	Lower          *string `json:"lower"`
	Upper          *string `json:"upper"`
	GrowthLower    *string `json:"growth_lower"`
	GrowthUpper    *string `json:"growth_upper"`
	Prior          *string `json:"prior"`
	Unit           string  `json:"unit"`
	Representation string  `json:"representation"`
	Status         string  `json:"status"`
}

type GuidanceRef struct {
	ArtifactID int64 `json:"artifact_id"`
	Row        int   `json:"row"`
}

type EarningsGuidance struct {
	ID                    string                    `json:"observation_id"`
	Source                string                    `json:"source"`
	EventKey              string                    `json:"event_key"`
	ContentHash           string                    `json:"content_hash"`
	NormalizationVersion  string                    `json:"normalization_version"`
	Ticker                string                    `json:"ticker"`
	ProviderCode          string                    `json:"provider_code"`
	ExchangeMIC           string                    `json:"exchange_mic"`
	Name                  string                    `json:"name"`
	InstrumentID          *int64                    `json:"instrument_id"`
	IdentityStatus        string                    `json:"identity_status"`
	Kind                  string                    `json:"guidance_kind"`
	PeriodStart           string                    `json:"period_start"`
	PeriodEnd             string                    `json:"period_end"`
	PeriodBasis           string                    `json:"period_basis"`
	AnnouncementDate      string                    `json:"announcement_date"`
	AnnouncementPrecision string                    `json:"announcement_precision"`
	SourceFilingID        string                    `json:"source_filing_id"`
	FilingID              *int64                    `json:"filing_id"`
	Title                 string                    `json:"title"`
	PredictType           string                    `json:"predict_type"`
	Contents              []string                  `json:"contents"`
	Reasons               []string                  `json:"reasons"`
	LatestHint            bool                      `json:"latest_hint"`
	Metrics               map[string]GuidanceMetric `json:"metrics"`
	SemanticStatus        string                    `json:"semantic_status"`
	Verification          string                    `json:"verification"`
	FirstObservedAt       time.Time                 `json:"first_observed_at"`
	SourceRefs            []GuidanceRef             `json:"source_refs"`
}

// Seal excludes acquisition order, identity resolution and raw-file positions.
func (g *EarningsGuidance) Seal() error {
	copy := *g
	copy.ID, copy.ContentHash = "", ""
	copy.InstrumentID, copy.FilingID = nil, nil
	copy.Ticker, copy.ExchangeMIC = "", ""
	copy.IdentityStatus, copy.Verification = "", ""
	copy.FirstObservedAt, copy.SourceRefs = time.Time{}, nil
	raw, err := json.Marshal(copy)
	if err != nil {
		return err
	}
	sum := sha256.Sum256(raw)
	g.ContentHash = hex.EncodeToString(sum[:])
	sum = sha256.Sum256([]byte(g.Source + "|" + g.EventKey + "|" + g.ContentHash + "|" + g.NormalizationVersion))
	g.ID = hex.EncodeToString(sum[:])
	return nil
}

type GuidanceRelation struct {
	FromID    string `json:"from_id"`
	ToID      string `json:"to_id"`
	Kind      string `json:"kind"`
	Evidence  string `json:"evidence"`
	Rule      string `json:"rule"`
	RevokesID string `json:"revokes_id"`
}
