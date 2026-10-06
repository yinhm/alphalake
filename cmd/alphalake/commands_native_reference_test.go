package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/artifact"
	store "github.com/yinhm/alphalake/internal/store/duckdb"
)

func TestNativeReferenceEvidenceUsesExplicitArchiveRoot(t *testing.T) {
	ctx := t.Context()
	root := t.TempDir()
	t.Setenv("ALPHALAKE_WORKSPACE", root)
	db, err := store.OpenInitialized(ctx, filepath.Join(t.TempDir(), "candidate.duckdb"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	evidence, err := artifact.Persist(ctx, db, root, artifact.Input{Source: "damodaran", Dataset: "root-check", SourceLocator: "wacc.xls", FetchedAt: time.Now(), Content: []byte("retained evidence bytes; not a valuation fixture")})
	if err != nil {
		t.Fatal(err)
	}
	association := store.IssuerIndustryAssociation{AssociationArtifactID: evidence.ArtifactID, Document: store.IssuerIndustryDocument{ArtifactID: evidence.ArtifactID, CatalogueArtifactID: evidence.ArtifactID}}
	packet := map[string]any{"releases": []json.RawMessage{json.RawMessage(fmt.Sprintf(`{"artifact_id":%d}`, evidence.ArtifactID))}, "issuer_associations": []store.IssuerIndustryAssociation{association}}
	if err = verifyNativeReferenceEvidence(ctx, db, packet); err != nil {
		t.Fatal("relocated candidate must retain evidence root", err)
	}
	if err = os.WriteFile(artifact.Resolve(root, evidence), []byte("tampered"), 0600); err != nil {
		t.Fatal(err)
	}
	if err = verifyNativeReferenceEvidence(ctx, db, packet); err == nil {
		t.Fatal("tampered reference evidence accepted")
	}
	packet["releases"] = []json.RawMessage{}
	if err = verifyNativeReferenceEvidence(ctx, db, packet); err == nil {
		t.Fatal("tampered issuer evidence accepted")
	}
}
