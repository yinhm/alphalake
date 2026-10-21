package macro

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"fmt"
	"math/big"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"time"
)

const ParserVersion = "official-macro-tables-v1"

//go:embed html.go types.go nbs.go pbc.go mof.go catalogue.go
var implementation embed.FS

func ParserHash() string {
	h := sha256.New()
	for _, name := range []string{"html.go", "types.go", "nbs.go", "pbc.go", "mof.go", "catalogue.go"} {
		b, _ := implementation.ReadFile(name)
		h.Write([]byte(name + "\n"))
		h.Write(b)
	}
	return hex.EncodeToString(h.Sum(nil))
}

type Series struct {
	Code               string `json:"series_code"`
	Name               string `json:"name_zh"`
	Source             string `json:"source"`
	Dataset            string `json:"dataset"`
	Geography          string `json:"geography"`
	Frequency          string `json:"frequency"`
	PeriodBasis        string `json:"period_basis"`
	Unit               string `json:"unit"`
	PriceBasis         string `json:"price_basis"`
	SeasonalAdjustment string `json:"seasonal_adjustment"`
	Definition         string `json:"definition"`
}
type Observation struct {
	SeriesCode  string  `json:"series_code"`
	PeriodStart string  `json:"period_start"`
	PeriodEnd   string  `json:"period_end"`
	RawValue    string  `json:"raw_value"`
	RawUnit     string  `json:"raw_unit"`
	Value       *string `json:"value"`
	Status      string  `json:"value_status"`
	Locator     string  `json:"source_locator"`
}
type Snapshot struct {
	Source               string        `json:"source"`
	Dataset              string        `json:"dataset"`
	URL                  string        `json:"url"`
	Title                string        `json:"title"`
	ObservationDate      string        `json:"observation_date"`
	PublishedAt          *time.Time    `json:"source_published_at"`
	PublicationPrecision string        `json:"publication_precision"`
	SHA256               string        `json:"sha256"`
	Runtime              string        `json:"runtime"`
	Series               []Series      `json:"series"`
	Observations         []Observation `json:"observations"`
	FuturePlaceholders   int           `json:"future_placeholders_skipped"`
}

type builder struct {
	s      Snapshot
	series map[string]Series
	rows   map[string]Observation
	err    error
}

func Parse(raw []byte, feed Feed, url string) (Snapshot, error) {
	if err := ValidateURL(feed.Source, url); err != nil {
		return Snapshot{}, err
	}
	d, err := document(raw, feed.Key != "financial_report" && feed.Key != "fiscal")
	if err != nil {
		return Snapshot{}, err
	}
	hash := sha256.Sum256(raw)
	b := builder{s: Snapshot{Source: feed.Source, Dataset: feed.Dataset(), URL: url, SHA256: hex.EncodeToString(hash[:]),
		Runtime: "go=" + runtime.Version() + ";reviewed-html-tables", PublicationPrecision: "unknown"}, series: map[string]Series{}, rows: map[string]Observation{}}
	if feed.Source == "nbs" {
		err = parseNBS(&b, d, feed.Key)
	} else if feed.Source == "mof" {
		err = parseFiscal(&b, d)
	} else {
		err = parsePBC(&b, d, feed.Key)
	}
	if err != nil {
		return Snapshot{}, err
	}
	if b.err != nil {
		return Snapshot{}, b.err
	}
	for _, o := range b.rows {
		if o.Value != nil && o.PeriodEnd > b.s.ObservationDate {
			b.s.ObservationDate = o.PeriodEnd
		}
	}
	for _, o := range b.rows {
		if o.PeriodEnd > b.s.ObservationDate && o.Value == nil {
			b.s.FuturePlaceholders++
			continue
		}
		b.s.Observations = append(b.s.Observations, o)
	}
	for _, s := range b.series {
		b.s.Series = append(b.s.Series, s)
	}
	sort.Slice(b.s.Series, func(i, j int) bool { return b.s.Series[i].Code < b.s.Series[j].Code })
	sort.Slice(b.s.Observations, func(i, j int) bool {
		a, c := b.s.Observations[i], b.s.Observations[j]
		if a.SeriesCode == c.SeriesCode {
			return a.PeriodEnd < c.PeriodEnd
		}
		return a.SeriesCode < c.SeriesCode
	})
	return b.s, Validate(b.s)
}

var numberPattern = regexp.MustCompile(`^-?[0-9]+(?:\.[0-9]+)?$`)
var codePattern = regexp.MustCompile(`^cn_[a-z0-9_]{2,100}$`)

func normalize(raw, unit string) (*string, string, error) {
	factor, out := "1", ""
	switch unit {
	case "percent":
		factor, out = "0.01", "fraction"
	case "diffusion_index_percent_scale":
		out = "index_points"
	case "percentage_points":
		out = "percentage_points"
	case "CNY_100_million":
		factor, out = "100000000", "CNY"
	case "CNY_trillion":
		factor, out = "1000000000000", "CNY"
	case "square_metre_10000":
		factor, out = "10000", "square_metre"
	case "CNY_per_person":
		out = "CNY_per_person"
	default:
		return nil, "", fmt.Errorf("unreviewed macro unit %q", unit)
	}
	if raw == "" || raw == "-" || raw == "—" || raw == "…" || raw == "..." {
		return nil, out, nil
	}
	if !numberPattern.MatchString(raw) || len(raw) > 40 {
		return nil, out, fmt.Errorf("invalid macro number %q", raw)
	}
	x, ok := new(big.Rat).SetString(raw)
	if !ok {
		return nil, out, fmt.Errorf("invalid macro decimal")
	}
	f, _ := new(big.Rat).SetString(factor)
	value := x.Mul(x, f).FloatString(12)
	if len(strings.Split(strings.TrimPrefix(value, "-"), ".")[0]) > 26 {
		return nil, out, fmt.Errorf("macro decimal overflow")
	}
	return &value, out, nil
}

func period(year, month int, basis string) (string, string) {
	end := time.Date(year, time.Month(month)+1, 0, 0, 0, 0, 0, time.UTC)
	start := time.Date(year, time.Month(month), 1, 0, 0, 0, 0, time.UTC)
	if basis == "ytd" {
		start = time.Date(year, 1, 1, 0, 0, 0, 0, time.UTC)
	}
	if basis == "quarter" {
		start = time.Date(year, time.Month(month)-2, 1, 0, 0, 0, 0, time.UTC)
	}
	if basis == "point_in_time" {
		start = end
	}
	if basis == "month_or_jan_feb" && month == 2 {
		start = time.Date(year, 1, 1, 0, 0, 0, 0, time.UTC)
	}
	return start.Format("2006-01-02"), end.Format("2006-01-02")
}

func (b *builder) add(code, name, basis, unit, prices, adjustment, definition string, year, month int, c Cell) {
	if b.err != nil {
		return
	}
	if year < 1990 || year > time.Now().Year() || month < 1 || month > 12 {
		b.err = fmt.Errorf("invalid macro period %d-%d", year, month)
		return
	}
	value, normalized, err := normalize(c.Text, unit)
	if err != nil {
		b.err = err
		return
	}
	frequency := "monthly"
	if basis == "quarter" || strings.HasPrefix(code, "gdp_") || strings.HasPrefix(code, "capacity_") || strings.HasPrefix(code, "household_") {
		frequency = "quarterly"
	}
	code = "cn_" + code
	s := Series{Code: code, Name: name, Source: b.s.Source, Dataset: b.s.Dataset, Geography: "CN", Frequency: frequency, PeriodBasis: basis,
		Unit: normalized, PriceBasis: prices, SeasonalAdjustment: adjustment, Definition: definition}
	if previous, ok := b.series[code]; ok && previous != s {
		b.err = fmt.Errorf("conflicting macro definition %s", code)
		return
	}
	b.series[code] = s
	start, end := period(year, month, basis)
	status := "reported"
	if value == nil {
		status = "not_reported"
	}
	o := Observation{code, start, end, c.Text, unit, value, status, c.Locator}
	key := code + "/" + end
	if prior, ok := b.rows[key]; ok {
		if prior.PeriodStart != start || prior.Status != status || (prior.Value != nil && *prior.Value != *value) || prior.RawUnit != unit {
			b.err = fmt.Errorf("conflicting macro table cells %s", key)
		}
		return
	}
	b.rows[key] = o
}

func Validate(s Snapshot) error {
	if s.Source != "nbs" && s.Source != "pbc" && s.Source != "mof" {
		return fmt.Errorf("unreviewed macro provider")
	}
	if err := ValidateURL(s.Source, s.URL); err != nil {
		return err
	}
	day, err := time.Parse("2006-01-02", s.ObservationDate)
	if err != nil || day.After(time.Now().UTC()) || len(s.Observations) == 0 || len(s.Series) == 0 || len(s.SHA256) != 64 || s.Runtime == "" {
		return fmt.Errorf("incomplete macro snapshot")
	}
	if s.PublicationPrecision == "timestamp" {
		if s.PublishedAt == nil || s.PublishedAt.After(time.Now().UTC()) {
			return fmt.Errorf("invalid macro publication timestamp")
		}
	} else if s.PublicationPrecision != "unknown" || s.PublishedAt != nil {
		return fmt.Errorf("invalid macro publication precision")
	}
	definitions := map[string]Series{}
	for _, v := range s.Series {
		if !codePattern.MatchString(v.Code) || v.Source != s.Source || v.Dataset != s.Dataset || v.Geography != "CN" || v.Name == "" || v.Definition == "" {
			return fmt.Errorf("invalid macro series identity")
		}
		if _, exists := definitions[v.Code]; exists {
			return fmt.Errorf("duplicate macro series")
		}
		definitions[v.Code] = v
	}
	seen := map[string]bool{}
	for _, o := range s.Observations {
		v, ok := definitions[o.SeriesCode]
		if !ok {
			return fmt.Errorf("undefined macro series")
		}
		start, e1 := time.Parse("2006-01-02", o.PeriodStart)
		end, e2 := time.Parse("2006-01-02", o.PeriodEnd)
		if e1 != nil || e2 != nil || start.After(end) || end.After(day) || o.Locator == "" {
			return fmt.Errorf("invalid macro observation period/locator")
		}
		a, e := period(end.Year(), int(end.Month()), v.PeriodBasis)
		if o.PeriodStart != a || o.PeriodEnd != e {
			return fmt.Errorf("macro period basis mismatch")
		}
		if v.Frequency == "quarterly" && int(end.Month())%3 != 0 {
			return fmt.Errorf("quarterly macro observation at a non-quarter end")
		}
		value, unit, err := normalize(o.RawValue, o.RawUnit)
		if err != nil || unit != v.Unit || (value == nil) != (o.Value == nil) || (value != nil && *value != *o.Value) || (value == nil && o.Status != "not_reported") || (value != nil && o.Status != "reported") {
			return fmt.Errorf("macro unit/value/status mismatch")
		}
		key := o.SeriesCode + "/" + o.PeriodEnd
		if seen[key] {
			return fmt.Errorf("duplicate macro observation")
		}
		seen[key] = true
		if strings.Contains(o.SeriesCode, "money_m1") && o.PeriodStart < "2025-01-01" {
			return fmt.Errorf("M1 2025 definition cannot be backfilled with the old scope")
		}
	}
	return nil
}
