package damodaran

import (
	"context"
	"errors"
	"fmt"
	"github.com/yinhm/alphalake/internal/source/reference"
	"strings"
)

const NativeReferenceScript = "valuation/backend/data_sources/damodaran_parsers/native_reference_parser.py"
const NativeReferenceVersion = "damodaran-native-reference-v2"

var NativeReferenceFiles = []string{"betas", "betaGlobal", "wacc", "waccGlobal", "margin", "marginGlobal", "taxrate", "taxrateGlobal", "capex", "capexGlobal", "fundgrEB", "fundgrEBGlobal", "EVA", "EVAGlobal", "vebitda", "vebitdaGlobal", "pedata", "peGlobal", "pbvdata", "pbvGlobal", "psdata", "psGlobal", "countrytaxrates"}

type NativeReferenceObservation struct {
	Subject       string  `json:"subject"`
	MetricCode    string  `json:"metric_code"`
	SourceLocator string  `json:"source_locator"`
	RawValue      string  `json:"raw_value"`
	RawUnit       string  `json:"raw_unit"`
	SampleCount   *int    `json:"sample_count"`
	Value         *string `json:"value"`
	ValueStatus   string  `json:"value_status"`
}
type NativeReferenceSnapshot struct {
	reference.Header
	UnleveringTaxRate string                       `json:"unlevering_tax_rate"`
	FileStem          string                       `json:"file_stem"`
	SampleRegion      string                       `json:"sample_region"`
	Observations      []NativeReferenceObservation `json:"observations"`
}

func NativeReferenceDataset(stem string) string { return "native-reference-" + stem + "-v1" }
func NativeReferenceURL(stem string) string {
	return "https://pages.stern.nyu.edu/~adamodar/pc/datasets/" + stem + ".xls"
}
func ParseNativeReference(ctx context.Context, python, script, path string) (NativeReferenceSnapshot, string, error) {
	var s NativeReferenceSnapshot
	hash, err := reference.Parse(ctx, python, script, path, []string{"xls_utils.py"}, &s)
	if err != nil {
		return s, "", err
	}
	return s, hash, ValidateNativeReference(s)
}
func ValidateNativeReference(s NativeReferenceSnapshot) error {
	if err := s.Header.Validate("alphalake-native-reference-source-v1", NativeReferenceVersion); err != nil {
		return err
	}
	supported := false
	for _, stem := range NativeReferenceFiles {
		if s.FileStem == stem {
			supported = true
		}
	}
	if !supported {
		return errors.New("unsupported native reference file")
	}
	if strings.HasPrefix(s.FileStem, "beta") {
		if err := reference.Decimal12(s.UnleveringTaxRate, s.UnleveringTaxRate, 1); err != nil {
			return err
		}
	} else if s.UnleveringTaxRate != "" {
		return errors.New("unexpected beta tax")
	}
	tax := s.FileStem == "countrytaxrates"
	expected := "us"
	if strings.HasSuffix(s.FileStem, "Global") {
		expected = "global"
	}
	if tax {
		expected = ""
	}
	if s.SampleRegion != expected {
		return errors.New("native reference region mismatch")
	}
	seen := map[string]bool{}
	subjects := map[string]int{}
	for _, o := range s.Observations {
		key := o.Subject + ":" + o.MetricCode
		if seen[key] || o.Subject == "" || !strings.Contains(o.SourceLocator, "!") || (o.RawUnit != "fraction" && o.RawUnit != "dimensionless") {
			return fmt.Errorf("invalid reference identity: %s", key)
		}
		seen[key] = true
		subjects[o.Subject]++
		if tax {
			if o.MetricCode != "corporate_marginal_tax_rate" || (o.Subject != "CN" && o.Subject != "HK" && o.Subject != "US") || o.SampleCount != nil {
				return errors.New("invalid tax identity")
			}
		} else if o.SampleCount == nil || *o.SampleCount < 0 {
			return errors.New("invalid sample count")
		}
		if o.ValueStatus == "reported" && o.Value != nil {
			if err := reference.Decimal12(o.RawValue, *o.Value, 1); err != nil {
				return err
			}
		} else if (o.ValueStatus != "missing" && !(tax && o.ValueStatus == "ambiguous")) || o.Value != nil {
			return errors.New("invalid reference value status")
		}
	}
	count := 94
	if tax {
		count = 3
	}
	if len(subjects) != count {
		return errors.New("incomplete reference subjects")
	}
	n := 0
	for _, v := range subjects {
		if n != 0 && n != v {
			return errors.New("incomplete reference metrics")
		}
		n = v
	}
	return nil
}
