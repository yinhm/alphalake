package duckdb

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

func insertSourceFieldCatalog(ctx context.Context, tx *sql.Tx) error {
	fields, err := financial.FieldCatalog()
	if err != nil {
		return err
	}
	stmt, err := tx.PrepareContext(ctx, `INSERT INTO fundamental.source_field VALUES ('tdx',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`)
	if err != nil {
		return err
	}
	defer stmt.Close()
	for _, f := range fields {
		if _, err = stmt.ExecContext(ctx, fmt.Sprintf("FN%d", f.Index), f.Index, sql.NullString{String: f.Name, Valid: f.Name != ""}, f.Label, f.Category, f.ValueKind, f.Unit, f.Multiplier, f.PeriodBasis, f.DefinitionStatus, f.Reference, financial.CatalogVersion, f.Statement, f.Section, f.MappingStatus, f.ReviewReason); err != nil {
			return err
		}
	}
	// Existing reviewed mappings retain their dates and zero policies. New
	// official mappings are generated from the same complete source catalog.
	_, err = tx.ExecContext(ctx, `
 INSERT INTO fundamental.field
 SELECT name,unit,value_kind,period_basis FROM fundamental.source_field s
 WHERE mapping_status='official_mapping'
 AND NOT EXISTS (SELECT 1 FROM fundamental.field f WHERE f.canonical_field=s.name);
 INSERT INTO fundamental.provider_field
 (source,provider_field,canonical_field,display_name,unit,value_kind,valid_from,notes,period_basis,value_multiplier,zero_policy)
 SELECT source,provider_field,name,display_name,unit,value_kind,
 CASE WHEN name='top_ten_tradable_a_shares' THEN DATE '2019-06-30' ELSE DATE '1900-01-01' END,
 'supplementary-metrics-20260925;'||definition_reference,period_basis,CAST(value_multiplier AS INTEGER),'reject'
 FROM fundamental.source_field s WHERE mapping_status='official_mapping'
 AND NOT EXISTS (SELECT 1 FROM fundamental.provider_field p WHERE p.source=s.source AND p.provider_field=s.provider_field)`)
	return err
}

type SourceFinancialObservation struct {
	financial.NamedSourceValue
	ProviderFactID int64  `json:"provider_fact_id"`
	Revision       string `json:"source_revision"`
	ArtifactID     *int64 `json:"artifact_id,omitempty"`
	SourceRow      int    `json:"source_row"`
	MappingSHA256  string `json:"mapping_sha256,omitempty"`
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

	root, err := FinancialArchiveRoot(ctx, db)
	if err != nil {
		return out, err
	}
	fields, err := financial.FieldCatalog()
	if err != nil {
		return out, err
	}
	mappings := map[int]string{}
	zeroAllowed := map[int]bool{}
	mappingRows, err := db.QueryContext(ctx, `SELECT CAST(substr(m.provider_field,3) AS INTEGER),sha256(CAST(to_json(m) AS VARCHAR)),m.zero_policy='allow' FROM fundamental.statement_field m WHERE source='tdx' AND valid_from<=? AND (valid_to IS NULL OR ?<valid_to)`, period, period)
	if err != nil {
		return out, err
	}
	for mappingRows.Next() {
		var index int
		var hash string
		var allow bool
		if err = mappingRows.Scan(&index, &hash, &allow); err != nil {
			mappingRows.Close()
			return out, err
		}
		mappings[index] = hash
		zeroAllowed[index] = allow
	}
	err = mappingRows.Err()
	mappingRows.Close()
	if err != nil {
		return out, err
	}
	rows, err := db.QueryContext(ctx, `SELECT r.source_record_id,r.source_row,r.artifact_id,a.local_path,a.source_locator,a.sha256,a.content_length FROM fundamental.source_record r JOIN meta.artifact a USING(artifact_id) WHERE r.provider_code=? AND r.report_period=? ORDER BY a.sha256,r.source_row`, code, period)
	if err != nil {
		return out, err
	}
	type locator struct {
		id, artifact, size int64
		row                int
		path, name, hash   string
	}
	var records []locator
	for rows.Next() {
		var r locator
		if err = rows.Scan(&r.id, &r.row, &r.artifact, &r.path, &r.name, &r.hash, &r.size); err != nil {
			rows.Close()
			return out, err
		}
		records = append(records, r)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return out, err
	}
	for _, r := range records {
		pkg, e := ReadFinancialArchive(root, r.path, r.name, r.hash, r.size)
		if e != nil {
			return out, e
		}
		if r.row < 1 || r.row > len(pkg.Records) || pkg.Records[r.row-1].Code != code {
			return out, fmt.Errorf("source row identity mismatch")
		}
		for i, v := range pkg.Records[r.row-1].Fields {
			f := financial.FieldDefinition{Index: i + 1, Unit: "unspecified", PeriodBasis: "unspecified", DefinitionStatus: "unpublished"}
			if i < len(fields) {
				f = fields[i]
			}
			o := SourceFinancialObservation{NamedSourceValue: financial.DecodeSourceValue(f, v), ProviderFactID: r.id*8192 + int64(i+1), Revision: r.hash, ArtifactID: &r.artifact, SourceRow: r.row, MappingSHA256: mappings[i+1]}
			if o.State == "zero_requires_review" && zeroAllowed[i+1] {
				zero := 0.0
				o.Value = &zero
				o.State = "source_observation_not_standard_fact"
			}
			out.States[o.State]++
			out.Observations = append(out.Observations, o)
		}
	}
	return out, nil
}
