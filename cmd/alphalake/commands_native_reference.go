package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/ingest"
	"github.com/yinhm/alphalake/internal/source/damodaran"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
	"io"
	"os"
	"path/filepath"
	"time"
)

func runNativeReference(ctx context.Context, command string, args []string) error {
	if len(args) < 1 {
		return usageError("usage: %s <db-path> [--file stem --local workbook | --offline] [--as-of RFC3339]", command)
	}
	fs := flag.NewFlagSet(command, flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	stem := fs.String("file", "", "reviewed workbook stem")
	local := fs.String("local", "", "import existing source workbook; acquisition time is import time")
	offline := fs.Bool("offline", false, "replay registered archive")
	script := fs.String("parser", damodaran.NativeReferenceScript, "reviewed parser script")
	python := fs.String("python", "python3", "Python with xlrd")
	at := fs.String("as-of", "", "reference information cutoff")
	if err := fs.Parse(args[1:]); err != nil {
		return parseError(err)
	}
	if fs.NArg() != 0 {
		return usageError("unexpected arguments")
	}
	if command == "upgrade-native-references" {
		db, err := duckstore.Open(ctx, args[0])
		if err != nil {
			return err
		}
		defer db.Close()
		return duckstore.UpgradeNativeReferences(ctx, db)
	}
	db, err := duckstore.OpenInitialized(ctx, args[0])
	if err != nil {
		return err
	}
	defer db.Close()
	var out any
	if command == "export-native-references" {
		date, e := time.Parse(time.RFC3339Nano, *at)
		if e != nil {
			return parseError(e)
		}
		packet, exportErr := duckstore.NativeReferenceExport(ctx, db, date)
		if exportErr != nil {
			return exportErr
		}
		for _, body := range packet["releases"].([]json.RawMessage) {
			var r struct {
				ArtifactID int64 `json:"artifact_id"`
			}
			if err = json.Unmarshal(body, &r); err != nil {
				return err
			}
			if _, _, err = artifact.LoadByID(ctx, db, filepath.Dir(args[0]), r.ArtifactID); err != nil {
				return fmt.Errorf("native reference evidence verification: %w", err)
			}
		}
		for _, a := range packet["issuer_associations"].([]duckstore.IssuerIndustryAssociation) {
			for _, id := range []int64{a.AssociationArtifactID, a.Document.ArtifactID, a.Document.CatalogueArtifactID} {
				if _, _, err = artifact.LoadByID(ctx, db, filepath.Dir(args[0]), id); err != nil {
					return fmt.Errorf("issuer reference evidence verification: %w", err)
				}
			}
		}
		out = packet
	} else {
		out, err = ingest.SyncNativeReference(ctx, db, filepath.Dir(args[0]), *stem, ingest.ReferenceOptions{Python: *python, Script: *script, LocalPath: *local, Offline: *offline})
	}
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(out)
}
