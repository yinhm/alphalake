package ingest

import (
	"bytes"
	"crypto/sha256"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/domain"
	"github.com/yinhm/alphalake/internal/source/cninfo"
	"github.com/yinhm/alphalake/internal/source/tdx"
	"github.com/yinhm/alphalake/internal/source/tdx/financial"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

// 使用生产归一化、身份解析、物化和查询；CSV 是查询快照，不是直接解码包生成的替代事实。
func TestRealValuationStandardChain(t *testing.T) {
	const dir = "testdata/valuation-chain-2026"
	ctx := t.Context()
	check := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	dbPath := filepath.Join(t.TempDir(), "valuation.duckdb")
	db, err := duckstore.OpenAndMigrate(ctx, dbPath)
	check(err)
	defer db.Close()
	// 冻结 CSV 是 UTC 渲染；TIMESTAMPTZ 的 VARCHAR 转换随会话时区变化。
	// SET 只作用于单个连接，先限制连接池再钉死时区，非 UTC 环境才可复现；
	// 每次重开数据库句柄都必须重新钉死。
	pinUTC := func() {
		t.Helper()
		db.SetMaxOpenConns(1)
		_, err = db.ExecContext(ctx, `SET TimeZone='UTC'`)
		check(err)
	}
	pinUTC()
	// 保留本历史验收的字段分母；045由独立真实处置现金测试验收。
	_, err = db.ExecContext(ctx, `DELETE FROM fundamental.provider_field WHERE source='tdx' AND provider_field='FN110'`)
	check(err)
	// 025 只新增映射；构造 v24 状态后实际导入全部真实源记录。
	_, err = db.ExecContext(ctx, `DELETE FROM fundamental.provider_field WHERE source='tdx' AND provider_field IN ('FN9','FN59','FN299','FN403','FN409','FN411','FN413','FN430','FN431','FN433','FN434','FN437','FN506','FN509','FN510','FN520','FN579'); DELETE FROM meta.schema_version WHERE version=25`)
	check(err)
	root := filepath.Join(t.TempDir(), "raw")
	var instruments []domain.InstrumentObservation
	check(json.Unmarshal(readFinancialSample(t, dir, "instruments.json"), &instruments))
	_, err = duckstore.UpsertInstruments(ctx, db, instruments)
	check(err)
	run, err := duckstore.StartIngestRun(ctx, db, "tdx", "valuation_acceptance_sample", nil)
	check(err)
	docClient, err := cninfo.NewDefaultClient()
	check(err)
	for _, path := range []string{dir + "/600519-catalogue.json", dir + "/300866-catalogue.json", "testdata/moutai-valuation-2026/catalogue.json", "testdata/moutai-valuation-2026/finance-catalogue.json", "testdata/anker-valuation-2026/catalogue.json"} {
		raw := readFinancialSample(t, filepath.Dir(path), filepath.Base(path))
		var meta struct {
			FetchedAt time.Time `json:"fetched_at"`
			SHA       string    `json:"sha256"`
		}
		var reports map[string]json.RawMessage
		if filepath.Dir(path) == dir {
			check(json.Unmarshal(readFinancialSample(t, dir, filepath.Base(path[:len(path)-5])+".meta.json"), &meta))
		} else {
			check(json.Unmarshal(readFinancialSample(t, filepath.Dir(path), "reports.json"), &reports))
			check(json.Unmarshal(reports[filepath.Base(path[:len(path)-5])], &meta))
		}
		if fmt.Sprintf("%x", sha256.Sum256(raw)) != meta.SHA {
			t.Fatal("catalogue hash", path)
		}
		stored, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "cninfo", Dataset: "filing_catalogue", SourceLocator: path, FetchedAt: meta.FetchedAt, MediaType: "application/json", ParserVersion: cninfo.CatalogueParserVersion, Content: raw})
		check(err)
		page, err := cninfo.ParseCataloguePage(raw)
		check(err)
		for _, f := range page.Filings {
			if f.ProviderCode == "600519" || f.ProviderCode == "300866" {
				f.CatalogueArtifactID = stored.ArtifactID
				f.SourceURL, err = docClient.FilingDocumentURL(f.DocumentLocator)
				check(err)
				if report, ok := reports[f.SourceFilingID]; ok {
					var document struct {
						FetchedAt time.Time `json:"fetched_at"`
						SHA       string    `json:"sha256"`
					}
					check(json.Unmarshal(report, &document))
					pdf := readFinancialSample(t, filepath.Dir(path), f.SourceFilingID+".pdf")
					if fmt.Sprintf("%x", sha256.Sum256(pdf)) != document.SHA {
						t.Fatal("document hash", f.SourceFilingID)
					}
					archived, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "cninfo", Dataset: "filing_document", SourceLocator: f.SourceURL, FetchedAt: document.FetchedAt, MediaType: "application/pdf", ParserVersion: "pdf-raw-v1", Content: pdf})
					check(err)
					f.DocumentArtifactID, f.DocumentSHA256 = archived.ArtifactID, archived.SHA256
				}
				_, err = duckstore.UpsertFilings(ctx, db, run, []domain.FilingObservation{f})
				check(err)
			}
		}
	}
	var packages []struct {
		File      string
		SHA       string    `json:"sample_sha256"`
		FetchedAt time.Time `json:"fetched_at"`
	}
	check(json.Unmarshal(readFinancialSample(t, dir, "packages.json"), &packages))
	if len(packages) != 6 {
		t.Fatal("six periods required")
	}
	for _, p := range packages {
		raw := readFinancialSample(t, dir, p.File)
		if fmt.Sprintf("%x", sha256.Sum256(raw)) != p.SHA {
			t.Fatal("package hash", p.File)
		}
		stored, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "tdx", Dataset: "valuation_acceptance_sample", SourceLocator: "sample/" + p.File, FetchedAt: p.FetchedAt, MediaType: "application/zip", ParserVersion: "gpcw-v1", Content: raw})
		check(err)
		records, err := (&tdx.Client{}).NormalizeProfessionalFinancialPackage(financial.FileEntry{Filename: p.File}, raw, stored.ArtifactID)
		check(err)
		resolved, _, err := resolveProviderFinancialRecords(ctx, db, records)
		check(err)
		if len(resolved) != 2 {
			t.Fatal("sample identities")
		}
		_, err = duckstore.ReconcileProviderFinancialRecordsForArtifact(ctx, db, run, "tdx", stored.SHA256, resolved)
		check(err)
	}
	check(duckstore.FinishIngestRun(ctx, db, run, duckstore.IngestRunCompleted, nil, nil))
	result, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if result.Linked != 12 {
		rows, err := db.QueryContext(ctx, `SELECT provider_code, report_period, status, candidate_count FROM fundamental.provider_filing_link WHERE status<>'linked'`)
		check(err)
		for rows.Next() {
			var a, b, c, d string
			check(rows.Scan(&a, &b, &c, &d))
			t.Log(a, b, c, d)
		}
		rows.Close()
		t.Fatalf("links: %+v", result)
	}
	if result.Inserted != 699 {
		t.Fatalf("v24 baseline %+v", result)
	}
	// 构造旧物化元数据，确认升级会重标已有事实，而非只插入新字段。
	_, err = db.ExecContext(ctx, `UPDATE fundamental.fact SET materializer_version='pit-fundamental-v4', normalization_rule='tdx-float32-decimal-v2'`)
	check(err)
	check(duckstore.Apply(ctx, db))
	upgraded, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if upgraded.Inserted != 111 || upgraded.Updated != 699 || upgraded.Removed != 0 {
		t.Fatalf("v24 to v25 replay %+v", upgraded)
	}
	check(db.Close())
	db, err = duckstore.OpenAndMigrate(ctx, dbPath)
	check(err)
	defer db.Close()
	pinUTC()
	evidence, err := csv.NewReader(bytes.NewReader(readFinancialSample(t, "testdata/earnings-working-capital-2026", "values.csv"))).ReadAll()
	check(err)
	if len(evidence) != 39 {
		t.Fatal("new field evidence count")
	}
	cashEvidence, err := csv.NewReader(bytes.NewReader(readFinancialSample(t, "testdata/cash-rd-2026", "values.csv"))).ReadAll()
	check(err)
	if len(cashEvidence) != 34 {
		t.Fatal("cash/RD evidence count")
	}
	evidence = append(evidence, cashEvidence[1:]...)
	balanceEvidence, err := csv.NewReader(bytes.NewReader(readFinancialSample(t, "testdata/balance-profit-2026", "values.csv"))).ReadAll()
	check(err)
	if len(balanceEvidence) != 42 {
		t.Fatal("balance/profit evidence count")
	}
	evidence = append(evidence, balanceEvidence[1:]...)
	financialEvidence, err := csv.NewReader(bytes.NewReader(readFinancialSample(t, "testdata/financial-instruments-2026", "values.csv"))).ReadAll()
	check(err)
	if len(financialEvidence) != 39 {
		t.Fatal("financial instrument evidence count")
	}
	evidence = append(evidence, financialEvidence[1:]...)
	for _, row := range evidence[1:] {
		encoded, multiplier := row[5], float64(1)
		if len(row) == 11 {
			encoded = row[10]
			multiplier, err = strconv.ParseFloat(row[9], 64)
			check(err)
		}
		want, err := strconv.ParseFloat(encoded, 32)
		check(err)
		want *= multiplier
		var value float64
		var unit, periodType string
		check(db.QueryRowContext(ctx, `SELECT cast(value AS DOUBLE),unit,period_type FROM fundamental.fact_asof('2026-09-06') WHERE provider_code=? AND source_provider_field=? AND report_period=cast(? AS DATE)`, row[0], row[1], row[2]).Scan(&value, &unit, &periodType))
		period := "instant"
		if row[4] == "ytd" {
			period = "H1"
			if row[2] == "2025-12-31" {
				period = "FY"
			}
		}
		// DECIMAL(38,10) 存储允许末位舍入；原始 float32 位由独立证据测试精确比较。
		if math.Abs(value-want) > 1e-7 || unit != "CNY" || periodType != period {
			t.Fatalf("new standard fact %v: %v %s %s", row, value, unit, periodType)
		}
	}
	export := func(name, query string) {
		t.Helper()
		rows, err := db.QueryContext(ctx, query)
		check(err)
		defer rows.Close()
		columns, err := rows.Columns()
		check(err)
		var out bytes.Buffer
		w := csv.NewWriter(&out)
		check(w.Write(columns))
		for rows.Next() {
			values := make([]string, len(columns))
			dest := make([]any, len(columns))
			for i := range values {
				dest[i] = &values[i]
			}
			check(rows.Scan(dest...))
			check(w.Write(values))
		}
		check(rows.Err())
		w.Flush()
		check(w.Error())
		path := filepath.Join(dir, name+".csv")
		if os.Getenv("ALPHALAKE_WRITE_VALUATION_CHAIN") == "1" {
			check(os.WriteFile(path, out.Bytes(), 0644))
		} else if !bytes.Equal(readFinancialSample(t, dir, name+".csv"), out.Bytes()) {
			t.Fatalf("%s differs from production query", name)
		}
	}
	var exactMoney string
	check(db.QueryRowContext(ctx, `SELECT cast(value AS VARCHAR) FROM fundamental.fact WHERE provider_code='600519' AND source_provider_field='FN403' AND report_period=DATE '2025-06-30'`).Scan(&exactMoney))
	if exactMoney != "126350830000.0000000000" {
		t.Fatal("wide decimal conversion", exactMoney)
	}
	export("filings", `SELECT provider_code AS code,source_filing_id AS announcement_id,cast(announcement_time AS VARCHAR) AS available_at,announcement_time_precision,coalesce(cast(report_period AS VARCHAR),'') AS report_period,title,coalesce(source_url,'') AS document_locator,coalesce(sha256,'') AS pdf_sha256 FROM fundamental.filing WHERE source='cninfo' AND provider_code IN ('300866','600519') ORDER BY provider_code,source_filing_id`)
	export("facts", `SELECT f.provider_code AS code, cast(f.report_period AS VARCHAR) AS period, f.source_provider_field AS field, f.canonical_field, CASE WHEN f.canonical_field='total_shares' THEN CASE month(f.report_period) WHEN 3 THEN 'Q1' WHEN 6 THEN 'H1' WHEN 9 THEN 'Q3' ELSE 'FY' END ELSE f.period_type END AS period_type, f.statement_scope, f.unit, cast(f.value AS VARCHAR) AS value, cast(p.value_float32_bits AS VARCHAR) AS bits, cast(m.value_multiplier AS VARCHAR) AS multiplier, f.revision_key AS artifact_sha256, fi.source_filing_id AS announcement_id, cast(f.announcement_time AS VARCHAR) AS available_at
 FROM fundamental.fact_asof('2026-09-06') f JOIN fundamental.provider_fact p ON p.provider_fact_id=f.provider_fact_id JOIN fundamental.provider_field m ON m.source=f.primary_source AND m.provider_field=f.source_provider_field JOIN fundamental.filing fi ON fi.filing_id=f.source_filing_id ORDER BY code,period,field`)
	// 原冻结CSV保留源字段标识；仅测试投影回旧格式，不作为应用消费契约。
	export("windows", `SELECT w.provider_code AS code, cast(w.report_period AS VARCHAR) AS period, m.provider_field AS field, w.canonical_field, calculation_basis, coverage_status, coalesce(cast(value AS VARCHAR),'') AS value, cast(required_inputs AS VARCHAR) AS required_inputs,cast(available_inputs AS VARCHAR) AS available_inputs, cast(input_periods AS VARCHAR) AS input_periods, cast(input_coefficients AS VARCHAR) AS coefficients FROM fundamental.ttm_asof('2026-09-06', DATE '2026-06-30') w JOIN fundamental.provider_field m ON m.source=w.primary_source AND m.canonical_field=w.canonical_field AND m.valid_from<=w.report_period AND (m.valid_to IS NULL OR m.valid_to>w.report_period) ORDER BY code,field`)
	replay, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if replay.Inserted != 0 || replay.Updated != 0 || replay.Removed != 0 {
		t.Fatalf("replay %+v", replay)
	}
	for _, code := range []string{"600519", "300866"} {
		var available time.Time
		check(db.QueryRowContext(ctx, `SELECT min(announcement_time) FROM fundamental.fact WHERE provider_code=? AND report_period=DATE '2026-06-30'`, code).Scan(&available))
		var n int
		check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact_asof(?) WHERE provider_code=? AND report_period=DATE '2026-06-30'`, available.Add(-time.Second), code).Scan(&n))
		if n != 0 {
			t.Fatal("future H1 visible")
		}
		check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact_asof(?) WHERE provider_code=? AND report_period=DATE '2026-06-30'`, available, code).Scan(&n))
		if n == 0 {
			t.Fatal("H1 missing at disclosure")
		}
	}
	var missingZeros int
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact WHERE (source_provider_field IN ('FN146','FN147','FN148') AND month(report_period) IN (3,9)) OR (provider_code='600519' AND source_provider_field='FN99')`).Scan(&missingZeros))
	if missingZeros != 0 {
		t.Fatal("ambiguous zeros became facts")
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=-1 WHERE source='tdx' AND provider_field IN ('FN99','FN104','FN146','FN147','FN148','FN304')`)
	check(err)
	rejectedCash, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if rejectedCash.Removed != 48 {
		t.Fatalf("cash/RD invalidation %+v", rejectedCash)
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=1 WHERE source='tdx' AND provider_field IN ('FN99','FN104','FN146','FN147','FN148','FN304')`)
	check(err)
	restoredCash, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if restoredCash.Inserted != 48 {
		t.Fatalf("cash/RD recovery %+v", restoredCash)
	}
	// 新批次缺失余额不得补零；累计利润与单季度字段分别保留。
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.fact WHERE provider_code='300866' AND (source_provider_field='FN19' OR (source_provider_field='FN28' AND report_period IN (DATE '2025-09-30',DATE '2026-03-31',DATE '2026-06-30')))`).Scan(&missingZeros))
	if missingZeros != 0 {
		t.Fatal("ambiguous balance zeros became facts")
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=-1 WHERE source='tdx' AND provider_field IN ('FN19','FN20','FN27','FN28','FN33','FN37','FN50','FN53','FN60','FN95','FN96','FN97')`)
	check(err)
	rejectedBalance, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if rejectedBalance.Removed != 135 {
		t.Fatalf("balance/profit invalidation %+v", rejectedBalance)
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=1 WHERE source='tdx' AND provider_field IN ('FN19','FN20','FN27','FN28','FN33','FN37','FN50','FN53','FN60','FN95','FN96','FN97')`)
	check(err)
	restoredBalance, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if restoredBalance.Inserted != 135 {
		t.Fatalf("balance/profit recovery %+v", restoredBalance)
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=-1 WHERE source='tdx' AND provider_field IN ('FN9','FN59','FN299','FN403','FN409','FN411','FN413','FN430','FN431','FN433','FN434','FN437','FN506','FN509','FN510','FN520','FN579')`)
	check(err)
	rejectedFinancial, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if rejectedFinancial.Removed != 111 {
		t.Fatalf("financial instrument invalidation %+v", rejectedFinancial)
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=CASE WHEN provider_field IN ('FN9','FN59','FN299') THEN 1 ELSE 10000 END WHERE source='tdx' AND provider_field IN ('FN9','FN59','FN299','FN403','FN409','FN411','FN413','FN430','FN431','FN433','FN434','FN437','FN506','FN509','FN510','FN520','FN579')`)
	check(err)
	restoredFinancial, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if restoredFinancial.Inserted != 111 {
		t.Fatalf("financial instrument recovery %+v", restoredFinancial)
	}
	// 修改映射依据后必须撤销标准值，不能回退 PDF；恢复后从源证据重建。
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=-1 WHERE provider_field IN ('FN12','FN13','FN46','FN47','FN82','FN83','FN301') AND source='tdx'`)
	check(err)
	rejectedNew, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if rejectedNew.Removed != 84 {
		t.Fatalf("new mappings invalidation %+v", rejectedNew)
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=1 WHERE provider_field IN ('FN12','FN13','FN46','FN47','FN82','FN83','FN301') AND source='tdx'`)
	check(err)
	recoveredNew, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if recoveredNew.Inserted != 84 {
		t.Fatalf("new mappings recovery %+v", recoveredNew)
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=-1 WHERE provider_field='FN8' AND source='tdx'`)
	check(err)
	invalid, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if invalid.Removed != 12 {
		t.Fatalf("invalid mapping %+v", invalid)
	}
	var n int
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.ttm_asof('2026-09-06',DATE '2026-06-30') WHERE source_provider_field='FN8' AND value IS NOT NULL`).Scan(&n))
	if n != 0 {
		t.Fatal("invalid cash still queryable")
	}
	_, err = db.ExecContext(ctx, `UPDATE fundamental.provider_field SET value_multiplier=1 WHERE provider_field='FN8' AND source='tdx'`)
	check(err)
	restored, err := MaterializeProviderFundamentals(ctx, db, "tdx")
	check(err)
	if restored.Inserted != 12 {
		t.Fatalf("restore %+v", restored)
	}

	// 新的生产导入/导出路径：独立附注不回写 TDX 事实。
	var supplements []duckstore.ReviewedSupplement
	check(json.Unmarshal(readFinancialSample(t, "testdata/valuation-integration-2026", "supplements.json"), &supplements))
	var debtSupplements []duckstore.ReviewedSupplement
	check(json.Unmarshal(readFinancialSample(t, "testdata/wacc-debt-2026", "supplements.json"), &debtSupplements))
	if len(debtSupplements) != 24 {
		t.Fatal("incomplete debt maturity table")
	}
	supplements = append(supplements, debtSupplements...)
	var assetSupplements []duckstore.ReviewedSupplement
	check(json.Unmarshal(readFinancialSample(t, "testdata/reviewed-assets-2026", "supplements.json"), &assetSupplements))
	supplements = append(supplements, assetSupplements...)
	n, err = duckstore.ImportReviewedSupplements(ctx, db, supplements)
	check(err)
	if n != len(supplements) {
		t.Fatalf("supplements inserted %d", n)
	}
	n, err = duckstore.ImportReviewedSupplements(ctx, db, supplements)
	check(err)
	if n != 0 {
		t.Fatal("supplement replay not idempotent")
	}
	bad := append([]duckstore.ReviewedSupplement(nil), supplements...)
	bad[0].Value = "1"
	if _, err = duckstore.ImportReviewedSupplements(ctx, db, bad); err == nil {
		t.Fatal("conflicting supplement accepted")
	}

	// 第二条失败时第一条新记录也不得发布。
	atomicBad := append([]duckstore.ReviewedSupplement(nil), supplements[:2]...)
	atomicBad[0].Item = "uncommitted_probe"
	atomicBad[1].PDFSHA256 = strings.Repeat("0", 64)
	if _, err = duckstore.ImportReviewedSupplements(ctx, db, atomicBad); err == nil {
		t.Fatal("bad PDF accepted")
	}
	check(db.QueryRowContext(ctx, `SELECT count(*) FROM fundamental.reviewed_supplement`).Scan(&n))
	if n != len(supplements) {
		t.Fatal("partial supplement batch committed")
	}
	for _, at := range []time.Time{time.Date(2026, 8, 15, 15, 59, 59, 0, time.UTC), time.Date(2026, 8, 15, 16, 0, 0, 0, time.UTC)} {
		snapshot, err := duckstore.ExportValuationData(ctx, db, "600519", time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC), at)
		check(err)
		var records []struct {
			Period string `json:"period"`
			Item   string `json:"item"`
		}
		check(json.Unmarshal(snapshot["supplements"].(json.RawMessage), &records))
		found := false
		for _, row := range records {
			if row.Period == "2026-06-30" && row.Item == "finance_equity" {
				found = true
			}
		}
		if found != (at.Hour() == 16) {
			t.Fatal("independent finance disclosure boundary")
		}
	}
	check(db.Close())
	db, err = duckstore.Open(ctx, dbPath)
	check(err)
	defer db.Close()
	for _, code := range []string{"300866", "600519", "999999"} {
		snapshot, err := duckstore.ExportValuationData(ctx, db, code, time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC), time.Date(2026, 9, 6, 0, 0, 0, 0, time.UTC))
		check(err)
		payload, err := json.MarshalIndent(snapshot, "", "  ")
		check(err)
		if output := os.Getenv("ALPHALAKE_VALUATION_EXPORT_DIR"); output != "" {
			check(os.MkdirAll(output, 0755))
			check(os.WriteFile(filepath.Join(output, code+".json"), payload, 0644))
		}
		var notes []any
		check(json.Unmarshal(snapshot["supplements"].(json.RawMessage), &notes))
		want := map[string]int{"300866": 29 + len(debtSupplements) + len(assetSupplements), "600519": 5, "999999": 0}[code]
		if len(notes) != want {
			t.Fatalf("%s exported supplements %d", code, len(notes))
		}
	}
	if output := os.Getenv("ALPHALAKE_VALUATION_EXPORT_DIR"); output != "" {
		check(db.Close())
		rawDB, err := os.ReadFile(dbPath)
		check(err)
		check(os.WriteFile(filepath.Join(output, "acceptance.duckdb"), rawDB, 0644))
	}
	t.Logf("production chain: %+v", result)
}
