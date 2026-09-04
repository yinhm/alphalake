package tdx

import (
	"context"
	"testing"

	"github.com/injoyai/tdx/protocol"
)

type fakeIndustryResultClient struct {
	assignments []*protocol.TdxHy
	files       map[string][]byte
}

func (f fakeIndustryResultClient) GetTdxHy() ([]*protocol.TdxHy, error) { return f.assignments, nil }
func (f fakeIndustryResultClient) GetZHBFiles() (map[string][]byte, error) { return f.files, nil }

func TestFetchIndustrySnapshotResultsKeepsShenwanWhenTDXBuildFails(t *testing.T) {
	client := fakeIndustryResultClient{
		assignments: []*protocol.TdxHy{{
			Market: 1, Code: "600001",
			TdxHy: "BAD", // invalid TDX prefix/shape
			SwHy:  "X010101001001",
		}},
		files: map[string][]byte{
			protocol.FileIncon: []byte("X01|SW1\nX0101|SW2\nX010101|SW3\nX010101001|SW4\nX010101001001|SW5\n"),
		},
	}
	results, err := fetchIndustrySnapshotResults(context.Background(), client)
	if err != nil { t.Fatal(err) }
	if len(results) != 2 { t.Fatalf("len(results)=%d, want 2", len(results)) }
	if results[0].Code != ClassificationTDXIndustry || results[0].Error == "" || results[0].Snapshot != nil {
		t.Fatalf("TDX result=%#v, want isolated build failure", results[0])
	}
	if results[1].Code != ClassificationShenwanIndustry || results[1].Error != "" || results[1].Snapshot == nil {
		t.Fatalf("Shenwan result=%#v, want successful snapshot", results[1])
	}
	if len(results[1].Snapshot.Nodes) != 5 {
		t.Fatalf("Shenwan nodes=%d, want 5", len(results[1].Snapshot.Nodes))
	}
}
