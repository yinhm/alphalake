package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"

	"github.com/yinhm/alphalake/internal/ingest"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
)

func runCoverageUpgrade(ctx context.Context, args []string) error {
	if len(args) != 1 {
		return usageError("usage: upgrade-filing-coverage <schema52-db>")
	}
	db, err := store.Open(ctx, args[0])
	if err != nil {
		return err
	}
	defer db.Close()
	return store.UpgradeFilingCoverage(ctx, db)
}

func runCoverageImport(ctx context.Context, args []string) error {
	if len(args) != 2 {
		return usageError("usage: import-filing-coverage <db> <reviews.json>")
	}
	f, err := os.Open(args[1])
	if err != nil {
		return err
	}
	defer f.Close()
	var reviews []ingest.FilingCoverageReview
	decoder := json.NewDecoder(f)
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(&reviews); err != nil {
		return err
	}
	if err = decoder.Decode(new(any)); err != io.EOF {
		return fmt.Errorf("trailing review JSON")
	}
	if len(reviews) == 0 {
		return fmt.Errorf("empty review batch")
	}
	db, err := store.OpenInitialized(ctx, args[0])
	if err != nil {
		return err
	}
	defer db.Close()
	root, err := store.FinancialArchiveRoot(ctx, db)
	if err != nil {
		return err
	}
	for _, review := range reviews {
		inserted, err := ingest.ImportFilingCoverage(ctx, db, root, review)
		if err != nil {
			return fmt.Errorf("%s %s: %w", review.Code, review.Period, err)
		}
		fmt.Printf("disclosure coverage: code=%s period=%s inserted=%t\n", review.Code, review.Period, inserted)
	}
	return nil
}

func runExportProspectuses(ctx context.Context, args []string) error {
	if len(args) != 1 {
		return usageError("usage: export-prospectuses <db>")
	}
	db, err := store.OpenReadOnly(ctx, args[0])
	if err != nil {
		return err
	}
	defer db.Close()
	rows, err := db.QueryContext(ctx, `SELECT CAST(to_json(f) AS VARCHAR) FROM (
 SELECT f.provider_code AS code,f.security_name,f.source_filing_id AS announcement_id,f.title,f.announcement_time,f.resolution_status,f.source_url,f.sha256 AS pdf_sha256,a.local_path
 FROM fundamental.filing f LEFT JOIN meta.artifact a ON a.artifact_id=f.artifact_id WHERE f.filing_type='prospectus' ORDER BY f.provider_code,f.announcement_time) f`)
	if err != nil {
		return err
	}
	defer rows.Close()
	out := []json.RawMessage{}
	for rows.Next() {
		var raw string
		if err = rows.Scan(&raw); err != nil {
			return err
		}
		out = append(out, json.RawMessage(raw))
	}
	if err = rows.Err(); err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(out)
}
