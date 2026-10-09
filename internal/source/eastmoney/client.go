package eastmoney

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

const Endpoint = "https://datacenter-web.eastmoney.com/api/data/v1/get"

type Client struct {
	HTTP     *http.Client
	Endpoint string
}

func NewClient() *Client {
	return &Client{HTTP: &http.Client{Timeout: 45 * time.Second}, Endpoint: Endpoint}
}

func (c *Client) GuidancePage(ctx context.Context, period string, page, size int) ([]byte, string, error) {
	if _, err := time.Parse("2006-01-02", period); err != nil || page < 1 || size < 1 || size > 500 {
		return nil, "", fmt.Errorf("invalid guidance period/page/size")
	}
	endpoint, err := url.Parse(c.Endpoint)
	if err != nil || endpoint.Host == "" || (endpoint.Scheme != "http" && endpoint.Scheme != "https") || c.HTTP == nil {
		return nil, "", fmt.Errorf("invalid Eastmoney client")
	}
	q := endpoint.Query()
	q.Set("reportName", GuidanceReport)
	q.Set("columns", "ALL")
	q.Set("filter", "(REPORT_DATE='"+period+"')")
	q.Set("pageNumber", strconv.Itoa(page))
	q.Set("pageSize", strconv.Itoa(size))
	q.Set("sortColumns", "NOTICE_DATE,SECUCODE,PREDICT_FINANCE_CODE")
	q.Set("sortTypes", "-1,1,1")
	q.Set("source", "WEB")
	q.Set("client", "WEB")
	endpoint.RawQuery = q.Encode()
	var last error
	var raw []byte
	for attempt := 0; attempt < 3; attempt++ {
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
		if err != nil {
			return nil, endpoint.String(), err
		}
		request.Header.Set("User-Agent", "AlphaLake/0.0 (+https://github.com/yinhm/alphalake)")
		request.Header.Set("Referer", "https://data.eastmoney.com/")
		response, err := c.HTTP.Do(request)
		if err != nil {
			last = err
		} else {
			raw, err = io.ReadAll(io.LimitReader(response.Body, (32<<20)+1))
			response.Body.Close()
			if len(raw) > 32<<20 {
				return nil, endpoint.String(), fmt.Errorf("Eastmoney response exceeds 32MiB")
			}
			if err == nil && response.StatusCode == http.StatusOK {
				return raw, endpoint.String(), nil
			}
			last = fmt.Errorf("Eastmoney HTTP %d: %v", response.StatusCode, err)
			if response.StatusCode >= 400 && response.StatusCode < 500 && response.StatusCode != 429 {
				break
			}
		}
		if attempt < 2 {
			select {
			case <-ctx.Done():
				return raw, endpoint.String(), ctx.Err()
			case <-time.After(time.Duration(attempt+1) * 250 * time.Millisecond):
			}
		}
	}
	return raw, endpoint.String(), last
}
