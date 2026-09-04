package cninfo

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	defaultBaseURL         = "https://www.cninfo.com.cn"
	defaultDocumentBaseURL = "https://static.cninfo.com.cn/"
	cataloguePath          = "/new/hisAnnouncement/query"
	defaultUserAgent       = "AlphaLake/0.0 (+https://github.com/yinhm/alphalake)"
	maxCatalogueBytes      = 32 << 20
	maxDocumentBytes       = 256 << 20
)

type ClientOptions struct {
	BaseURL         string
	DocumentBaseURL string
	UserAgent       string
	Referer         string
	Retries         int
	MinInterval     time.Duration
}

type Client struct {
	httpClient *http.Client
	baseURL    *url.URL
	documentBaseURL *url.URL
	userAgent  string
	referer    string
	retries    int
	minInterval time.Duration

	mu          sync.Mutex
	lastRequest time.Time
}

func NewDefaultClient() (*Client, error) {
	return NewClient(&http.Client{Timeout: 45 * time.Second}, ClientOptions{
		Retries: 3, MinInterval: 250 * time.Millisecond,
	})
}

func NewClient(httpClient *http.Client, options ClientOptions) (*Client, error) {
	if httpClient == nil {
		return nil, errors.New("CNINFO http client is nil")
	}
	base := strings.TrimSpace(options.BaseURL)
	if base == "" {
		base = defaultBaseURL
	}
	documentBase := strings.TrimSpace(options.DocumentBaseURL)
	if documentBase == "" {
		documentBase = defaultDocumentBaseURL
	}
	baseURL, err := url.Parse(base)
	if err != nil || baseURL.Scheme == "" || baseURL.Host == "" {
		return nil, fmt.Errorf("invalid CNINFO base URL %q", base)
	}
	documentBaseURL, err := url.Parse(documentBase)
	if err != nil || documentBaseURL.Scheme == "" || documentBaseURL.Host == "" {
		return nil, fmt.Errorf("invalid CNINFO document base URL %q", documentBase)
	}
	userAgent := strings.TrimSpace(options.UserAgent)
	if userAgent == "" {
		userAgent = defaultUserAgent
	}
	referer := strings.TrimSpace(options.Referer)
	if referer == "" {
		referer = baseURL.ResolveReference(&url.URL{Path: "/new/disclosure"}).String()
	}
	retries := options.Retries
	if retries < 0 {
		return nil, errors.New("CNINFO retries must be non-negative")
	}
	return &Client{
		httpClient: httpClient, baseURL: baseURL, documentBaseURL: documentBaseURL,
		userAgent: userAgent, referer: referer, retries: retries,
		minInterval: options.MinInterval,
	}, nil
}

func (c *Client) CataloguePage(ctx context.Context, request CatalogueRequest) (CataloguePage, []byte, error) {
	if c == nil {
		return CataloguePage{}, nil, errors.New("CNINFO client is nil")
	}
	if err := ValidateCatalogueRequest(request); err != nil {
		return CataloguePage{}, nil, err
	}
	start := dateUTC(request.StartDate)
	end := dateUTC(request.EndDate)
	form := url.Values{
		"pageNum":   {strconv.Itoa(request.Page)},
		"pageSize":  {strconv.Itoa(request.PageSize)},
		"column":    {"szse"},
		"tabName":   {"fulltext"},
		"plate":     {""},
		"stock":     {""},
		"searchkey": {""},
		"secid":     {""},
		"category":  {PeriodicReportCategories},
		"trade":     {""},
		"seDate":    {start.Format("2006-01-02") + "~" + end.Format("2006-01-02")},
		"sortName":  {"time"},
		"sortType":  {"desc"},
		"isHLtitle": {"true"},
	}
	endpoint := c.baseURL.ResolveReference(&url.URL{Path: cataloguePath}).String()
	headers := map[string]string{
		"Accept":           "application/json, text/javascript, */*; q=0.01",
		"Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
		"Origin":           c.baseURL.Scheme + "://" + c.baseURL.Host,
		"Referer":          c.referer,
		"X-Requested-With": "XMLHttpRequest",
	}
	response, err := c.do(ctx, http.MethodPost, endpoint, []byte(form.Encode()), headers, maxCatalogueBytes)
	if err != nil {
		return CataloguePage{}, nil, err
	}
	page, err := ParseCataloguePage(response.body)
	if err != nil {
		return CataloguePage{}, response.body, err
	}
	if page.Page == 0 {
		page.Page = request.Page
	}
	if page.PageSize == 0 {
		page.PageSize = request.PageSize
	}
	return page, response.body, nil
}

// FilingDocument downloads one CNINFO attachment and returns provider bytes,
// the canonical absolute source URL, and the response media type.
func (c *Client) FilingDocument(ctx context.Context, locator string) ([]byte, string, string, error) {
	if c == nil {
		return nil, "", "", errors.New("CNINFO client is nil")
	}
	resolved, err := c.resolveDocumentURL(locator)
	if err != nil {
		return nil, "", "", err
	}
	headers := map[string]string{
		"Accept":  "application/pdf, application/octet-stream, */*",
		"Referer": c.referer,
	}
	response, err := c.do(ctx, http.MethodGet, resolved.String(), nil, headers, maxDocumentBytes)
	if err != nil {
		return nil, "", "", err
	}
	mediaType := response.header.Get("Content-Type")
	if i := strings.IndexByte(mediaType, ';'); i >= 0 {
		mediaType = mediaType[:i]
	}
	return response.body, resolved.String(), strings.TrimSpace(mediaType), nil
}

func (c *Client) resolveDocumentURL(locator string) (*url.URL, error) {
	locator = strings.TrimSpace(locator)
	if locator == "" {
		return nil, errors.New("CNINFO document locator is empty")
	}
	parsed, err := url.Parse(locator)
	if err != nil {
		return nil, fmt.Errorf("parse CNINFO document locator: %w", err)
	}
	resolved := c.documentBaseURL.ResolveReference(parsed)
	if resolved.Scheme != "http" && resolved.Scheme != "https" {
		return nil, fmt.Errorf("unsupported CNINFO document scheme %q", resolved.Scheme)
	}
	if !strings.EqualFold(resolved.Host, c.documentBaseURL.Host) {
		return nil, fmt.Errorf("CNINFO document host %q differs from configured host %q", resolved.Host, c.documentBaseURL.Host)
	}
	return resolved, nil
}

type httpResponse struct {
	body   []byte
	header http.Header
}

func (c *Client) do(ctx context.Context, method, endpoint string, body []byte, headers map[string]string, maxBytes int64) (httpResponse, error) {
	var lastErr error
	for attempt := 0; attempt <= c.retries; attempt++ {
		if err := c.wait(ctx); err != nil {
			return httpResponse{}, err
		}
		request, err := http.NewRequestWithContext(ctx, method, endpoint, bytes.NewReader(body))
		if err != nil {
			return httpResponse{}, fmt.Errorf("build CNINFO request: %w", err)
		}
		request.Header.Set("User-Agent", c.userAgent)
		for key, value := range headers {
			request.Header.Set(key, value)
		}
		response, err := c.httpClient.Do(request)
		if err == nil {
			payload, readErr := readLimited(response.Body, maxBytes)
			response.Body.Close()
			if readErr != nil {
				return httpResponse{}, readErr
			}
			if response.StatusCode >= 200 && response.StatusCode < 300 {
				return httpResponse{body: payload, header: response.Header.Clone()}, nil
			}
			lastErr = fmt.Errorf("CNINFO %s %s returned %s: %s", method, endpoint, response.Status, truncate(string(payload), 300))
			if response.StatusCode != http.StatusTooManyRequests && response.StatusCode < 500 {
				return httpResponse{}, lastErr
			}
			if delay := retryAfter(response.Header.Get("Retry-After")); delay > 0 {
				if err := sleepContext(ctx, delay); err != nil {
					return httpResponse{}, err
				}
			}
		} else {
			lastErr = fmt.Errorf("CNINFO %s %s: %w", method, endpoint, err)
		}
		if attempt < c.retries {
			delay := time.Duration(1<<attempt) * 250 * time.Millisecond
			if err := sleepContext(ctx, delay); err != nil {
				return httpResponse{}, err
			}
		}
	}
	return httpResponse{}, lastErr
}

func (c *Client) wait(ctx context.Context) error {
	if c.minInterval <= 0 {
		return nil
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	wait := c.minInterval - time.Since(c.lastRequest)
	if wait > 0 {
		if err := sleepContext(ctx, wait); err != nil {
			return err
		}
	}
	c.lastRequest = time.Now()
	return nil
}

func readLimited(reader io.Reader, maxBytes int64) ([]byte, error) {
	payload, err := io.ReadAll(io.LimitReader(reader, maxBytes+1))
	if err != nil {
		return nil, fmt.Errorf("read CNINFO response: %w", err)
	}
	if int64(len(payload)) > maxBytes {
		return nil, fmt.Errorf("CNINFO response exceeds %d bytes", maxBytes)
	}
	return payload, nil
}

func retryAfter(value string) time.Duration {
	seconds, err := strconv.Atoi(strings.TrimSpace(value))
	if err != nil || seconds <= 0 {
		return 0
	}
	return time.Duration(seconds) * time.Second
}

func sleepContext(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func truncate(value string, max int) string {
	value = strings.TrimSpace(value)
	if len(value) <= max {
		return value
	}
	return value[:max]
}
