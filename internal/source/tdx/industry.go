package tdx

import (
	"context"
	"fmt"
	"sort"
	"strings"

	"github.com/injoyai/tdx/protocol"
	"github.com/yinhm/alphalake/internal/domain"
)

const (
	ClassificationTDXIndustry     = "tdx_industry"
	ClassificationShenwanIndustry = "tdx_shenwan_industry"
)

type industrySnapshotClient interface {
	GetTdxHy() ([]*protocol.TdxHy, error)
	GetZHBFiles() (map[string][]byte, error)
}

type industrySpec struct {
	code          string
	name          string
	typeName      string
	prefix        byte
	prefixLengths []int
	assignment    func(*protocol.TdxHy) string
}

var industrySpecs = []industrySpec{
	{
		code: ClassificationTDXIndustry,
		name: "TDX Industry",
		typeName: "industry",
		prefix: 'T',
		prefixLengths: []int{3, 5, 7},
		assignment: func(v *protocol.TdxHy) string { return v.TdxHy },
	},
	{
		code: ClassificationShenwanIndustry,
		name: "Shenwan Industry (TDX)",
		typeName: "industry",
		prefix: 'X',
		prefixLengths: []int{3, 5, 7, 10, 13},
		assignment: func(v *protocol.TdxHy) string { return v.SwHy },
	},
}

// IndustrySnapshots fetches the TDX security-industry assignments and the
// shared incon.dat code/name dictionary once, then emits two complete canonical
// classification snapshots: TDX industry and Shenwan industry.
func (c *Client) IndustrySnapshots(ctx context.Context) ([]domain.ClassificationSnapshot, error) {
	if c == nil || c.raw == nil {
		return nil, fmt.Errorf("TDX client is not initialized")
	}
	return fetchIndustrySnapshots(ctx, c.raw)
}

func fetchIndustrySnapshots(ctx context.Context, c industrySnapshotClient) ([]domain.ClassificationSnapshot, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	assignments, err := c.GetTdxHy()
	if err != nil {
		return nil, fmt.Errorf("fetch TDX industry assignments: %w", err)
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	files, err := c.GetZHBFiles()
	if err != nil {
		return nil, fmt.Errorf("fetch TDX classification files: %w", err)
	}
	incon, ok := files[protocol.FileIncon]
	if !ok || len(incon) == 0 {
		return nil, fmt.Errorf("TDX %s is missing from zhb.zip", protocol.FileIncon)
	}
	names := parseInconNames(incon)
	if len(names) == 0 {
		return nil, fmt.Errorf("TDX %s contains no industry names", protocol.FileIncon)
	}

	out := make([]domain.ClassificationSnapshot, 0, len(industrySpecs))
	for _, spec := range industrySpecs {
		snapshot, err := buildIndustrySnapshot(ctx, assignments, names, spec)
		if err != nil {
			return nil, fmt.Errorf("build %s snapshot: %w", spec.code, err)
		}
		out = append(out, snapshot)
	}
	return out, nil
}

// parseInconNames parses incon.dat (GBK text). Lines beginning with # are
// section metadata; data lines are `code|name`.
func parseInconNames(data []byte) map[string]string {
	decoded := string(protocol.UTF8ToGBK(data))
	out := make(map[string]string)
	for _, line := range strings.Split(decoded, "\n") {
		line = strings.TrimSpace(strings.TrimRight(line, "\r"))
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, "|", 2)
		if len(parts) != 2 {
			continue
		}
		code := strings.TrimSpace(parts[0])
		name := strings.TrimSpace(parts[1])
		if code != "" && name != "" {
			out[code] = name
		}
	}
	return out
}

func buildIndustrySnapshot(ctx context.Context, assignments []*protocol.TdxHy, names map[string]string, spec industrySpec) (domain.ClassificationSnapshot, error) {
	taxonomy := domain.ClassificationTaxonomy{Source: Provider, Code: spec.code, Name: spec.name, Type: spec.typeName}
	type nodeBuilder struct {
		code       string
		name       string
		parentCode string
		level      int
		members    map[string]domain.Identifier
	}
	nodes := make(map[string]*nodeBuilder)

	for _, assignment := range assignments {
		if err := ctx.Err(); err != nil {
			return domain.ClassificationSnapshot{}, err
		}
		if assignment == nil {
			continue
		}
		industryCode := strings.TrimSpace(spec.assignment(assignment))
		if industryCode == "" {
			continue
		}
		if industryCode[0] != spec.prefix || !validIndustryCodeLength(len(industryCode), spec.prefixLengths) {
			return domain.ClassificationSnapshot{}, fmt.Errorf("unexpected industry code %q", industryCode)
		}
		for _, r := range industryCode[1:] {
			if r < '0' || r > '9' {
				return domain.ClassificationSnapshot{}, fmt.Errorf("non-numeric industry code %q", industryCode)
			}
		}
		if strings.TrimSpace(names[industryCode]) == "" {
			return domain.ClassificationSnapshot{}, fmt.Errorf("industry code %q has no name in incon.dat", industryCode)
		}

		symbol, err := normalizeIndustryMember(assignment.Market, assignment.Code)
		if err != nil {
			return domain.ClassificationSnapshot{}, fmt.Errorf("normalize industry member market=%d code=%q: %w", assignment.Market, assignment.Code, err)
		}

		parentCode := ""
		for levelIndex, length := range spec.prefixLengths {
			if length > len(industryCode) {
				break
			}
			code := industryCode[:length]
			name := strings.TrimSpace(names[code])
			if name == "" {
				// Historical incon.dat variants may omit an intermediate label.
				// Keep the nearest named ancestor, but require the assigned leaf
				// itself above so membership is never attached to an invented node.
				continue
			}
			node := nodes[code]
			if node == nil {
				node = &nodeBuilder{code: code, name: name, parentCode: parentCode, level: levelIndex + 1, members: make(map[string]domain.Identifier)}
				nodes[code] = node
			} else if node.name != name || node.parentCode != parentCode {
				return domain.ClassificationSnapshot{}, fmt.Errorf("inconsistent hierarchy for industry code %q", code)
			}
			parentCode = code
		}
		leaf := nodes[industryCode]
		if leaf == nil {
			return domain.ClassificationSnapshot{}, fmt.Errorf("assigned industry node %q was not built", industryCode)
		}
		leaf.members[symbol] = domain.Identifier{Provider: Provider, Type: "symbol", Value: symbol}
	}

	codes := make([]string, 0, len(nodes))
	for code := range nodes {
		codes = append(codes, code)
	}
	sort.Strings(codes)
	observations := make([]domain.ClassificationNodeObservation, 0, len(codes))
	for _, code := range codes {
		node := nodes[code]
		memberKeys := make([]string, 0, len(node.members))
		for symbol := range node.members {
			memberKeys = append(memberKeys, symbol)
		}
		sort.Strings(memberKeys)
		members := make([]domain.Identifier, 0, len(memberKeys))
		for _, symbol := range memberKeys {
			members = append(members, node.members[symbol])
		}
		observations = append(observations, domain.ClassificationNodeObservation{
			Taxonomy: taxonomy,
			SourceNodeCode: node.code,
			Name: node.name,
			ParentNodeCode: node.parentCode,
			Level: node.level,
			Members: members,
		})
	}
	return domain.ClassificationSnapshot{Taxonomy: taxonomy, Nodes: observations, Complete: true}, nil
}

func validIndustryCodeLength(length int, levels []int) bool {
	for _, allowed := range levels {
		if length == allowed {
			return true
		}
	}
	return false
}

func normalizeIndustryMember(market uint8, code string) (string, error) {
	code = strings.TrimSpace(code)
	if len(code) != 6 {
		return "", fmt.Errorf("expected 6-digit security code")
	}
	for _, r := range code {
		if r < '0' || r > '9' {
			return "", fmt.Errorf("non-numeric security code")
		}
	}
	var prefix string
	switch market {
	case 0:
		prefix = "sz"
	case 1:
		prefix = "sh"
	case 2:
		prefix = "bj"
	default:
		return "", fmt.Errorf("unsupported TDX market %d", market)
	}
	return prefix + code, nil
}
