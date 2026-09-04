package tdx

import (
	"context"
	"testing"

	"github.com/injoyai/tdx/protocol"
)

type fakeBlockClassificationClient struct {
	plain   map[string][]*protocol.Block
	indexed map[string][]*protocol.Block
}

func (f fakeBlockClassificationClient) GetBlockData(file string) ([]*protocol.Block, error) {
	return f.plain[file], nil
}

func (f fakeBlockClassificationClient) GetBlockDataWithIndex(file string) ([]*protocol.Block, error) {
	return f.indexed[file], nil
}

func TestFetchClassificationSnapshotNormalizesConceptMembers(t *testing.T) {
	fake := fakeBlockClassificationClient{indexed: map[string][]*protocol.Block{
		protocol.BlockFileGN: {
			{Name: "机器人", Index: "880500", Codes: []string{"1600519", "0000001", "2920000", "1600519"}},
		},
	}}

	snapshot, err := fetchClassificationSnapshot(context.Background(), fake, ClassificationConcept)
	if err != nil {
		t.Fatalf("fetchClassificationSnapshot() error = %v", err)
	}
	if !snapshot.Complete || snapshot.Taxonomy.Code != ClassificationConcept || snapshot.Taxonomy.Type != "concept" {
		t.Fatalf("snapshot taxonomy = %#v", snapshot)
	}
	if len(snapshot.Nodes) != 1 {
		t.Fatalf("nodes = %d, want 1", len(snapshot.Nodes))
	}
	node := snapshot.Nodes[0]
	if node.SourceNodeCode != "880500" || node.SourceSymbol != "880500" || node.Name != "机器人" {
		t.Fatalf("node = %#v", node)
	}
	want := []string{"sh600519", "sz000001", "bj920000"}
	if len(node.Members) != len(want) {
		t.Fatalf("members = %#v", node.Members)
	}
	for i := range want {
		if node.Members[i].Provider != Provider || node.Members[i].Type != "symbol" || node.Members[i].Value != want[i] {
			t.Fatalf("member %d = %#v, want %q", i, node.Members[i], want[i])
		}
	}
}

func TestFetchClassificationSnapshotUsesExplicitIndexBlockFallbackIdentity(t *testing.T) {
	fake := fakeBlockClassificationClient{plain: map[string][]*protocol.Block{
		protocol.BlockFileZS: {
			{Name: "沪深300", Codes: []string{"1600000"}},
		},
	}}

	snapshot, err := fetchClassificationSnapshot(context.Background(), fake, ClassificationIndexBlock)
	if err != nil {
		t.Fatalf("fetchClassificationSnapshot() error = %v", err)
	}
	if len(snapshot.Nodes) != 1 {
		t.Fatalf("nodes = %d, want 1", len(snapshot.Nodes))
	}
	node := snapshot.Nodes[0]
	if node.SourceNodeCode != protocol.BlockFileZS+":沪深300" || node.SourceSymbol != "" {
		t.Fatalf("fallback node identity = %#v", node)
	}
}

func TestNormalizeBlockMemberRejectsUnknownMarket(t *testing.T) {
	if _, err := normalizeBlockMember("9600519"); err == nil {
		t.Fatal("normalizeBlockMember() expected market flag error")
	}
}
