package tdx

import (
	"context"
	"testing"

	"github.com/injoyai/tdx/protocol"
)

func TestParseInconNames(t *testing.T) {
	names := parseInconNames([]byte("#section\r\nT01|Level 1\r\nbad-line\r\nT0101|Level 2\r\n|missing\r\n"))
	if len(names) != 2 || names["T01"] != "Level 1" || names["T0101"] != "Level 2" {
		t.Fatalf("names=%#v", names)
	}
}

func TestBuildIndustrySnapshotsConstructsHierarchyAndLeafMembership(t *testing.T) {
	assignments := []*protocol.TdxHy{{
		Market: 1,
		Code:   "600001",
		TdxHy:  "T010101",
		SwHy:   "X010101001001",
	}}
	names := map[string]string{
		"T01": "TDX L1", "T0101": "TDX L2", "T010101": "TDX L3",
		"X01": "SW L1", "X0101": "SW L2", "X010101": "SW L3",
		"X010101001": "SW L4", "X010101001001": "SW L5",
	}

	tdxSnapshot, err := buildIndustrySnapshot(context.Background(), assignments, names, industrySpecs[0])
	if err != nil {
		t.Fatal(err)
	}
	if !tdxSnapshot.Complete || tdxSnapshot.Taxonomy.Code != ClassificationTDXIndustry || len(tdxSnapshot.Nodes) != 3 {
		t.Fatalf("TDX snapshot=%#v", tdxSnapshot)
	}
	assertIndustryNode(t, tdxSnapshot.Nodes, "T01", "", 1, 0)
	assertIndustryNode(t, tdxSnapshot.Nodes, "T0101", "T01", 2, 0)
	assertIndustryNode(t, tdxSnapshot.Nodes, "T010101", "T0101", 3, 1)

	swSnapshot, err := buildIndustrySnapshot(context.Background(), assignments, names, industrySpecs[1])
	if err != nil {
		t.Fatal(err)
	}
	if !swSnapshot.Complete || swSnapshot.Taxonomy.Code != ClassificationShenwanIndustry || len(swSnapshot.Nodes) != 5 {
		t.Fatalf("Shenwan snapshot=%#v", swSnapshot)
	}
	assertIndustryNode(t, swSnapshot.Nodes, "X01", "", 1, 0)
	assertIndustryNode(t, swSnapshot.Nodes, "X0101", "X01", 2, 0)
	assertIndustryNode(t, swSnapshot.Nodes, "X010101", "X0101", 3, 0)
	assertIndustryNode(t, swSnapshot.Nodes, "X010101001", "X010101", 4, 0)
	assertIndustryNode(t, swSnapshot.Nodes, "X010101001001", "X010101001", 5, 1)

	for _, snapshot := range []struct {
		name  string
		nodes int
	}{{"tdx", len(tdxSnapshot.Nodes)}, {"sw", len(swSnapshot.Nodes)}} {
		if snapshot.nodes == 0 {
			t.Fatalf("%s snapshot has no nodes", snapshot.name)
		}
	}
}

func TestBuildIndustrySnapshotRejectsUnknownMarket(t *testing.T) {
	assignments := []*protocol.TdxHy{{Market: 9, Code: "600001", TdxHy: "T010101"}}
	names := map[string]string{"T01": "L1", "T0101": "L2", "T010101": "L3"}
	if _, err := buildIndustrySnapshot(context.Background(), assignments, names, industrySpecs[0]); err == nil {
		t.Fatal("expected unsupported market error")
	}
}

func TestBuildIndustrySnapshotRejectsMissingAssignedLeafName(t *testing.T) {
	assignments := []*protocol.TdxHy{{Market: 1, Code: "600001", TdxHy: "T010101"}}
	names := map[string]string{"T01": "L1", "T0101": "L2"}
	if _, err := buildIndustrySnapshot(context.Background(), assignments, names, industrySpecs[0]); err == nil {
		t.Fatal("expected missing leaf name error")
	}
}

func TestNormalizeIndustryMemberSupportsAllAshareMarkets(t *testing.T) {
	for market, want := range map[uint8]string{0: "sz000001", 1: "sh600001", 2: "bj920001"} {
		got, err := normalizeIndustryMember(market, want[2:])
		if err != nil || got != want {
			t.Fatalf("market=%d got=%q err=%v want=%q", market, got, err, want)
		}
	}
}

func assertIndustryNode(t *testing.T, nodes []domain.ClassificationNodeObservation, code, parent string, level, members int) {
	t.Helper()
	for _, node := range nodes {
		if node.SourceNodeCode != code {
			continue
		}
		if node.ParentNodeCode != parent || node.Level != level || len(node.Members) != members {
			t.Fatalf("node %s=%#v, want parent=%q level=%d members=%d", code, node, parent, level, members)
		}
		if members == 1 && node.Members[0].Value != "sh600001" {
			t.Fatalf("node %s member=%#v", code, node.Members)
		}
		return
	}
	t.Fatalf("node %s not found", code)
}
