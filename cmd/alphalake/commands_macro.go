package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"syscall"

	"github.com/yinhm/alphalake/internal/ingest"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func runMacroData(ctx context.Context, command string, args []string) error {
	if len(args) == 0 {
		return usageError("%s <db-path>; see command help for synchronization/query options", command)
	}
	if command == "upgrade-macro-data" {
		if len(args) != 1 {
			return usageError("upgrade-macro-data <unpublished-schema58-copy>")
		}
		db, err := duckstore.Open(ctx, args[0])
		if err != nil {
			return err
		}
		defer db.Close()
		return duckstore.UpgradeMacroData(ctx, db)
	}
	fs := flag.NewFlagSet(command, flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	source, root := "all", filepath.Dir(args[0])
	offline, latest := false, false
	from, through := "", ""
	series := []string{}
	if command == "sync-macro" {
		fs.StringVar(&source, "source", "all", "official source all, nbs, pbc or mof")
		fs.StringVar(&root, "workspace", root, "source archive root")
		fs.BoolVar(&offline, "offline", false, "replay verified published archives without HTTP")
	}
	if command == "macro-data" {
		fs.StringVar(&from, "from", "", "first observation period end")
		fs.StringVar(&through, "through", "", "last observation period end")
		fs.BoolVar(&latest, "latest", false, "latest observation period for each requested series")
		fs.Func("series", "repeatable standard macro series name", func(value string) error { series = append(series, value); return nil })
	}
	if err := fs.Parse(args[1:]); err != nil {
		return parseError(err)
	}
	if fs.NArg() != 0 {
		return usageError("unexpected macro arguments")
	}
	if command == "sync-macro" {
		path := filepath.Join(root, "derived", "macro", "sync.lock")
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			return err
		}
		lock, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
		if err != nil {
			return err
		}
		defer lock.Close()
		if err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
			return fmt.Errorf("macro workspace already locked: %w", err)
		}
		defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
		db, err := duckstore.OpenInitialized(ctx, args[0])
		if err != nil {
			return err
		}
		defer db.Close()
		out, err := ingest.SyncMacroData(ctx, db, root, ingest.MacroOptions{Source: source, Offline: offline})
		if encoded := json.NewEncoder(os.Stdout).Encode(out); encoded != nil {
			return errors.Join(err, encoded)
		}
		return err
	}
	db, err := duckstore.OpenReadOnly(ctx, args[0])
	if err != nil {
		return err
	}
	defer db.Close()
	version, err := duckstore.CurrentSchemaVersion(ctx, db)
	if err != nil || version != duckstore.SchemaVersion {
		return fmt.Errorf("macro query requires current schema: %d (%v)", version, err)
	}
	if command == "macro-series" {
		rows, err := duckstore.ListMacroSeries(ctx, db)
		if err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"contract": "alphalake-macro-series-v1", "count": len(rows), "series": rows})
	}
	rows, err := duckstore.QueryMacroData(ctx, db, series, from, through, latest)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"contract": "alphalake-macro-data-v1", "count": len(rows), "vintage": "latest_source_vintage_per_series_period_not_historical_pit", "observations": rows})
}
