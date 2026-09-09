package hkex

import (
	"context"
	"errors"
	"github.com/yinhm/alphalake/internal/source/reference"
	"regexp"
	"time"
)

const Source = "hkex"
const Dataset = "anker-unadjusted-close-v1"
const ParserVersion = "hkex-anker-close-v1"
const Script = "internal/source/hkex/parse.py"

func URL(day string) string {
	d, e := time.Parse("2006-01-02", day)
	if e != nil {
		return ""
	}
	return "https://www.hkex.com.hk/eng/stat/smstat/dayquot/d" + d.Format("060102") + "e.htm"
}

type Snapshot struct {
	reference.Header
	Symbol        string `json:"symbol"`
	Currency      string `json:"currency"`
	RawValue      string `json:"raw_value"`
	Value         string `json:"value"`
	SourceLocator string `json:"source_locator"`
}

func Parse(ctx context.Context, python, script, path string) (Snapshot, string, error) {
	var s Snapshot
	h, e := reference.Parse(ctx, python, script, path, nil, &s)
	if e != nil {
		return s, "", e
	}
	return s, h, Validate(s)
}
func Validate(s Snapshot) error {
	if e := s.Header.Validate("alphalake-hkex-anker-close-v1", ParserVersion); e != nil {
		return e
	}
	if !regexp.MustCompile(`^[0-9]+(\.[0-9]{1,6})?$`).MatchString(s.RawValue) || s.Symbol != "00668" || s.Currency != "HKD" || s.SourceLocator != "#quotations/668 ANKER/second-line/CLOSING" || s.Value <= "0.000000000000" {
		return errors.New("unreviewed/invalid HKEX quotation")
	}
	return reference.Decimal12(s.RawValue, s.Value, 1)
}
