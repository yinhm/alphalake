package duckdb

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/source/damodaran"
)

func TestIssuerReviewPublicationReplayRevocationAndIdentity(t *testing.T) {
	ctx := t.Context()
	root := t.TempDir()
	db, err := OpenInitialized(ctx, filepath.Join(root, "issuer.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	exec := func(q string, args ...any) {
		t.Helper()
		if _, e := db.ExecContext(ctx, q, args...); e != nil {
			t.Fatal(e)
		}
	}
	exec(`INSERT INTO meta.ingest_run(ingest_run_id,source,dataset,status) VALUES(1,'damodaran',?,'completed')`, damodaran.CompanyIndustryDataset)
	sha := strings.Repeat("a", 64)
	exec(`INSERT INTO meta.artifact(source,dataset,source_locator,fetched_at,sha256,content_length) VALUES('damodaran',?,'fixture',now(),?,1)`, damodaran.CompanyIndustryDataset, sha)
	exec(`INSERT INTO meta.dataset_release(release_id,source,dataset,content_key,publication_precision,available_at,availability_basis,first_seen_at,parser_version,normalization_version,ingest_run_id) VALUES(1,'damodaran',?,?,'unknown',now(),'first_seen',now(),'fixture','fixture',1)`, damodaran.CompanyIndustryDataset, sha)
	exec(`INSERT INTO meta.dataset_release_artifact VALUES(1,1,'data')`)
	exec(`INSERT INTO meta.checkpoint(source,dataset,checkpoint_key,checkpoint_value) VALUES('damodaran',?,?,'1')`, damodaran.CompanyIndustryDataset, sha)
	exec(`INSERT INTO core.instrument(instrument_id,instrument_type,exchange_mic,currency) VALUES(1,'equity','XSHE','CNY')`)
	exec(`INSERT INTO core.instrument_identifier(instrument_id,provider,identifier_type,identifier_value) VALUES(1,'tdx','symbol','sz001234')`)
	packet := IssuerIndustryPacket{Contract: "alphalake-reviewed-issuer-inputs-v1", WorkbookSHA256: sha, ManifestSHA256: sha, Active: []IssuerIndustryAssociation{{TargetTicker: "SZSE:001234", SourceTicker: "SEHK:1234", SourceLocator: "By company name!A2:H2", ReviewedAt: time.Now().Add(-time.Hour).UTC(), Relationship: "same_legal_issuer_different_share_class", SourceCompany: damodaran.CompanyIndustry{Ticker: "SEHK:1234", SourceLocator: "By company name!A2:H2", Industry: "Power", Country: "China"}}}, ReviewEvents: []IssuerIndustryReviewEvent{{ReviewSHA256: sha, ReviewedRecord: json.RawMessage(`{"action":"publish"}`)}}}
	publish := func(p IssuerIndustryPacket) (bool, error) {
		raw, _ := json.Marshal(p)
		stored, e := artifact.Persist(ctx, db, root, artifact.Input{Source: "issuer-review", Dataset: "company_industry", SourceLocator: "review://fixture.json", FetchedAt: time.Now(), Content: raw})
		if e != nil {
			t.Fatal(e)
		}
		return ImportIssuerIndustryReview(ctx, db, stored.ArtifactID, p)
	}
	if inserted, e := publish(packet); e != nil || !inserted {
		t.Fatal(inserted, e)
	}
	if inserted, e := publish(packet); e != nil || inserted {
		t.Fatal("replay", inserted, e)
	}
	read := func(at time.Time, sourceSHA string) ([]IssuerIndustryAssociation, error) {
		tx, e := db.BeginTx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		return issuerIndustryAssociations(ctx, tx, 1, sourceSHA, at)
	}
	at := time.Now().Add(time.Minute)
	associations, e := read(at, sha)
	if e != nil || len(associations) != 1 || associations[0].InstrumentID != 1 {
		t.Fatal(associations, e)
	}
	if rows, e := read(time.Now().Add(-time.Minute), sha); e != nil || len(rows) != 0 {
		t.Fatal("future review", rows, e)
	}
	if rows, e := read(at, strings.Repeat("b", 64)); e != nil || len(rows) != 0 {
		t.Fatal("stale workbook", rows, e)
	}
	exec(`INSERT INTO core.instrument(instrument_id,instrument_type,exchange_mic,currency) VALUES(2,'equity','XSHE','CNY'); INSERT INTO core.instrument_identifier(instrument_id,provider,identifier_type,identifier_value,valid_to) VALUES(2,'tdx','symbol','sz001234','2020-01-01')`)
	if rows, e := read(at, sha); e != nil || len(rows) != 0 {
		t.Fatal("undated identity ambiguity", rows, e)
	}
	exec(`DELETE FROM core.instrument_identifier WHERE instrument_id=2; DELETE FROM core.instrument WHERE instrument_id=2`)
	// An exact source record always wins without changing the review evidence.
	exec(`INSERT INTO reference.security_industry(release_id,artifact_id,source_locator,exchange_ticker,industry_node_id,raw_payload) VALUES(1,1,'fixture!A3:H3','SZSE:001234',1,'{}')`)
	if rows, e := read(at, sha); e != nil || len(rows) != 0 {
		t.Fatal("exact source priority", rows, e)
	}
	exec(`DELETE FROM reference.security_industry`)
	packet.Active = []IssuerIndustryAssociation{}
	packet.ReviewEvents = append(packet.ReviewEvents, IssuerIndustryReviewEvent{ReviewSHA256: strings.Repeat("c", 64), ReviewedRecord: json.RawMessage(`{"action":"revoke"}`)})
	if inserted, e := publish(packet); e != nil || !inserted {
		t.Fatal("revoke", inserted, e)
	}
	if rows, e := read(at, sha); e != nil || len(rows) != 0 {
		t.Fatal("revocation fell back", rows, e)
	}
	old := packet
	old.ReviewEvents = old.ReviewEvents[:1]
	if _, e := publish(old); e == nil {
		t.Fatal("old ledger replay accepted")
	}
	exec(`UPDATE reference.issuer_industry_review SET verified_record='{}' WHERE artifact_id=(SELECT max(artifact_id) FROM reference.issuer_industry_review)`)
	if _, e := read(at, sha); e == nil {
		t.Fatal("tampered verification accepted")
	}
}

func TestUpgradeIssuerReferencesIsExplicitAndPreservesFacts(t *testing.T) {
	db, err := OpenInitialized(t.Context(), filepath.Join(t.TempDir(), "upgrade.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err = db.Exec(`DROP TABLE reference.issuer_industry_review; UPDATE meta.schema_version SET version=56; INSERT INTO fundamental.statement_snapshot(source_record_id,instrument_id,report_period,ingest_run_id,revenue) VALUES(1,1,'2025-12-31',1,123.5)`); err != nil {
		t.Fatal(err)
	}
	if err = UpgradeIssuerReferences(t.Context(), db); err != nil {
		t.Fatal(err)
	}
	var value float64
	if err = db.QueryRow(`SELECT revenue FROM fundamental.statement_snapshot`).Scan(&value); err != nil || value != 123.5 {
		t.Fatal(value, err)
	}
	if err = UpgradeIssuerReferences(t.Context(), db); err == nil {
		t.Fatal("repeat upgrade accepted")
	}
}
