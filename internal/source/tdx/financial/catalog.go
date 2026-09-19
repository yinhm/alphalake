package financial

import (
	"bytes"
	_ "embed"
	"encoding/csv"
	"fmt"
	"math"
	"strconv"
	"strings"

	"github.com/yinhm/alphalake/internal/domain"
)

// CatalogVersion identifies the complete frozen source dictionary, not a claim
// that all metrics are reviewed standard facts or suitable for valuation.
const CatalogVersion = "tdx-financial-20260919-v2"

//go:embed catalog.csv
var catalogCSV []byte

type FieldDefinition struct {
	RequiresDisambiguation bool     `json:"requires_source_disambiguation,omitempty"`
	Index                  int      `json:"source_index"`
	Name                   string   `json:"name,omitempty"`
	Label                  string   `json:"label"`
	Category               string   `json:"category"`
	ValueKind              string   `json:"value_kind"`
	Unit                   string   `json:"unit"`
	Multiplier             *float64 `json:"multiplier,omitempty"`
	PeriodBasis            string   `json:"period_basis"`
	DefinitionStatus       string   `json:"definition_status"`
	MappingStatus          string   `json:"mapping_status"`
	Reference              string   `json:"reference,omitempty"`
}

// FieldCatalog includes every position, including explicitly unresolved ones.
// Return independent rows so callers cannot mutate the shared dictionary.
func FieldCatalog() ([]FieldDefinition, error) {
	rows, err := csv.NewReader(bytes.NewReader(catalogCSV)).ReadAll()
	if err != nil {
		return nil, err
	}
	out := make([]FieldDefinition, 0, len(rows)-1)
	for i, r := range rows[1:] {
		if len(r) != 11 {
			return nil, fmt.Errorf("financial catalog row %d: invalid columns", i+2)
		}
		index, err := strconv.Atoi(r[0])
		if err != nil || index != i+1 {
			return nil, fmt.Errorf("financial catalog row %d: invalid index", i+2)
		}
		f := FieldDefinition{Index: index, Name: r[1], Label: r[2], Category: r[3], ValueKind: r[4], Unit: r[5], PeriodBasis: r[7], DefinitionStatus: r[8], MappingStatus: r[9], Reference: r[10]}
		if r[6] != "" {
			n, e := strconv.ParseFloat(r[6], 64)
			if e != nil || (n != 1 && n != 10000) {
				return nil, fmt.Errorf("financial catalog index %d: invalid multiplier", index)
			}
			f.Multiplier = &n
		}
		if f.Name != "" && (strings.TrimSpace(f.Name) != f.Name || strings.ContainsAny(f.Name, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 \t")) {
			return nil, fmt.Errorf("financial catalog index %d: nonstandard name", index)
		}
		out = append(out, f)
	}
	counts := map[string]int{}
	for _, f := range out {
		if f.Name != "" {
			counts[f.Name]++
		}
	}
	for i := range out {
		out[i].RequiresDisambiguation = counts[out[i].Name] > 1
	}
	return out, nil
}

type NamedSourceValue struct {
	Field            string              `json:"field,omitempty"`
	Value            *float64            `json:"value,omitempty"`
	Unit             string              `json:"unit"`
	PeriodBasis      string              `json:"period_basis"`
	State            string              `json:"state"`
	DefinitionStatus string              `json:"definition_status"`
	Evidence         SourceValueEvidence `json:"source_evidence"`
}

type SourceValueEvidence struct {
	Field     string   `json:"provider_field"`
	Bits      *uint32  `json:"float32_bits"`
	RawValue  *float64 `json:"raw_value,omitempty"`
	Reference string   `json:"definition_reference,omitempty"`
}

// DecodeSourceValue never promotes a provider observation into a standard fact.
// Unknown positions retain their bits; missing units and ambiguous zeros do not
// acquire fabricated normalized values. Dates remain explicit YYMMDD encodings.
func DecodeSourceValue(f FieldDefinition, value domain.ProviderFloat32) NamedSourceValue {
	out := NamedSourceValue{Field: f.Name, Unit: f.Unit, PeriodBasis: f.PeriodBasis, DefinitionStatus: f.DefinitionStatus,
		Evidence: SourceValueEvidence{Field: fmt.Sprintf("FN%d", f.Index), Bits: &value.Bits, Reference: f.Reference}}
	raw := float64(math.Float32frombits(value.Bits))
	if math.IsNaN(raw) || math.IsInf(raw, 0) {
		out.State = "nonfinite_source"
		return out
	}
	out.Evidence.RawValue = &raw
	if raw != value.Value {
		out.State = "source_bits_mismatch"
		return out
	}
	if f.Name == "" {
		out.State = "undefined_source_field"
		return out
	}
	if f.RequiresDisambiguation {
		out.State = "ambiguous_source_variant"
		return out
	}
	if raw == 0 && f.ValueKind != "code" {
		out.State = "zero_requires_review"
		return out
	}
	if f.ValueKind == "date" {
		out.State = "source_date_encoding_only"
		return out
	}
	if f.Multiplier == nil || f.Unit == "unspecified" {
		out.State = "unit_requires_review"
		return out
	}
	scaled := raw * *f.Multiplier
	if math.IsNaN(scaled) || math.IsInf(scaled, 0) {
		out.State = "nonfinite_normalized_value"
		return out
	}
	out.Value = &scaled
	out.State = "source_observation_not_standard_fact"
	return out
}
