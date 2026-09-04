package tdx

import (
	"context"
	"fmt"
	"strings"

	"github.com/injoyai/tdx/protocol"
	"github.com/yinhm/alphalake/internal/domain"
)

const (
	ClassificationConcept    = "tdx_concept"
	ClassificationStyleRegion = "tdx_style_region"
	ClassificationIndexBlock = "tdx_index_block"
)

type blockClassificationSpec struct {
	code      string
	name      string
	typeName  string
	file      string
	withIndex bool
}

var blockClassificationSpecs = []blockClassificationSpec{
	{code: ClassificationConcept, name: "TDX Concept", typeName: "concept", file: protocol.BlockFileGN, withIndex: true},
	{code: ClassificationStyleRegion, name: "TDX Style / Region", typeName: "style_region", file: protocol.BlockFileFG, withIndex: true},
	{code: ClassificationIndexBlock, name: "TDX Index Block", typeName: "index", file: protocol.BlockFileZS, withIndex: false},
}

type blockClassificationClient interface {
	GetBlockData(file string) ([]*protocol.Block, error)
	GetBlockDataWithIndex(file string) ([]*protocol.Block, error)
}

func (c *Client) ClassificationFamilies() []string {
	families := make([]string, len(blockClassificationSpecs))
	for i, spec := range blockClassificationSpecs {
		families[i] = spec.code
	}
	return families
}

// ClassificationSnapshot fetches one complete TDX block family. A successful
// return is marked Complete=true; callers must not infer removals from an error.
func (c *Client) ClassificationSnapshot(ctx context.Context, family string) (domain.ClassificationSnapshot, error) {
	if c == nil || c.raw == nil {
		return domain.ClassificationSnapshot{}, fmt.Errorf("TDX client is not initialized")
	}
	return fetchClassificationSnapshot(ctx, c.raw, family)
}

func fetchClassificationSnapshot(ctx context.Context, c blockClassificationClient, family string) (domain.ClassificationSnapshot, error) {
	if err := ctx.Err(); err != nil {
		return domain.ClassificationSnapshot{}, err
	}
	spec, ok := classificationSpec(family)
	if !ok {
		return domain.ClassificationSnapshot{}, fmt.Errorf("unsupported TDX classification family %q", family)
	}

	var blocks []*protocol.Block
	var err error
	if spec.withIndex {
		blocks, err = c.GetBlockDataWithIndex(spec.file)
	} else {
		blocks, err = c.GetBlockData(spec.file)
	}
	if err != nil {
		return domain.ClassificationSnapshot{}, fmt.Errorf("fetch TDX classification %s: %w", family, err)
	}

	taxonomy := domain.ClassificationTaxonomy{Source: Provider, Code: spec.code, Name: spec.name, Type: spec.typeName}
	nodes := make([]domain.ClassificationNodeObservation, 0, len(blocks))
	for _, block := range blocks {
		if err := ctx.Err(); err != nil {
			return domain.ClassificationSnapshot{}, err
		}
		if block == nil || strings.TrimSpace(block.Name) == "" {
			continue
		}
		sourceNodeCode := strings.TrimSpace(block.Index)
		if sourceNodeCode == "" {
			// block_zs.dat has no stable index identifier in the source file.
			// Namespace the provider name with the file so this fallback is
			// explicit and cannot collide with another TDX taxonomy.
			sourceNodeCode = spec.file + ":" + strings.TrimSpace(block.Name)
		}

		members := make([]domain.Identifier, 0, len(block.Codes))
		seen := make(map[string]struct{}, len(block.Codes))
		for _, raw := range block.Codes {
			symbol, err := normalizeBlockMember(raw)
			if err != nil {
				return domain.ClassificationSnapshot{}, fmt.Errorf("normalize TDX block %q member %q: %w", block.Name, raw, err)
			}
			if _, exists := seen[symbol]; exists {
				continue
			}
			seen[symbol] = struct{}{}
			members = append(members, domain.Identifier{Provider: Provider, Type: "symbol", Value: symbol})
		}

		nodes = append(nodes, domain.ClassificationNodeObservation{
			Taxonomy: taxonomy,
			SourceNodeCode: sourceNodeCode,
			Name: strings.TrimSpace(block.Name),
			Level: 1,
			SourceSymbol: strings.TrimSpace(block.Index),
			Members: members,
		})
	}

	return domain.ClassificationSnapshot{Taxonomy: taxonomy, Nodes: nodes, Complete: true}, nil
}

func classificationSpec(family string) (blockClassificationSpec, bool) {
	for _, spec := range blockClassificationSpecs {
		if spec.code == family {
			return spec, true
		}
	}
	return blockClassificationSpec{}, false
}

// TDX block members are seven characters: market flag + six-digit code.
// Modern TDX uses 0=Shenzhen, 1=Shanghai, 2=Beijing.
func normalizeBlockMember(raw string) (string, error) {
	raw = strings.TrimSpace(raw)
	if len(raw) != 7 {
		return "", fmt.Errorf("expected 7 characters")
	}
	var prefix string
	switch raw[0] {
	case '0':
		prefix = "sz"
	case '1':
		prefix = "sh"
	case '2':
		prefix = "bj"
	default:
		return "", fmt.Errorf("unsupported market flag %q", raw[0])
	}
	for _, r := range raw[1:] {
		if r < '0' || r > '9' {
			return "", fmt.Errorf("non-numeric security code")
		}
	}
	return prefix + raw[1:], nil
}
