package duckdb

import (
	"context"
	"database/sql"
	"fmt"
	"math"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

func insertSourceFieldCatalog(ctx context.Context, tx *sql.Tx) error {
	fields, err := financial.FieldCatalog()
	if err != nil {
		return err
	}
	stmt, err := tx.PrepareContext(ctx, `INSERT INTO fundamental.source_field VALUES ('tdx',?,?,?,?,?,?,?,?,?,?,?,?)`)
	if err != nil {
		return err
	}
	defer stmt.Close()
	for _, f := range fields {
		if _, err = stmt.ExecContext(ctx, fmt.Sprintf("FN%d", f.Index), f.Index, sql.NullString{String: f.Name, Valid: f.Name != ""}, f.Label, f.Category, f.ValueKind, f.Unit, f.Multiplier, f.PeriodBasis, f.DefinitionStatus, f.Reference, financial.CatalogVersion); err != nil {
			return err
		}
	}
	return nil
}

type SourceFinancialObservation struct {
	financial.NamedSourceValue
	ProviderFactID int64  `json:"provider_fact_id"`
	Revision       string `json:"source_revision"`
	ArtifactID     *int64 `json:"artifact_id,omitempty"`
}

type SourceFinancialExport struct {
	ContractVersion string                       `json:"contract_version"`
	CatalogVersion  string                       `json:"catalog_version"`
	Code            string                       `json:"code"`
	ReportPeriod    string                       `json:"report_period"`
	Scope           string                       `json:"scope"`
	States          map[string]int               `json:"states"`
	Observations    []SourceFinancialObservation `json:"observations"`
}

// ExportSourceFinancialData is an explicit source-maintenance export. It keeps
// every revision and unknown position; it is not a PIT or valuation contract.
func ExportSourceFinancialData(ctx context.Context, db *sql.DB, code string, period time.Time) (SourceFinancialExport, error) {
	out := SourceFinancialExport{ContractVersion: "alphalake-source-financial-v1", CatalogVersion: financial.CatalogVersion, Code: code, ReportPeriod: period.Format("2006-01-02"), Scope: "all_local_source_revisions_not_standard_facts_or_pit", States: map[string]int{}, Observations: []SourceFinancialObservation{}}
	if len(code) != 6 || strings.IndexFunc(code, func(r rune) bool { return r < '0' || r > '9' }) >= 0 {
		return out, fmt.Errorf("six-digit provider code required")
	}
	version, err := CurrentSchemaVersion(ctx, db)
	if err != nil {
		return out, err
	}
	if version != SchemaVersion {
		return out, fmt.Errorf("unsupported schema %d; expected %d", version, SchemaVersion)
	}
	rows, err := db.QueryContext(ctx, `SELECT p.provider_fact_id,p.provider_field,p.revision_key,p.artifact_id,p.value,p.value_float32_bits,
 c.source_index,coalesce(c.name,''),coalesce(c.display_name,''),coalesce(c.category,''),coalesce(c.value_kind,''),coalesce(c.unit,'unspecified'),c.value_multiplier,coalesce(c.period_basis,'unspecified'),coalesce(c.definition_status,'unpublished'),coalesce(c.definition_reference,''),coalesce(c.name IN (SELECT name FROM fundamental.source_field WHERE source='tdx' AND name IS NOT NULL GROUP BY name HAVING count(*)>1),false)
 FROM fundamental.provider_fact p LEFT JOIN fundamental.source_field c ON c.source=p.source AND c.provider_field=p.provider_field
 WHERE p.source='tdx' AND p.provider_code=? AND p.report_period=? ORDER BY p.revision_key,try_cast(substr(p.provider_field,3) AS INTEGER),p.provider_fact_id`, code, period)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var o SourceFinancialObservation
		var f financial.FieldDefinition
		var provider string
		var raw, mult sql.NullFloat64
		var bits, index, artifact sql.NullInt64
		if err = rows.Scan(&o.ProviderFactID, &provider, &o.Revision, &artifact, &raw, &bits, &index, &f.Name, &f.Label, &f.Category, &f.ValueKind, &f.Unit, &mult, &f.PeriodBasis, &f.DefinitionStatus, &f.Reference, &f.RequiresDisambiguation); err != nil {
			return out, err
		}
		if index.Valid {
			f.Index = int(index.Int64)
		}
		if mult.Valid {
			f.Multiplier = &mult.Float64
		}
		if artifact.Valid {
			n := artifact.Int64
			o.ArtifactID = &n
		}
		if raw.Valid && bits.Valid && bits.Int64 >= 0 && bits.Int64 <= math.MaxUint32 {
			o.NamedSourceValue = financial.DecodeSourceValue(f, domain.ProviderFloat32{Bits: uint32(bits.Int64), Value: raw.Float64})
		} else {
			o.NamedSourceValue = financial.NamedSourceValue{Field: f.Name, Unit: f.Unit, PeriodBasis: f.PeriodBasis, DefinitionStatus: f.DefinitionStatus, State: "missing_source_encoding"}
		}
		if o.Evidence.Bits == nil && bits.Valid && bits.Int64 >= 0 && bits.Int64 <= math.MaxUint32 {
			n := uint32(bits.Int64)
			o.Evidence.Bits = &n
		}
		if o.Evidence.RawValue == nil && raw.Valid && !math.IsNaN(raw.Float64) && !math.IsInf(raw.Float64, 0) {
			n := raw.Float64
			o.Evidence.RawValue = &n
		}
		o.Evidence.Field = provider
		out.States[o.State]++
		out.Observations = append(out.Observations, o)
	}
	return out, rows.Err()
}
