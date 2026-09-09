package ingest

import (
	"context"
	source "github.com/yinhm/alphalake/internal/source/tdx"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
	"net"
	"path/filepath"
	"strings"
	"testing"
)

func TestTDXExhaustedNodesPersistFailure(t *testing.T) {
	// Closed local ports exercise actual connection refusal, not mocked retries.
	var hosts []string
	var listeners []net.Listener
	for range 4 {
		l, e := net.Listen("tcp", "127.0.0.1:0")
		if e != nil {
			t.Fatal(e)
		}
		listeners = append(listeners, l)
		hosts = append(hosts, l.Addr().String())
	}
	for _, l := range listeners {
		l.Close()
	}
	c, e := source.DialHosts(hosts[:3])
	if e != nil {
		t.Fatal(e)
	}
	defer c.Close()
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "failed.duckdb")
	db, e := store.OpenAndMigrate(ctx, path)
	if e != nil {
		t.Fatal(e)
	}
	summary, e := SyncTDXDailyWithSummary(ctx, db, c, "sh600519")
	if e == nil {
		t.Fatal("exhausted connections succeeded")
	}
	db.Close()
	db, e = store.Open(ctx, path)
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	var status, message string
	if e = db.QueryRowContext(ctx, `SELECT status,error_message FROM meta.ingest_run WHERE ingest_run_id=?`, summary.RunID).Scan(&status, &message); e != nil {
		t.Fatal(e)
	}
	if status != "failed" {
		t.Fatal(status, message)
	}
	for _, host := range hosts[:3] {
		if !strings.Contains(message, host) {
			t.Fatal("node detail lost on restart", message)
		}
	}
	if strings.Contains(message, hosts[3]) {
		t.Fatal("unconfigured fourth host")
	}
}
