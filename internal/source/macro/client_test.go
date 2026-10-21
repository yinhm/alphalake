package macro

import (
	"io"
	"net/http"
	"strings"
	"testing"
)

type macroTransport func(*http.Request) (*http.Response, error)

func (f macroTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func TestMacroHTTPRetriesTransientErrorsButNotAccessDenials(t *testing.T) {
	for _, status := range []int{502, 403} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			calls := 0
			client := NewClient()
			client.HTTP.Transport = retryTransport{base: macroTransport(func(r *http.Request) (*http.Response, error) {
				calls++
				code := status
				if calls > 1 {
					code = 200
				}
				return &http.Response{StatusCode: code, Body: io.NopCloser(strings.NewReader("official fixture response")), Header: make(http.Header), Request: r}, nil
			})}
			_, err := client.Fetch(t.Context(), "nbs", "https://www.stats.gov.cn/sj/zxfb/")
			if status == 502 && (err != nil || calls != 2) {
				t.Fatal(calls, err)
			}
			if status == 403 && (err == nil || calls != 1) {
				t.Fatal(calls, err)
			}
		})
	}
}
