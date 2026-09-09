package tdx

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/injoyai/ios"
	tdxlib "github.com/injoyai/tdx"
)

const maxServerAttempts = 3
const nodeAttemptTimeout = 45 * time.Second

type nodeSession struct {
	sdk  *tdxlib.Client
	stop func()
	once sync.Once
}

func (s *nodeSession) close() { s.once.Do(s.stop) }

type nodeDial func(context.Context, string) (*nodeSession, error)

// Client owns SDK connections. Operations are serialized so replacing a failed
// connection cannot interrupt another request on that same connection.
// ponytail: one active connection; use separate Clients if parallelism is needed.
type Client struct {
	mu      sync.Mutex
	hosts   []string
	index   int
	session *nodeSession
	dial    nodeDial
	closed  bool
}

// DialDefault prepares a lazy connection: dial failures belong to the first
// tracked operation and share its three-server budget, not a separate budget.
func DialDefault() (*Client, error) {
	// Spread first choices across SDK endpoint groups rather than three adjacent
	// addresses. These are server regions, not exchange capability guarantees.
	var hosts []string
	groups := [][]string{tdxlib.SHHosts, tdxlib.BJHosts, tdxlib.GZHosts, tdxlib.WHHosts}
	for i := 0; ; i++ {
		added := false
		for _, group := range groups {
			if i < len(group) {
				hosts = append(hosts, group[i])
				added = true
			}
		}
		if !added {
			break
		}
	}
	return DialHosts(hosts)
}

// DialHosts retains explicit order, normalizes ports and removes duplicates.
func DialHosts(hosts []string) (*Client, error) {
	var unique []string
	seen := map[string]bool{}
	for _, host := range hosts {
		host = strings.TrimSpace(host)
		if host == "" {
			return nil, errors.New("empty TDX server")
		}
		if !strings.Contains(host, ":") {
			host = net.JoinHostPort(host, "7709")
		}
		name, port, err := net.SplitHostPort(host)
		if err != nil || name == "" || port == "" {
			return nil, fmt.Errorf("invalid TDX server %q", host)
		}
		number, e := strconv.Atoi(port)
		if e != nil || number < 1 || number > 65535 {
			return nil, fmt.Errorf("invalid TDX port %q", port)
		}
		if ip := net.ParseIP(name); ip != nil {
			name = ip.String()
		}
		host = net.JoinHostPort(strings.ToLower(name), strconv.Itoa(number))
		if !seen[host] {
			seen[host] = true
			unique = append(unique, host)
		}
	}
	if len(unique) == 0 {
		return nil, errors.New("TDX servers required")
	}
	return &Client{hosts: unique, dial: dialNode}, nil
}
func dialNode(ctx context.Context, host string) (*nodeSession, error) {
	sdk, err := tdxlib.DialWith(func(_ context.Context) (ios.ReadWriteCloser, string, error) {
		d := net.Dialer{Timeout: 5 * time.Second}
		conn, err := d.DialContext(ctx, "tcp", host)
		return conn, host, err
	}, tdxlib.WithRedial(false), tdxlib.WithDebug(false))
	if err != nil {
		return nil, err
	}
	sdk.SetTimeout(5 * time.Second)
	return &nodeSession{sdk: sdk, stop: func() { sdk.Close() }}, nil
}
func (c *Client) Close() {
	if c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.closed = true
	c.disconnect()
}
func (c *Client) disconnect() {
	if c.session != nil {
		c.session.close()
		c.session = nil
	}
}

// withNode retries the whole SDK operation, discarding partial results. Local
// domain validation happens outside this boundary, not by changing providers.
func withNode[T any](ctx context.Context, c *Client, operation string, call func(*tdxlib.Client) (T, error)) (T, error) {
	var zero T
	if c == nil {
		return zero, errors.New("TDX client is not initialized")
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.hosts) == 0 || c.dial == nil {
		return zero, errors.New("TDX client is not initialized")
	}
	if c.closed {
		return zero, errors.New("TDX client is closed")
	}
	attempts := min(maxServerAttempts, len(c.hosts))
	failures := []error{}
	for n := 0; n < attempts; n++ {
		if err := ctx.Err(); err != nil {
			return zero, err
		}
		host := c.hosts[c.index]
		phase := "request"
		attemptCtx, cancel := context.WithTimeout(ctx, nodeAttemptTimeout)
		var err error
		if c.session == nil {
			phase = "connect"
			c.session, err = c.dial(attemptCtx, host)
		}
		var result T
		if err == nil {
			phase = "request"
			session := c.session
			done := make(chan struct{})
			stop := context.AfterFunc(attemptCtx, func() { session.close(); close(done) })
			result, err = call(session.sdk)
			if !stop() {
				<-done
			}
		}
		if attemptCtx.Err() != nil {
			err = fmt.Errorf("node attempt deadline: %v", attemptCtx.Err())
		}
		cancel()
		if err == nil {
			if len(failures) > 0 {
				slog.Info("TDX request recovered", "operation", operation, "server", host, "attempts", n+1)
			}
			return result, nil
		}
		failures = append(failures, fmt.Errorf("server=%s phase=%s: %w", host, phase, err))
		if n+1 < attempts && ctx.Err() == nil {
			slog.Info("TDX switching server", "operation", operation, "server", host, "attempt", n+1, "phase", phase, "error", err)
		}
		c.disconnect()
		c.index = (c.index + 1) % len(c.hosts)
		if ctx.Err() != nil {
			return zero, ctx.Err()
		}
	}
	err := fmt.Errorf("TDX %s failed after %d distinct servers: %w", operation, len(failures), errors.Join(failures...))
	slog.Warn("TDX servers exhausted", "operation", operation, "attempts", len(failures), "error", err)
	return zero, err
}
