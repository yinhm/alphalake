package damodaran

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/yinhm/alphalake/internal/source/reference"
)

const CapitalDataset = "global-industry-capital-2026-v1"
const CapitalURL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/capexGlobal.xls"
const CapitalParserVersion = "damodaran-global-capital-v1"
const CapitalScript = "valuation/backend/data_sources/damodaran_parsers/capex_parser.py"
const CapitalMetric = "sales_to_invested_capital_ltm"
const CapitalMethod = "source_sales_to_invested_capital_ltm"

type CapitalSnapshot struct {
	reference.Header
	Observations []IndustryObservation `json:"observations"`
}

func ParseCapital(ctx context.Context, python, script, path string) (CapitalSnapshot, string, error) {
	var s CapitalSnapshot
	hash, err := reference.Parse(ctx, python, script, path, []string{"xls_utils.py"}, &s)
	if err != nil {
		return s, "", err
	}
	return s, hash, ValidateCapital(s)
}

func ValidateCapital(s CapitalSnapshot) error {
	if err := s.Header.Validate("alphalake-global-capital-v1", CapitalParserVersion); err != nil {
		return err
	}
	if !strings.HasPrefix(s.ObservationDate, "2026-") || len(s.Observations) != 94 {
		return errors.New("unsupported/incomplete capital scope")
	}
	names := map[string]bool{}
	for _, name := range strings.Split(strings.TrimSpace(betaIndustries), "\n") {
		names[name] = true
	}
	seen, rows := map[string]bool{}, map[int]bool{}
	locator := regexp.MustCompile(`^Industry Averages!J([0-9]+)$`)
	for _, o := range s.Observations {
		match := locator.FindStringSubmatch(o.SourceLocator)
		if !names[o.Industry] || seen[o.Industry] || o.MetricCode != CapitalMetric || o.SampleCount <= 0 || len(match) != 2 {
			return fmt.Errorf("invalid capital identity: %s", o.Industry)
		}
		row, _ := strconv.Atoi(match[1])
		if row < 9 || row > 102 || rows[row] {
			return errors.New("invalid capital row")
		}
		if err := reference.Decimal12(o.RawValue, o.Value, 1); err != nil {
			return err
		}
		v, err := strconv.ParseFloat(o.Value, 64)
		if err != nil || v <= 0 {
			return errors.New("invalid capital ratio")
		}
		seen[o.Industry], rows[row] = true, true
	}
	return nil
}
