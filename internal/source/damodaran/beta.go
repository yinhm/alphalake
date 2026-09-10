package damodaran

import (
	"context"
	_ "embed"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/yinhm/alphalake/internal/source/reference"
)

const BetaDataset = "global-industry-beta-2026-v1"
const BetaURL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betaGlobal.xls"
const BetaParserVersion = "damodaran-global-beta-v1"
const BetaScript = "valuation/backend/data_sources/damodaran_parsers/beta_parser.py"
const BetaTaxonomy = "damodaran_industry_2026"

//go:embed industries-global-2026.txt
var betaIndustries string

type IndustryObservation struct {
	Industry      string `json:"industry"`
	SampleCount   int    `json:"sample_count"`
	MetricCode    string `json:"metric_code"`
	SourceLocator string `json:"source_locator"`
	RawValue      string `json:"raw_value"`
	Value         string `json:"value"`
}
type BetaSnapshot struct {
	reference.Header
	UnleveringTaxRate string                `json:"unlevering_tax_rate"`
	Observations      []IndustryObservation `json:"observations"`
}

func ParseBeta(ctx context.Context, python, script, path string) (BetaSnapshot, string, error) {
	var s BetaSnapshot
	hash, err := reference.Parse(ctx, python, script, path, []string{"xls_utils.py"}, &s)
	if err != nil {
		return s, "", err
	}
	return s, hash, ValidateBeta(s)
}
func ValidateBeta(s BetaSnapshot) error {
	if err := s.Header.Validate("alphalake-global-beta-v1", BetaParserVersion); err != nil {
		return err
	}
	if !strings.HasPrefix(s.ObservationDate, "2026-") || len(s.Observations) != 376 {
		return errors.New("unsupported/incomplete beta scope")
	}
	if err := reference.Decimal12(s.UnleveringTaxRate, s.UnleveringTaxRate, 1); err != nil {
		return err
	}
	tax, err := strconv.ParseFloat(s.UnleveringTaxRate, 64)
	if err != nil || tax < 0 || tax > 1 {
		return errors.New("invalid unlevering tax")
	}
	names := map[string]bool{}
	for _, name := range strings.Split(strings.TrimSpace(betaIndustries), "\n") {
		names[name] = true
	}
	columns := map[string]string{"beta_unlevered": "F", "beta_unlevered_cash_adjusted": "H", "debt_equity_ratio": "D", "effective_tax_rate": "E"}
	seen := map[string]bool{}
	samples := map[string]int{}
	rows := map[string]string{}
	owners := map[string]string{}
	locator := regexp.MustCompile(`^Industry Averages!([DEFH])([0-9]+)$`)
	for _, o := range s.Observations {
		key := o.Industry + ":" + o.MetricCode
		match := locator.FindStringSubmatch(o.SourceLocator)
		if !names[o.Industry] || columns[o.MetricCode] == "" || seen[key] || o.SampleCount <= 0 || len(match) != 3 || match[1] != columns[o.MetricCode] {
			return fmt.Errorf("invalid beta identity: %s", key)
		}
		row, _ := strconv.Atoi(match[2])
		if row < 11 || row > 104 {
			return errors.New("beta row outside industry scope")
		}
		if previous := rows[o.Industry]; previous != "" && previous != match[2] {
			return errors.New("industry row mismatch")
		}
		if owner := owners[match[2]]; owner != "" && owner != o.Industry {
			return errors.New("duplicate industry row")
		}
		if n := samples[o.Industry]; n != 0 && n != o.SampleCount {
			return errors.New("inconsistent industry sample count")
		}
		if err := reference.Decimal12(o.RawValue, o.Value, 1); err != nil {
			return err
		}
		seen[key] = true
		samples[o.Industry] = o.SampleCount
		rows[o.Industry] = match[2]
		owners[match[2]] = o.Industry
	}
	return nil
}
func BetaMethod(s BetaSnapshot, o IndustryObservation) string {
	if strings.HasPrefix(o.MetricCode, "beta_") {
		return "provider_marginal_tax_" + s.UnleveringTaxRate
	}
	return "provider_reported"
}
