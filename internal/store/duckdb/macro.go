package duckdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/source/macro"
)

var macroSeriesColumns = []string{"series_code", "name_zh", "source", "dataset", "geography", "frequency", "period_basis", "unit", "price_basis", "seasonal_adjustment", "definition"}
var macroObservationColumns = []string{"series_code", "period_start", "period_end", "value", "value_status", "raw_value", "raw_unit", "source_locator"}

// Project a bounded, typed batch once in DuckDB instead of executing one INSERT per point.
func macroSeriesInput() string {
	cols := []string{}
	for _, name := range macroSeriesColumns {
		cols = append(cols, "j.value->>'$."+name+"' AS "+name)
	}
	return "SELECT " + strings.Join(cols, ",") + " FROM json_each(CAST(? AS JSON)) j"
}

const macroObservationInput = `SELECT j.value->>'$.series_code' AS series_code,
 CAST(j.value->>'$.period_start' AS DATE) AS period_start,
 CAST(j.value->>'$.period_end' AS DATE) AS period_end,
 CAST(j.value->>'$.value' AS DECIMAL(38,12)) AS value,
 j.value->>'$.value_status' AS value_status,
 j.value->>'$.raw_value' AS raw_value,
 j.value->>'$.raw_unit' AS raw_unit,
 j.value->>'$.source_locator' AS source_locator FROM json_each(CAST(? AS JSON)) j`

func PublishMacroData(ctx context.Context, db *sql.DB, runID, artifactID int64, s macro.Snapshot) (int64, bool, error) {
	if err := macro.Validate(s); err != nil {
		return 0, false, err
	}
	p, err := beginReferencePublication(ctx, db, runID, artifactID, referenceInput{Source: s.Source, Dataset: s.Dataset, URL: s.URL, Date: s.ObservationDate, SHA: s.SHA256,
		ParserVersion: macro.ParserVersion, Runtime: s.Runtime, Normalization: "macro-standard-units-v1", ParserHash: macro.ParserHash(), PublishedAt: s.PublishedAt})
	if err != nil {
		return 0, false, err
	}
	defer p.tx.Rollback()
	series, _ := json.Marshal(s.Series)
	observations, _ := json.Marshal(s.Observations)
	predicates := []string{}
	for _, column := range macroSeriesColumns[1:] {
		predicates = append(predicates, "old."+column+" IS DISTINCT FROM incoming."+column)
	}
	var conflicts int
	if err = p.tx.QueryRowContext(ctx, "WITH incoming AS ("+macroSeriesInput()+") SELECT count(*) FROM macro.series old JOIN incoming USING(series_code) WHERE "+strings.Join(predicates, " OR "), string(series)).Scan(&conflicts); err != nil {
		return 0, false, err
	}
	if conflicts > 0 {
		return 0, false, fmt.Errorf("macro series definitions changed; use an explicit reviewed rebuild or a distinct semantic series")
	}
	if _, err = p.tx.ExecContext(ctx, "INSERT INTO macro.series "+macroSeriesInput()+" WHERE NOT EXISTS(SELECT 1 FROM macro.series s WHERE s.series_code=(j.value->>'$.series_code'))", string(series)); err != nil {
		return 0, false, err
	}
	columns := strings.Join(macroObservationColumns, ",")
	if p.existing {
		query := `WITH incoming AS (` + macroObservationInput + `), saved AS (SELECT ` + columns + ` FROM macro.observation WHERE release_id=?)
 SELECT count(*) FROM ((SELECT * FROM incoming EXCEPT ALL SELECT * FROM saved) UNION ALL (SELECT * FROM saved EXCEPT ALL SELECT * FROM incoming))`
		if err = p.tx.QueryRowContext(ctx, query, string(observations), p.id).Scan(&conflicts); err != nil {
			return 0, false, err
		}
		if conflicts > 0 {
			return 0, false, fmt.Errorf("published macro interpretation/scope changed")
		}
	} else {
		if _, err = p.tx.ExecContext(ctx, "INSERT INTO macro.observation(release_id,"+columns+") SELECT ?,incoming.* FROM ("+macroObservationInput+") incoming", p.id, string(observations)); err != nil {
			return 0, false, err
		}
	}
	return p.finish(ctx)
}

type MacroEvidence struct {
	ReleaseID            int64      `json:"release_id"`
	ArtifactID           int64      `json:"artifact_id"`
	URL                  string     `json:"url"`
	SHA256               string     `json:"sha256"`
	RawValue             string     `json:"raw_value"`
	RawUnit              string     `json:"raw_unit"`
	Locator              string     `json:"source_locator"`
	PublishedAt          *time.Time `json:"source_published_at"`
	PublicationPrecision string     `json:"publication_precision"`
	FirstAcquiredAt      time.Time  `json:"first_acquired_at"`
	RecordedAt           time.Time  `json:"recorded_at"`
}
type MacroObservation struct {
	SeriesCode         string        `json:"series_code"`
	Name               string        `json:"name_zh"`
	Unit               string        `json:"unit"`
	PeriodBasis        string        `json:"period_basis"`
	PriceBasis         string        `json:"price_basis"`
	SeasonalAdjustment string        `json:"seasonal_adjustment"`
	PeriodStart        string        `json:"period_start"`
	PeriodEnd          string        `json:"period_end"`
	Value              *string       `json:"value"`
	Status             string        `json:"value_status"`
	Evidence           MacroEvidence `json:"source_evidence"`
}

func QueryMacroData(ctx context.Context, db interface {
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
}, series []string, from, through string, latest bool) ([]MacroObservation, error) {
	conditions := []string{"TRUE"}
	args := []any{}
	for _, bound := range []struct{ value, predicate string }{{from, "o.period_end>=CAST(? AS DATE)"}, {through, "o.period_end<=CAST(? AS DATE)"}} {
		if bound.value != "" {
			if _, err := time.Parse("2006-01-02", bound.value); err != nil {
				return nil, err
			}
			conditions = append(conditions, bound.predicate)
			args = append(args, bound.value)
		}
	}
	if from != "" && through != "" && from > through {
		return nil, fmt.Errorf("macro period range reversed")
	}
	if len(series) > 0 {
		marks := []string{}
		seen := map[string]bool{}
		for _, code := range series {
			if seen[code] {
				return nil, fmt.Errorf("duplicate macro series")
			}
			seen[code] = true
			marks = append(marks, "?")
			args = append(args, code)
		}
		conditions = append(conditions, "o.series_code IN ("+strings.Join(marks, ",")+")")
	}
	query := `WITH current AS (SELECT o.*,r.source_published_at,r.publication_precision,r.first_seen_at,r.recorded_at,a.artifact_id,a.source_locator AS source_url,a.sha256
 FROM macro.observation o JOIN meta.dataset_release r USING(release_id)
 JOIN meta.dataset_release_artifact link ON link.release_id=r.release_id AND link.role='data'
 JOIN meta.artifact a ON a.artifact_id=link.artifact_id WHERE ` + strings.Join(conditions, " AND ") + `
 QUALIFY row_number() OVER(PARTITION BY o.series_code,o.period_end ORDER BY coalesce(r.source_published_at,CAST(r.source_version AS TIMESTAMPTZ)) DESC,r.recorded_at DESC,r.release_id DESC)=1)
 SELECT c.series_code,s.name_zh,s.unit,s.period_basis,s.price_basis,s.seasonal_adjustment,CAST(c.period_start AS VARCHAR),CAST(c.period_end AS VARCHAR),CAST(c.value AS VARCHAR),c.value_status,
 c.release_id,c.artifact_id,c.source_url,c.sha256,c.raw_value,c.raw_unit,c.source_locator,c.source_published_at,c.publication_precision,c.first_seen_at,c.recorded_at
 FROM current c JOIN macro.series s USING(series_code)`
	if latest {
		query += ` QUALIFY row_number() OVER(PARTITION BY c.series_code ORDER BY c.period_end DESC)=1`
	}
	query += " ORDER BY c.series_code,c.period_end"
	rows, err := db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []MacroObservation{}
	for rows.Next() {
		var o MacroObservation
		var value sql.NullString
		var published sql.NullTime
		e := &o.Evidence
		if err = rows.Scan(&o.SeriesCode, &o.Name, &o.Unit, &o.PeriodBasis, &o.PriceBasis, &o.SeasonalAdjustment, &o.PeriodStart, &o.PeriodEnd, &value, &o.Status, &e.ReleaseID, &e.ArtifactID, &e.URL, &e.SHA256, &e.RawValue, &e.RawUnit, &e.Locator, &published, &e.PublicationPrecision, &e.FirstAcquiredAt, &e.RecordedAt); err != nil {
			return nil, err
		}
		if value.Valid {
			v := value.String
			o.Value = &v
		}
		if published.Valid {
			v := published.Time
			e.PublishedAt = &v
		}
		out = append(out, o)
	}
	return out, rows.Err()
}

type MacroSeriesCoverage struct {
	macro.Series
	Observations int     `json:"observations"`
	LatestPeriod *string `json:"latest_period"`
}

func ListMacroSeries(ctx context.Context, db interface {
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
}) ([]MacroSeriesCoverage, error) {
	rows, err := db.QueryContext(ctx, `SELECT s.*,count(DISTINCT o.period_end),CAST(max(o.period_end) AS VARCHAR) FROM macro.series s LEFT JOIN macro.observation o USING(series_code) GROUP BY ALL ORDER BY s.series_code`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []MacroSeriesCoverage{}
	for rows.Next() {
		var r MacroSeriesCoverage
		var latest sql.NullString
		if err = rows.Scan(&r.Code, &r.Name, &r.Source, &r.Dataset, &r.Geography, &r.Frequency, &r.PeriodBasis, &r.Unit, &r.PriceBasis, &r.SeasonalAdjustment, &r.Definition, &r.Observations, &latest); err != nil {
			return nil, err
		}
		if latest.Valid {
			v := latest.String
			r.LatestPeriod = &v
		}
		out = append(out, r)
	}
	return out, rows.Err()
}
