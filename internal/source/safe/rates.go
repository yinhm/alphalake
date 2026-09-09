// Package safe parses the reviewed SAFE HKD/CNY central parity column.
package safe

import (
	"context"
	"errors"
	"github.com/yinhm/alphalake/internal/source/reference"
	"regexp"
	"time"
)

const Source = "safe"
const Dataset = "hkd-cny-central-parity-v1"
const URL = "https://www.safe.gov.cn/AppStructured/hlw/RMBQuery.do"
const Script = "internal/source/safe/parse.py"
const ParserVersion = "safe-hkd-cny-v1"

type Rate struct {
	Date          string `json:"date"`
	RawValue      string `json:"raw_value"`
	Value         string `json:"value"`
	SourceLocator string `json:"source_locator"`
}
type Snapshot struct {
	reference.Header
	Observations []Rate `json:"observations"`
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
	if err := s.Header.Validate("alphalake-safe-hkd-cny-v1", ParserVersion); err != nil {
		return err
	}
	if len(s.Observations) == 0 || len(s.Observations) > 100 || s.Observations[0].Date != s.ObservationDate {
		return errors.New("invalid FX scope")
	}
	previous := "9999-12-31"
	for _, o := range s.Observations {
		if _, err := time.Parse("2006-01-02", o.Date); err != nil {
			return err
		}
		if o.Date >= previous || o.SourceLocator != "#InfoTable/date="+o.Date+"/港元" || !regexp.MustCompile(`^[0-9]+(\.[0-9]{1,8})?$`).MatchString(o.RawValue) || o.Value <= "0.000000000000" {
			return errors.New("invalid FX date/value/locator")
		}
		if err := reference.Decimal12(o.RawValue, o.Value, 100); err != nil {
			return err
		}
		previous = o.Date
	}
	return nil
}
