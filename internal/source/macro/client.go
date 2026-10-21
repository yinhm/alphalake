package macro

import (
	"context"
	"fmt"
	"html"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

type Page struct {
	URL       string
	Raw       []byte
	FetchedAt time.Time
}
type Discovery struct {
	URLs     map[string]string
	Pages    []Page
	Failures map[string]string
}
type Client struct{ HTTP *http.Client }

func NewClient() *Client {
	return &Client{HTTP: &http.Client{Timeout: 30 * time.Second, Transport: retryTransport{base: http.DefaultTransport}, CheckRedirect: func(req *http.Request, via []*http.Request) error {
		if len(via) == 0 || len(via) > 5 {
			return fmt.Errorf("unsupported macro redirect")
		}
		source := "pbc"
		if via[0].URL.Host == "www.stats.gov.cn" {
			source = "nbs"
		}
		if via[0].URL.Host == "gks.mof.gov.cn" {
			source = "mof"
		}
		return ValidateURL(source, req.URL.String())
	}}}
}

// One bounded retry for transient official-site errors; never retry a denial or malformed publication.
type retryTransport struct{ base http.RoundTripper }

func (t retryTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	for attempt := 0; attempt < 2; attempt++ {
		response, err := t.base.RoundTrip(req.Clone(req.Context()))
		transient := err != nil || response.StatusCode == 408 || response.StatusCode == 429 || response.StatusCode == 502 || response.StatusCode == 503 || response.StatusCode == 504
		if attempt == 1 || !transient || req.Method != http.MethodGet || req.Context().Err() != nil {
			return response, err
		}
		if response != nil {
			response.Body.Close()
		}
		select {
		case <-req.Context().Done():
			return nil, req.Context().Err()
		case <-time.After(250 * time.Millisecond):
		}
	}
	return nil, fmt.Errorf("macro HTTP retry exhausted")
}
func (c *Client) Fetch(ctx context.Context, source, address string) (Page, error) {
	if c == nil || c.HTTP == nil {
		return Page{}, fmt.Errorf("macro HTTP client missing")
	}
	if err := ValidateURL(source, address); err != nil {
		return Page{}, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, address, nil)
	if err != nil {
		return Page{}, err
	}
	req.Header.Set("User-Agent", "AlphaLake/1.0 (+https://github.com/yinhm/alphalake)")
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return Page{}, err
	}
	defer resp.Body.Close()
	if err = ValidateURL(source, resp.Request.URL.String()); err != nil {
		return Page{}, err
	}
	if resp.StatusCode != http.StatusOK {
		return Page{}, fmt.Errorf("official macro HTTP %d", resp.StatusCode)
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, (8<<20)+1))
	if err != nil {
		return Page{}, err
	}
	if len(raw) == 0 || len(raw) > 8<<20 {
		return Page{}, fmt.Errorf("empty/oversized macro response")
	}
	return Page{URL: address, Raw: raw, FetchedAt: time.Now().UTC()}, nil
}

var anchorPattern = regexp.MustCompile(`(?is)<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a\s*>`)

type link struct{ URL, Title string }

func links(page Page) []link {
	base, _ := url.Parse(page.URL)
	var out []link
	for _, a := range anchorPattern.FindAllStringSubmatch(string(page.Raw), -1) {
		u, err := url.Parse(html.UnescapeString(strings.TrimSpace(a[1])))
		if err != nil {
			continue
		}
		out = append(out, link{base.ResolveReference(u).String(), text(a[2])})
	}
	return out
}

func (c *Client) Discover(ctx context.Context, source string) Discovery {
	d := Discovery{URLs: map[string]string{}, Failures: map[string]string{}}
	get := func(address string) (Page, error) {
		p, e := c.Fetch(ctx, source, address)
		if e == nil {
			d.Pages = append(d.Pages, p)
		}
		return p, e
	}
	if source == "nbs" {
		for page := 0; page < 3; page++ {
			address := "https://www.stats.gov.cn/sj/zxfb/"
			if page > 0 {
				address += fmt.Sprintf("index_%d.html", page)
			}
			p, err := get(address)
			if err != nil {
				d.Failures["catalogue"] = err.Error()
				break
			}
			for _, a := range links(p) {
				if !strings.HasPrefix(a.URL, "https://www.stats.gov.cn/sj/zxfb/") || !strings.HasSuffix(a.URL, ".html") {
					continue
				}
				for _, f := range Feeds() {
					if f.Source == source && regexp.MustCompile(f.TitlePattern).MatchString(a.Title) && (d.URLs[f.Key] == "" || a.URL > d.URLs[f.Key]) {
						d.URLs[f.Key] = a.URL
					}
				}
			}
			found, want := 0, 0
			for _, f := range Feeds() {
				if f.Source == source {
					want++
					if d.URLs[f.Key] != "" {
						found++
					}
				}
			}
			if found == want {
				break
			}
		}
	} else if source == "pbc" {
		reports, reportErr := get("https://www.pbc.gov.cn/diaochatongjisi/116219/116225/index.html")
		if reportErr != nil {
			d.Failures["financial_report"] = reportErr.Error()
		} else {
			for _, a := range links(reports) {
				if regexp.MustCompile(`^20[0-9]{2}年.*金融统计数据报告$`).MatchString(a.Title) {
					d.URLs["financial_report"] = a.URL
					break
				}
			}
		}
		p, err := get("https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html")
		if err != nil {
			d.Failures["catalogue"] = err.Error()
			return d
		}
		all := links(p)
		years := []link{}
		for _, a := range all {
			m := regexp.MustCompile(`^(20[0-9]{2})年统计数据$`).FindStringSubmatch(a.Title)
			if m != nil {
				year, _ := strconv.Atoi(m[1])
				if year <= time.Now().Year() {
					years = append(years, a)
				}
			}
		}
		sort.Slice(years, func(i, j int) bool { return years[i].Title > years[j].Title })
		if len(years) == 0 {
			d.Failures["catalogue"] = "official annual statistical catalogue missing"
			return d
		}
		prefix := strings.TrimSuffix(years[0].URL, "index.html")
		for _, category := range []string{"社会融资规模", "货币统计概览"} {
			addresses := map[string]bool{}
			for _, a := range all {
				if a.Title == category && strings.HasPrefix(a.URL, prefix) {
					addresses[a.URL] = true
				}
			}
			if len(addresses) != 1 {
				d.Failures[category] = "ambiguous/missing official category"
				continue
			}
			for address := range addresses {
				page, err := get(address)
				if err != nil {
					d.Failures[category] = err.Error()
					continue
				}
				for _, f := range Feeds() {
					if f.Source != source || f.Key == "financial_report" {
						continue
					}
					// Each official category table binds one Chinese title to its htm/xls/pdf links.
					for _, table := range tablePattern.FindAllStringSubmatch(string(page.Raw), -1) {
						if !strings.Contains(text(table[1]), f.Name) {
							continue
						}
						for _, a := range links(Page{URL: address, Raw: []byte(table[1])}) {
							if a.Title == "htm" {
								if previous := d.URLs[f.Key]; previous != "" && previous != a.URL {
									d.Failures[f.Key] = "conflicting official table links"
								} else {
									d.URLs[f.Key] = a.URL
								}
							}
						}
					}
				}
			}
		}
	} else {
		if source == "mof" {
			page, err := get("https://gks.mof.gov.cn/tongjishuju/")
			if err != nil {
				d.Failures["catalogue"] = err.Error()
				return d
			}
			for _, a := range links(page) {
				if _, _, e := fiscalPeriod(a.Title); e == nil {
					if d.URLs["fiscal"] == "" || a.URL > d.URLs["fiscal"] {
						d.URLs["fiscal"] = a.URL
					}
				}
			}
		} else {
			d.Failures["source"] = "unsupported macro source"
		}
	}
	for _, f := range Feeds() {
		if f.Source == source && d.URLs[f.Key] == "" {
			d.Failures[f.Key] = "not found in bounded official catalogue; not evidence that the statistic is absent"
		}
	}
	return d
}
