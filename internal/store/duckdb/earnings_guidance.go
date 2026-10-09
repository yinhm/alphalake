package duckdb

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

const GuidanceDataset = "earnings_guidance"

type GuidanceCatalogueWindow struct {
	Start       string  `json:"start"`
	End         string  `json:"end"`
	ArtifactIDs []int64 `json:"artifact_ids"`
}

type GuidanceReceipt struct {
	Contract         string                    `json:"contract_version"`
	Source           string                    `json:"source"`
	Period           string                    `json:"period"`
	Start            string                    `json:"start"`
	End              string                    `json:"end"`
	Scope            []string                  `json:"scope"`
	ScopeHash        string                    `json:"scope_hash"`
	ContentHash      string                    `json:"content_hash"`
	RunID            int64                     `json:"run_id"`
	ObservedAt       time.Time                 `json:"observed_at"`
	Mode             string                    `json:"mode"`
	ArtifactIDs      []int64                   `json:"artifact_ids"`
	CatalogueWindows []GuidanceCatalogueWindow `json:"catalogue_windows,omitempty"`
	ObservationIDs   []string                  `json:"observation_ids"`
	Rows             int                       `json:"source_rows"`
	Issues           int                       `json:"issues"`
}

func GuidanceCheckpointKey(period, start, end string, scope []string) string {
	copy := append([]string{}, scope...)
	sort.Strings(copy)
	raw, _ := json.Marshal(copy)
	sum := sha256.Sum256(raw)
	return "guidance-v1|" + period + "|" + start + "|" + end + "|" + hex.EncodeToString(sum[:])
}

func ValidateGuidanceScope(ctx context.Context, db *sql.DB, scope []string, at time.Time) error {
	filings := make([]domain.FilingObservation, 0, len(scope))
	seen := map[string]bool{}
	for _, ticker := range scope {
		parts := strings.Split(ticker, ":")
		if len(parts) != 2 || (parts[0] != "SHSE" && parts[0] != "SZSE") || !sixDigitProviderCode(parts[1]) || seen[ticker] {
			return fmt.Errorf("invalid/duplicate guidance scope ticker %q", ticker)
		}
		seen[ticker] = true
		filings = append(filings, domain.FilingObservation{ProviderCode: parts[1]})
	}
	ids, err := loadFilingIdentifiers(ctx, db, filings)
	if err != nil {
		return err
	}
	for _, ticker := range scope {
		parts := strings.Split(ticker, ":")
		mic := "XSHG"
		if parts[0] == "SZSE" {
			mic = "XSHE"
		}
		id, _, err := resolveFilingInstrument(ids[parts[1]], parts[1], mic, at)
		if err != nil {
			return err
		}
		if id == 0 {
			return fmt.Errorf("scope ticker has no unique temporal identity: %s", ticker)
		}
	}
	return nil
}

func ResolveGuidanceIdentities(ctx context.Context, db *sql.DB, gs []domain.EarningsGuidance, observed time.Time) error {
	filings := []domain.FilingObservation{}
	for _, g := range gs {
		if sixDigitProviderCode(g.ProviderCode) {
			filings = append(filings, domain.FilingObservation{ProviderCode: g.ProviderCode})
		}
	}
	identifiers, err := loadFilingIdentifiers(ctx, db, filings)
	if err != nil {
		return err
	}
	for i := range gs {
		g := &gs[i]
		g.IdentityStatus = "pending"
		if g.ExchangeMIC == "XBSE" && !domain.IncludesBSE(ctx) {
			g.IdentityStatus = "out_of_scope"
			continue
		}
		if g.ExchangeMIC != "XSHG" && g.ExchangeMIC != "XSHE" {
			continue
		}
		at := observed
		if g.AnnouncementDate != "" {
			at, _ = time.Parse("2006-01-02", g.AnnouncementDate)
		}
		id, _, err := resolveFilingInstrument(identifiers[g.ProviderCode], g.ProviderCode, g.ExchangeMIC, at)
		if err != nil {
			return err
		}
		if id > 0 {
			g.InstrumentID = &id
			g.IdentityStatus = "resolved"
		}
	}
	return nil
}

// PublishGuidance atomically commits wide observations, diagnostics and the complete window.
func PublishGuidance(ctx context.Context, db *sql.DB, key string, receipt GuidanceReceipt, gs []domain.EarningsGuidance, diagnostics []IngestDiagnostic, advance bool) (int, error) {
	if receipt.RunID <= 0 || receipt.ObservedAt.IsZero() || receipt.Source == "" || key == "" {
		return 0, fmt.Errorf("invalid guidance receipt")
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	columns := []string{"observation_id", "source", "event_key", "content_hash", "normalization_version", "ticker", "provider_code", "exchange_mic", "name", "instrument_id", "identity_status", "guidance_kind", "period_start", "period_end", "period_basis", "announcement_date", "announcement_precision", "source_filing_id", "filing_id", "title", "predict_type", "contents", "reasons", "latest_hint", "semantic_status", "verification", "first_observed_at", "source_refs"}
	values := []string{}
	for _, col := range columns {
		expression := "json_extract_string(j.value,'$." + col + "')"
		switch col {
		case "period_start", "announcement_date":
			expression = "CAST(NULLIF(" + expression + ",'') AS DATE)"
		case "period_end":
			expression = "CAST(" + expression + " AS DATE)"
		case "instrument_id", "filing_id":
			expression = "CAST(" + expression + " AS BIGINT)"
		case "latest_hint":
			expression = "CAST(" + expression + " AS BOOLEAN)"
		case "first_observed_at":
			expression = "CAST(" + expression + " AS TIMESTAMPTZ)"
		case "contents", "reasons", "source_refs":
			expression = "json_extract(j.value,'$." + col + "')"
		}
		values = append(values, expression)
	}
	for _, metric := range domain.GuidanceMetrics {
		for _, part := range []string{"lower", "upper", "growth_lower", "growth_upper", "prior", "unit", "representation", "status", "predict_type", "scope"} {
			columns = append(columns, metric+"_"+part)
			v := "json_extract_string(j.value,'$.metrics." + metric + "." + part + "')"
			if part != "unit" && part != "representation" && part != "status" && part != "predict_type" && part != "scope" {
				v = "CAST(" + v + " AS DECIMAL(38,10))"
			}
			values = append(values, v)
		}
	}
	columns = append(columns, "ingest_run_id")
	values = append(values, "?")
	if gs == nil {
		gs = []domain.EarningsGuidance{}
	}
	for _, g := range gs {
		check := g
		if err = check.Seal(); err != nil || check.ID != g.ID || check.ContentHash != g.ContentHash || g.Source != receipt.Source || g.FirstObservedAt.IsZero() || len(g.SourceRefs) == 0 {
			return 0, fmt.Errorf("guidance content signature/source/first observation invalid: %s", g.ID)
		}
		for _, ref := range g.SourceRefs {
			var source string
			if err = tx.QueryRowContext(ctx, `SELECT source FROM meta.artifact WHERE artifact_id=?`, ref.ArtifactID).Scan(&source); err != nil || source != g.Source || ref.Row < 1 {
				return 0, fmt.Errorf("invalid guidance raw reference")
			}
		}
		for name, m := range g.Metrics {
			unit := "CNY"
			if name == "forecast_eps" {
				unit = "CNY/share"
			}
			known := false
			for _, field := range domain.GuidanceMetrics {
				known = known || name == field
			}
			if !known || m.Unit != unit || (m.Status != "valid" && m.Status != "unresolved" && m.Status != "conflicting") {
				return 0, fmt.Errorf("invalid guidance metric definition: %s", name)
			}
		}
	}
	expected := map[string]bool{}
	for _, g := range gs {
		expected[g.ID] = true
	}
	seenMembers := map[string]bool{}
	for _, id := range receipt.ObservationIDs {
		if !expected[id] || seenMembers[id] {
			return 0, fmt.Errorf("invalid guidance receipt membership")
		}
		seenMembers[id] = true
	}
	raw, err := json.Marshal(gs)
	if err != nil {
		return 0, err
	}
	var n int
	if err = tx.QueryRowContext(ctx, `SELECT count(*) FROM json_each(?) j WHERE NOT EXISTS(SELECT 1 FROM fundamental.earnings_guidance_observation o WHERE o.observation_id=json_extract_string(j.value,'$.observation_id'))`, string(raw)).Scan(&n); err != nil {
		return 0, err
	}
	_, err = tx.ExecContext(ctx, "INSERT INTO fundamental.earnings_guidance_observation ("+strings.Join(columns, ",")+") SELECT "+strings.Join(values, ",")+" FROM json_each(?) j ON CONFLICT(observation_id) DO UPDATE SET instrument_id=excluded.instrument_id,identity_status=excluded.identity_status,ticker=excluded.ticker,exchange_mic=excluded.exchange_mic,filing_id=excluded.filing_id", receipt.RunID, string(raw))
	if err != nil {
		return 0, err
	}
	// Sign the canonical membership, not page order or this replay's run ID.
	sort.Strings(receipt.ObservationIDs)
	signature, _ := json.Marshal(struct {
		Source, Period, Start, End, Scope string
		IDs                               []string
	}{receipt.Source, receipt.Period, receipt.Start, receipt.End, receipt.ScopeHash, receipt.ObservationIDs})
	sum := sha256.Sum256(signature)
	receipt.ContentHash = hex.EncodeToString(sum[:])
	if advance {
		payload, err := json.Marshal(receipt)
		if err != nil {
			return 0, err
		}
		if _, err = tx.ExecContext(ctx, `INSERT INTO meta.checkpoint(source,dataset,checkpoint_key,checkpoint_value) VALUES(?,?,?,?) ON CONFLICT(source,dataset,checkpoint_key) DO UPDATE SET checkpoint_value=excluded.checkpoint_value,updated_at=now()`, receipt.Source, GuidanceDataset, key, string(payload)); err != nil {
			return 0, err
		}
	}
	for _, diagnostic := range diagnostics {
		if _, err = tx.ExecContext(ctx, `INSERT INTO meta.validation_result(ingest_run_id,source,dataset,rule_code,severity,subject_type,subject_key,passed,details) SELECT ?,?,?,?,?,?,?,false,? WHERE NOT EXISTS(SELECT 1 FROM meta.validation_result WHERE source=? AND dataset=? AND rule_code=? AND subject_key=? AND details=?)`, receipt.RunID, receipt.Source, GuidanceDataset, diagnostic.RuleCode, diagnostic.Severity, diagnostic.SubjectType, diagnostic.SubjectKey, diagnostic.Details, receipt.Source, GuidanceDataset, diagnostic.RuleCode, diagnostic.SubjectKey, diagnostic.Details); err != nil {
			return 0, err
		}
	}
	if err = tx.Commit(); err != nil {
		return 0, err
	}
	return int(n), nil
}

func PublishGuidanceRelations(ctx context.Context, db *sql.DB, runID int64, relations []domain.GuidanceRelation) (int, error) {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	n := 0
	for _, relation := range relations {
		if relation.Evidence == "" || relation.Rule == "" || runID <= 0 {
			return 0, fmt.Errorf("guidance relation requires evidence/rule/run")
		}
		raw, _ := json.Marshal(relation)
		sum := sha256.Sum256(raw)
		var revokes any
		if relation.RevokesID != "" {
			revokes = relation.RevokesID
			var from, to, kind string
			if err = tx.QueryRowContext(ctx, `SELECT from_id,to_id,kind FROM fundamental.earnings_guidance_relation WHERE relation_id=?`, revokes).Scan(&from, &to, &kind); err != nil || from != relation.FromID || to != relation.ToID || kind == "revoke" || relation.Kind != "revoke" {
				return 0, fmt.Errorf("invalid guidance relation revocation")
			}
		}
		r, err := tx.ExecContext(ctx, `INSERT INTO fundamental.earnings_guidance_relation(relation_id,from_id,to_id,kind,evidence,rule_version,revokes_id,ingest_run_id) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING`, hex.EncodeToString(sum[:]), relation.FromID, relation.ToID, relation.Kind, relation.Evidence, relation.Rule, revokes, runID)
		if err != nil {
			return 0, err
		}
		count, err := r.RowsAffected()
		if err != nil {
			return 0, err
		}
		n += int(count)
	}
	if err = tx.Commit(); err != nil {
		return 0, err
	}
	return n, nil
}

func QueryEarningsGuidance(ctx context.Context, db *sql.DB, period string, scope []string) ([]map[string]any, error) {
	rows, err := db.QueryContext(ctx, `SELECT CAST(to_json(q) AS VARCHAR) FROM (
 SELECT g.*,
 CASE WHEN EXISTS(SELECT 1 FROM fundamental.earnings_guidance_relation r WHERE r.from_id=g.observation_id AND r.kind='withdraws' AND NOT EXISTS(SELECT 1 FROM fundamental.earnings_guidance_relation x WHERE x.revokes_id=r.relation_id)) THEN 'withdrawn'
 WHEN EXISTS(SELECT 1 FROM fundamental.earnings_guidance_relation r WHERE r.from_id=g.observation_id AND r.kind='supersedes' AND NOT EXISTS(SELECT 1 FROM fundamental.earnings_guidance_relation x WHERE x.revokes_id=r.relation_id)) THEN 'superseded' ELSE 'current' END AS lifecycle,
 EXISTS(SELECT 1 FROM fundamental.earnings_guidance_relation r WHERE r.kind='same_disclosure' AND (r.from_id=g.observation_id OR r.to_id=g.observation_id) AND NOT EXISTS(SELECT 1 FROM fundamental.earnings_guidance_relation x WHERE x.revokes_id=r.relation_id)) AS linked_disclosure,
 EXISTS(SELECT 1 FROM meta.ingest_run f JOIN meta.ingest_run p ON p.ingest_run_id=g.published_run_id WHERE f.source=g.source AND f.dataset='earnings_guidance' AND f.status IN ('failed','canceled') AND f.started_at>p.started_at AND json_extract_string(f.checkpoint_before,'$.period')=CAST(g.period_end AS VARCHAR) AND json_extract_string(f.checkpoint_before,'$.scope_hash')=split_part(g.checkpoint_key,'|',5) AND json_extract_string(f.checkpoint_before,'$.start')=split_part(g.checkpoint_key,'|',3) AND json_extract_string(f.checkpoint_before,'$.end')=split_part(g.checkpoint_key,'|',4)) AS source_stale
 FROM fundamental.current_earnings_guidance g WHERE period_end=CAST(? AS DATE) AND ticker IN (SELECT unnest(?::VARCHAR[]))) q ORDER BY ticker,source,announcement_date,event_key`, period, scope)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		var payload string
		if err = rows.Scan(&payload); err != nil {
			return nil, err
		}
		var item map[string]any
		decoder := json.NewDecoder(strings.NewReader(payload))
		decoder.UseNumber()
		if err = decoder.Decode(&item); err != nil {
			return nil, err
		}
		if item["linked_disclosure"] == true {
			item["verification"] = "linked"
		}
		delete(item, "provider_code")
		// JSON decimals remain strings at the public boundary.
		for _, name := range domain.GuidanceMetrics {
			for _, part := range []string{"lower", "upper", "growth_lower", "growth_upper", "prior"} {
				key := name + "_" + part
				if value, ok := item[key].(json.Number); ok {
					item[key] = value.String()
				}
			}
		}
		out = append(out, item)
	}
	return out, rows.Err()
}
