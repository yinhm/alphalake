package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func runIssuerReference(ctx context.Context, command string, args []string) error {
	if len(args) < 1 {
		return usageError("usage: %s <db-path> [--manifest file --workbook file --coverage file --workspace root --python interpreter]", command)
	}
	if command == "upgrade-issuer-references" {
		db, err := duckstore.Open(ctx, args[0])
		if err != nil {
			return err
		}
		defer db.Close()
		return duckstore.UpgradeIssuerReferences(ctx, db)
	}
	fs := flag.NewFlagSet(command, flag.ContinueOnError)
	manifest := fs.String("manifest", "", "reviewed issuer ledger")
	workbook := fs.String("workbook", "", "archived official company workbook")
	coverage := fs.String("coverage", "", "complete security denominator")
	root := fs.String("workspace", filepath.Dir(args[0]), "artifact root")
	python := fs.String("python", "python3", "Python with existing parser dependencies")
	if err := fs.Parse(args[1:]); err != nil {
		return err
	}
	if fs.NArg() != 0 || *manifest == "" || *workbook == "" || *coverage == "" {
		return usageError("explicit manifest/workbook/coverage required")
	}
	cmd := exec.CommandContext(ctx, *python, "-m", "tools.verify_issuer_reference", "--manifest", *manifest, "--workbook", *workbook, "--coverage", *coverage, "--workspace", *root)
	cmd.Env = append(os.Environ(), "PYTHONPATH=valuation/backend")
	cmd.Stderr = os.Stderr
	raw, err := cmd.Output()
	if err != nil {
		return fmt.Errorf("issuer evidence verification: %w", err)
	}
	var packet duckstore.IssuerIndustryPacket
	if err = json.Unmarshal(raw, &packet); err != nil {
		return err
	}
	db, err := duckstore.OpenInitialized(ctx, args[0])
	if err != nil {
		return err
	}
	defer db.Close()
	for i := range packet.Active {
		doc := &packet.Active[i].Document
		for _, proof := range []struct {
			path, sha, source, dataset, locator, media string
			dest                                       *int64
		}{
			{doc.LocalPath, doc.SHA256, "cninfo", "issuer_identity_document", doc.SourceURL, "application/pdf", &doc.ArtifactID},
			{doc.CataloguePath, doc.CatalogueSHA256, "cninfo", "issuer_identity_catalogue", "https://www.cninfo.com.cn/new/hisAnnouncement/query#" + doc.AnnouncementID, "application/json", &doc.CatalogueArtifactID},
		} {
			body, e := os.ReadFile(filepath.Join(*root, proof.path))
			if e != nil {
				return e
			}
			stored, e := artifact.Persist(ctx, db, *root, artifact.Input{Source: proof.source, Dataset: proof.dataset, SourceLocator: proof.locator, FetchedAt: time.Now().UTC(), MediaType: proof.media, ParserVersion: "issuer-identity-review-v1", Content: body})
			if e != nil {
				return e
			}
			if stored.SHA256 != proof.sha {
				return fmt.Errorf("issuer evidence changed during import")
			}
			*proof.dest = stored.ArtifactID
		}
	}
	raw, err = json.Marshal(packet)
	if err != nil {
		return err
	}
	stored, err := artifact.Persist(ctx, db, *root, artifact.Input{Source: "issuer-review", Dataset: "company_industry", SourceLocator: "review://company-industries/" + packet.ManifestSHA256 + ".json", FetchedAt: time.Now().UTC(), MediaType: "application/json", ParserVersion: "issuer-reference-verifier-v1", Content: raw})
	if err != nil {
		return err
	}
	inserted, err := duckstore.ImportIssuerIndustryReview(ctx, db, stored.ArtifactID, packet)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"inserted": inserted, "artifact_id": stored.ArtifactID, "active_reviews": len(packet.Active), "revoked": packet.Revoked})
}
