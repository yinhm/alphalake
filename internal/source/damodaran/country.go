// Package damodaran defines the reviewed, deliberately bounded country-risk feed.
package damodaran

import (
	"context"
	"errors"
	"fmt"
	"github.com/yinhm/alphalake/internal/source/reference"
	"math/big"
	"regexp"
	"strings"
	"time"
)

const Source = "damodaran"
const Dataset = "country-risk-cn-hk-us-rating-v1"
const URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctrypremJuly26.xlsx"
const ParserVersion = "damodaran-country-selected-v1"
const DefaultScript = "valuation/backend/data_sources/damodaran_parsers/country_risk_parser.py"

type Observation struct {
	SubjectKind   string `json:"subject_kind"`
	SubjectCode   string `json:"subject_code"`
	MetricCode    string `json:"metric_code"`
	SourceLocator string `json:"source_locator"`
	RawValue      string `json:"raw_value"`
	Value         string `json:"value"`
}
type Snapshot struct {
	Contract        string        `json:"contract"`
	ObservationDate string        `json:"observation_date"`
	WorkbookSHA256  string        `json:"workbook_sha256"`
	ParserVersion   string        `json:"parser_version"`
	Runtime         string        `json:"runtime"`
	Observations    []Observation `json:"observations"`
}

func Parse(ctx context.Context, python, script, workbook string) (Snapshot, string, error) {
	var out Snapshot

	hash, err := reference.Parse(ctx, python, script, workbook, nil, &out)
	if err != nil {
		return out, "", err
	}
	return out, hash, Validate(out)
}

// Validate locks the supported scope and canonical decimal representation.
func Validate(s Snapshot) error {
	if s.Contract != "alphalake-country-risk-v1" || s.ParserVersion != ParserVersion || s.Runtime == "" || len(s.Observations) != 10 {
		return errors.New("unsupported/incomplete country-risk snapshot")
	}
	date, err := time.Parse("2006-01-02", s.ObservationDate)
	if err != nil || date.After(time.Now().UTC()) {
		return errors.New("invalid/future observation date")
	}
	if !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(s.WorkbookSHA256) {
		return errors.New("invalid workbook hash")
	}
	seen := map[string]bool{}
	values := map[string]*big.Rat{}
	for _, o := range s.Observations {
		valid := o.SubjectKind == "market_group" && o.SubjectCode == "mature" && o.MetricCode == "mature_market_erp"
		if o.SubjectKind == "country" && (o.SubjectCode == "CN" || o.SubjectCode == "HK" || o.SubjectCode == "US") {
			valid = o.MetricCode == "sovereign_default_spread" || o.MetricCode == "total_equity_risk_premium" || o.MetricCode == "country_risk_premium"
		}
		key := o.SubjectCode + ":" + o.MetricCode
		if !valid || seen[key] || !regexp.MustCompile(`^ERPs by country![DEF][1-9][0-9]*$`).MatchString(o.SourceLocator) {
			return fmt.Errorf("unsupported/duplicate observation: %s", key)
		}
		seen[key] = true
		v, ok := new(big.Rat).SetString(o.Value)
		raw, rawOK := new(big.Rat).SetString(o.RawValue)
		if !ok || !rawOK || !regexp.MustCompile(`^[01]\.[0-9]{12}$`).MatchString(o.Value) || v.Sign() < 0 || v.Cmp(big.NewRat(1, 1)) > 0 || raw.Sign() < 0 || raw.Cmp(big.NewRat(1, 1)) > 0 {
			return fmt.Errorf("invalid decimal: %s", key)
		}
		delta := new(big.Rat).Sub(raw, v)
		delta.Abs(delta)
		if delta.Cmp(big.NewRat(1, 2000000000000)) > 0 {
			return fmt.Errorf("raw/standard precision mismatch: %s", key)
		}
		values[key] = v
	}
	for _, code := range []string{"CN", "HK"} {
		delta := new(big.Rat).Sub(values[code+":total_equity_risk_premium"], values[code+":country_risk_premium"])
		delta.Sub(delta, values["mature:mature_market_erp"])
		delta.Abs(delta)
		if delta.Cmp(big.NewRat(2, 1000000000000)) > 0 {
			return errors.New("ERP component mismatch")
		}
	}
	if strings.TrimSpace(s.Runtime) != s.Runtime {
		return errors.New("invalid parser runtime")
	}
	return nil
}

// Method keeps mature-market estimation distinct from rating-based country rows.
func Method(o Observation) string {
	if o.MetricCode == "mature_market_erp" {
		return "implied_mature"
	}
	return "rating"
}
