package damodaran

import (
	"context"
	"errors"
	"fmt"
	"github.com/yinhm/alphalake/internal/source/reference"
	"math/big"
)

const CreditDataset = "synthetic-credit-large-2026-v1"
const CreditURL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"
const CreditScript = "internal/source/damodaran/ratings.py"
const CreditParserVersion = "damodaran-ratings-large-2026-v1"

type CreditBand struct {
	Lower         string `json:"lower"`
	Upper         string `json:"upper"`
	Rating        string `json:"rating"`
	RawValue      string `json:"raw_value"`
	Value         string `json:"value"`
	SourceLocator string `json:"source_locator"`
}
type CreditSnapshot struct {
	reference.Header
	Observations []CreditBand `json:"observations"`
}

func ParseCredit(ctx context.Context, python, script, path string) (CreditSnapshot, string, error) {
	var s CreditSnapshot
	hash, err := reference.Parse(ctx, python, script, path, nil, &s)
	if err != nil {
		return s, "", err
	}
	return s, hash, ValidateCredit(s)
}
func ValidateCredit(s CreditSnapshot) error {
	if err := s.Header.Validate("alphalake-credit-spreads-v1", CreditParserVersion); err != nil {
		return err
	}
	ratings := []string{"D2/D", "C2/C", "Ca2/CC", "Caa/CCC", "B3/B-", "B2/B", "B1/B+", "Ba2/BB", "Ba1/BB+", "Baa2/BBB", "A3/A-", "A2/A", "A1/A+", "Aa2/AA", "Aaa/AAA"}
	lower := []string{"-100000", "0.2", "0.65", "0.8", "1.25", "1.5", "1.75", "2", "2.25", "2.5", "3", "4.25", "5.5", "6.5", "8.50"}
	upper := []string{"0.199999", "0.649999", "0.799999", "1.249999", "1.499999", "1.749999", "1.999999", "2.2499999", "2.49999", "2.999999", "4.249999", "5.499999", "6.499999", "8.499999", "100000"}
	if s.ObservationDate != "2026-01-01" || len(s.Observations) != 15 {
		return errors.New("unsupported credit scope/month")
	}
	for i, o := range s.Observations {
		if o.Rating != ratings[i] || o.Lower != lower[i] || o.Upper != upper[i] || o.SourceLocator != fmt.Sprintf("table[1]/tr[%d]/td[1:4]", i+5) {
			return errors.New("unsupported credit band")
		}
		if err := reference.Decimal12(o.RawValue, o.Value, 100); err != nil {
			return err
		}
		v, ok := new(big.Rat).SetString(o.Value)
		if !ok || v.Sign() < 0 || v.Cmp(big.NewRat(1, 1)) >= 0 {
			return errors.New("invalid credit spread")
		}
	}
	return nil
}
