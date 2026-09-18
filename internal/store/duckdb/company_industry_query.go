package duckdb

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/source/damodaran"
)

// 标准分类的只读时点投影；来源日期未知，按首次取得日解析身份，不回填历史分类。
func companyIndustriesAsOf(ctx context.Context, tx *sql.Tx, asof time.Time) (map[int64][]any, map[string]any, error) {
	members := map[int64][]any{}
	info := map[string]any{"status": "not_published"}
	candidates, err := tx.QueryContext(ctx, `SELECT release_id FROM meta.dataset_release WHERE source=? AND dataset=? AND available_at<=? AND recorded_at<=?
 QUALIFY dense_rank() OVER(ORDER BY available_at DESC,recorded_at DESC)=1`, damodaran.Source, damodaran.CompanyIndustryDataset, asof, asof)
	if err != nil {
		return nil, info, err
	}
	var releaseID int64
	count := 0
	for candidates.Next() {
		if err = candidates.Scan(&releaseID); err != nil {
			candidates.Close()
			return nil, info, err
		}
		count++
	}
	err = candidates.Err()
	candidates.Close()
	if err != nil {
		return nil, info, err
	}
	if count == 0 {
		return members, info, nil
	}
	if count != 1 {
		return nil, info, errors.New("ambiguous company industry release")
	}
	info["release_id"] = releaseID
	var artifactID, runID int64
	var sha, parser, normalization, key, url string
	var available, recorded, finished time.Time
	err = tx.QueryRowContext(ctx, `SELECT a.artifact_id,a.sha256,r.parser_version,r.normalization_version,r.content_key,a.source_locator,r.available_at,r.recorded_at,r.ingest_run_id,i.finished_at
 FROM meta.dataset_release r JOIN meta.dataset_release_artifact l ON l.release_id=r.release_id AND l.role='data'
 JOIN meta.artifact a ON a.artifact_id=l.artifact_id AND a.source=r.source AND a.dataset=r.dataset
 JOIN meta.checkpoint c ON c.source=r.source AND c.dataset=r.dataset AND c.checkpoint_key=r.content_key AND c.checkpoint_value=CAST(r.release_id AS VARCHAR)
 JOIN meta.ingest_run i ON i.ingest_run_id=r.ingest_run_id AND i.source=r.source AND i.dataset=r.dataset
 WHERE r.release_id=? AND r.source_version IS NULL AND r.source_published_at IS NULL AND r.publication_precision='unknown'
 AND r.availability_basis='first_seen' AND r.available_at=r.first_seen_at AND r.first_seen_at=a.fetched_at
 AND i.status='completed' AND i.finished_at<=?`, releaseID, asof).Scan(&artifactID, &sha, &parser, &normalization, &key, &url, &available, &recorded, &runID, &finished)
	if err != nil {
		return nil, info, fmt.Errorf("company industry publication lineage: %w", err)
	}
	parts := strings.SplitN(normalization, ";", 3)
	if len(parts) != 3 || !strings.HasPrefix(parts[0], "source-company-industries-undated-v2:") || len(parts[1]) != 64 || url != damodaran.CompanyIndustryURL || fmt.Sprintf("%x", sha256.Sum256([]byte(sha+"\n"+parser+"\n"+normalization))) != key {
		return nil, info, errors.New("unverified company industry interpretation; resync source with current parser")
	}
	if _, err = hex.DecodeString(parts[1]); err != nil {
		return nil, info, err
	}
	s := damodaran.CompanyIndustrySnapshot{Contract: "alphalake-company-industries-v1", SHA256: sha, ParserVersion: parser, Runtime: parts[2], TotalRows: 48156, UnidentifiedRows: 12, OutsideScopeRows: 43056}
	catalog, err := tx.QueryContext(ctx, `SELECT n.source_node_code FROM classification.node n JOIN classification.taxonomy t USING(taxonomy_id)
 WHERE t.source=? AND t.taxonomy_code=? AND t.name='Damodaran industries 2026' AND t.taxonomy_type='industry'
 AND n.name=n.source_node_code AND n.level=1 AND n.parent_node_id IS NULL ORDER BY n.source_node_code`, damodaran.Source, damodaran.BetaTaxonomy)
	if err != nil {
		return nil, info, err
	}
	for catalog.Next() {
		var name string
		if err = catalog.Scan(&name); err != nil {
			catalog.Close()
			return nil, info, err
		}
		s.Industries = append(s.Industries, name)
	}
	err = catalog.Err()
	catalog.Close()
	if err != nil {
		return nil, info, err
	}
	observations, err := tx.QueryContext(ctx, `SELECT o.observation_id,o.artifact_id,o.source_locator,o.exchange_ticker,o.raw_payload,n.node_id,n.source_node_code,n.name
 FROM reference.security_industry o LEFT JOIN classification.node n ON n.node_id=o.industry_node_id
 LEFT JOIN classification.taxonomy t ON t.taxonomy_id=n.taxonomy_id AND t.source=? AND t.taxonomy_code=?
 WHERE o.release_id=? AND t.taxonomy_id IS NOT NULL AND n.level=1 AND n.parent_node_id IS NULL ORDER BY TRY_CAST(regexp_extract(o.source_locator,'!A([0-9]+):',1) AS BIGINT)`, damodaran.Source, damodaran.BetaTaxonomy, releaseID)
	if err != nil {
		return nil, info, err
	}
	type evidence struct {
		company damodaran.CompanyIndustry
		node    int64
	}
	evidenceByID := map[int64]evidence{}
	for observations.Next() {
		var id, art, node int64
		var locator, ticker, raw, nodeCode, nodeName string
		if err = observations.Scan(&id, &art, &locator, &ticker, &raw, &node, &nodeCode, &nodeName); err != nil {
			observations.Close()
			return nil, info, err
		}
		var c damodaran.CompanyIndustry
		decoder := json.NewDecoder(strings.NewReader(raw))
		decoder.DisallowUnknownFields()
		if err = decoder.Decode(&c); err != nil {
			observations.Close()
			return nil, info, err
		}
		if art != artifactID || c.Ticker != ticker || c.SourceLocator != locator || c.Industry != nodeCode || c.Industry != nodeName {
			observations.Close()
			return nil, info, errors.New("company industry row lineage changed")
		}
		evidenceByID[id] = evidence{c, node}
		s.Companies = append(s.Companies, c)
	}
	err = observations.Err()
	observations.Close()
	if err != nil {
		return nil, info, err
	}
	if err = damodaran.ValidateCompanyIndustries(s); err != nil {
		return nil, info, err
	}
	if parts[0] != "source-company-industries-undated-v2:"+damodaran.CompanyIndustryDigest(s) {
		return nil, info, errors.New("company industry snapshot digest changed")
	}
	day := available.In(time.FixedZone("China", 8*3600)).Format("2006-01-02")
	identities, err := tx.QueryContext(ctx, `WITH history AS (
 SELECT identifier_value,count(DISTINCT instrument_id) AS identities FROM core.instrument_identifier
 WHERE provider='tdx' AND identifier_type='symbol' GROUP BY identifier_value)
 SELECT o.observation_id,count(d.instrument_id),min(d.instrument_id),min(i.exchange_mic),min(i.instrument_type),min(i.currency),min(CAST(d.valid_from AS VARCHAR)),min(CAST(d.valid_to AS VARCHAR)),min(h.identities)
 FROM reference.security_industry o LEFT JOIN core.instrument_identifier d ON d.provider='tdx' AND d.identifier_type='symbol'
 AND d.identifier_value=(CASE WHEN starts_with(o.exchange_ticker,'SHSE:') THEN 'sh' ELSE 'sz' END)||split_part(o.exchange_ticker,':',2)
 AND (d.valid_from IS NULL OR d.valid_from<=CAST(? AS DATE)) AND (d.valid_to IS NULL OR d.valid_to>CAST(? AS DATE))
 LEFT JOIN core.instrument i ON i.instrument_id=d.instrument_id
 LEFT JOIN history h ON h.identifier_value=(CASE WHEN starts_with(o.exchange_ticker,'SHSE:') THEN 'sh' ELSE 'sz' END)||split_part(o.exchange_ticker,':',2)
 WHERE o.release_id=? GROUP BY o.observation_id ORDER BY o.observation_id`, day, day, releaseID)
	if err != nil {
		return nil, info, err
	}
	counts := map[string]int{}
	pending := []map[string]any{}
	assignments := map[int64][]map[string]any{}
	for identities.Next() {
		var observationID int64
		var matches int
		var id, historicalIdentities sql.NullInt64
		var mic, kind, currency, validFrom, validTo sql.NullString
		if err = identities.Scan(&observationID, &matches, &id, &mic, &kind, &currency, &validFrom, &validTo, &historicalIdentities); err != nil {
			identities.Close()
			return nil, info, err
		}
		e := evidenceByID[observationID]
		expectedMIC := "XSHE"
		if strings.HasPrefix(e.company.Ticker, "SHSE:") {
			expectedMIC = "XSHG"
		}
		status := "resolved_A_equity"
		switch {
		case matches == 0:
			status = "unresolved"
		case matches != 1:
			status = "ambiguous_identity"
		case historicalIdentities.Int64 != 1:
			status = "ambiguous_undated_source_identity"
		case !mic.Valid || mic.String != expectedMIC:
			status = "identity_metadata_conflict"
		case kind.String != "equity" || currency.String != "CNY":
			status = "outside_A_equity_scope"
		}
		if status != "resolved_A_equity" {
			counts[status]++
			pending = append(pending, map[string]any{"source_observation_id": observationID, "ticker": e.company.Ticker, "status": status})
			continue
		}
		identity := map[string]any{"provider": "tdx", "identifier_type": "symbol", "identifier_value": strings.ToLower(strings.Split(e.company.Ticker, ":")[0][:2]) + strings.Split(e.company.Ticker, ":")[1], "valid_from": nil, "valid_to": nil, "known_historical_instrument_count": historicalIdentities.Int64}
		if validFrom.Valid {
			identity["valid_from"] = validFrom.String
		}
		if validTo.Valid {
			identity["valid_to"] = validTo.String
		}
		assignments[id.Int64] = append(assignments[id.Int64], map[string]any{"source": damodaran.Source, "taxonomy_code": damodaran.BetaTaxonomy, "node_code": e.company.Industry, "node_name": e.company.Industry, "node_id": e.node,
			"observed_at": available.UTC().Format(time.RFC3339Nano), "effective_from": day, "ingest_run_id": runID, "run_finished_at": finished.UTC().Format(time.RFC3339Nano),
			"source_release_id": releaseID, "source_observation_id": observationID, "artifact_id": artifactID, "artifact_sha256": sha, "source_ticker": e.company.Ticker, "source_locator": e.company.SourceLocator,
			"identity_basis": "first_seen_snapshot_date_not_source_effective_date", "identity_evidence": identity})
	}
	err = identities.Err()
	identities.Close()
	if err != nil {
		return nil, info, err
	}
	for id, rows := range assignments {
		if len(rows) != 1 {
			counts["ambiguous_source_assignment"] += len(rows)
			for _, r := range rows {
				pending = append(pending, map[string]any{"source_observation_id": r["source_observation_id"], "ticker": r["source_ticker"], "status": "ambiguous_source_assignment"})
			}
			continue
		}
		counts["resolved_A_equity"]++
		members[id] = []any{rows[0]}
	}
	sort.Slice(pending, func(i, j int) bool {
		return pending[i]["source_observation_id"].(int64) < pending[j]["source_observation_id"].(int64)
	})
	info["status"] = "verified_source"
	info["identity_date"] = day
	info["identity_basis"] = "first_seen_snapshot_date_not_source_effective_date"
	info["status_counts"] = counts
	info["unassigned"] = pending
	return members, info, nil
}
