package chinabond

import (
	"context"
	"errors"
	"fmt"
	"regexp"

	"github.com/yinhm/alphalake/internal/source/reference"
)

const Source = "chinabond"
const Dataset = "cny-government-eight-tenors-v1"
const URL = "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/more?locale=cn_ZH"
const ParserVersion = "chinabond-government-eight-v1"
const Script = "internal/source/chinabond/parse.py"
const CurveCode = "chinabond_government_pbc"

type Point struct {
	TenorMonths   int    `json:"tenor_months"`
	SourceLocator string `json:"source_locator"`
	RawValue      string `json:"raw_value"`
	Value         string `json:"value"`
}
type Snapshot struct {
	reference.Header
	Observations []Point `json:"observations"`
}

func Parse(ctx context.Context, python, script, path string) (Snapshot, string, error) {
	var s Snapshot
	hash, err := reference.Parse(ctx, python, script, path, nil, &s)
	if err != nil {
		return s, "", err
	}
	return s, hash, Validate(s)
}
func Validate(s Snapshot) error {
	if err := s.Header.Validate("alphalake-cny-government-yield-v1", ParserVersion); err != nil {
		return err
	}
	tenors := []int{3, 6, 12, 36, 60, 84, 120, 360}
	if len(s.Observations) != len(tenors) {
		return errors.New("incomplete government curve")
	}
	for i, o := range s.Observations {
		if o.TenorMonths != tenors[i] || o.SourceLocator != fmt.Sprintf("#gjqxData/tr[2]/td[%d]", i+2) || !regexp.MustCompile(`^-?[0-9]+\.[0-9]{4}$`).MatchString(o.RawValue) {
			return errors.New("unsupported curve point")
		}
		if err := reference.Decimal12(o.RawValue, o.Value, 100); err != nil {
			return err
		}
	}
	return nil
}
