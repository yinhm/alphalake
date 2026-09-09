package tdx

import (
	"context"
	"errors"
	tdxlib "github.com/injoyai/tdx"
	"strings"
	"testing"
)

func TestNodeRecoveryAndThreeServerLimit(t *testing.T) {
	c, e := DialHosts([]string{"127.0.0.1:7709", "127.0.0.1:07709", "127.0.0.2", "127.0.0.3", "127.0.0.4"})
	if e != nil {
		t.Fatal(e)
	}
	defer c.Close()
	var dials []string
	closed := 0
	c.dial = func(ctx context.Context, host string) (*nodeSession, error) {
		dials = append(dials, host)
		if host == "127.0.0.1:7709" {
			return nil, errors.New("connect refused")
		}
		return &nodeSession{sdk: &tdxlib.Client{}, stop: func() { closed++ }}, nil
	}
	calls := 0
	value, e := withNode(context.Background(), c, "test", func(*tdxlib.Client) (string, error) {
		calls++
		if calls == 1 {
			return "partial", errors.New("request timeout")
		}
		return "complete", nil
	})
	if e != nil || value != "complete" || calls != 2 || len(dials) != 3 || closed != 1 {
		t.Fatal(value, e, calls, dials, closed)
	}
	_, e = withNode(context.Background(), c, "reuse", func(*tdxlib.Client) (string, error) { return "ok", nil })
	if e != nil || len(dials) != 3 {
		t.Fatal("healthy node not retained", e, dials)
	}
	dials = nil
	sentinel := errors.New("request failed")
	value, e = withNode(context.Background(), c, "limit", func(*tdxlib.Client) (string, error) { return "partial", sentinel })
	if value != "" || !errors.Is(e, sentinel) || len(dials) != 2 {
		t.Fatal(value, e, dials)
	}
	for _, host := range []string{"127.0.0.3:7709", "127.0.0.4:7709", "127.0.0.1:7709"} {
		if !strings.Contains(e.Error(), host) {
			t.Fatal("missing attempt", e)
		}
	}
	if strings.Contains(e.Error(), "127.0.0.2:7709") {
		t.Fatal("fourth node attempted", e)
	}
}

func TestNodeCancellationClosesAndStops(t *testing.T) {
	c, _ := DialHosts([]string{"127.0.0.1", "127.0.0.2"})
	defer c.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	stopped := make(chan struct{})
	dials := 0
	c.dial = func(context.Context, string) (*nodeSession, error) {
		dials++
		return &nodeSession{stop: func() { close(stopped) }}, nil
	}
	v, e := withNode(ctx, c, "cancel", func(*tdxlib.Client) (string, error) { cancel(); <-stopped; return "partial", nil })
	if v != "" || !errors.Is(e, context.Canceled) || dials != 1 {
		t.Fatal(v, e, dials)
	}
	c.Close()
	_, e = withNode(context.Background(), c, "closed", func(*tdxlib.Client) (int, error) { t.Fatal("closed client called"); return 0, nil })
	if e == nil {
		t.Fatal("closed client accepted")
	}
}
