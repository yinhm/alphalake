package tdx

import (
	"context"
	"fmt"
	tdxlib "github.com/injoyai/tdx"
	"github.com/injoyai/tdx/protocol"
)

// Implement the existing narrow source interfaces, routing every network call
// through the same connection policy. No SDK client escapes this package.
type nodeRequests struct {
	owner *Client
	ctx   context.Context
}

func (c *Client) requests(ctx context.Context) nodeRequests { return nodeRequests{c, ctx} }
func (r nodeRequests) GetCodeAll(exchange protocol.Exchange) (*protocol.CodeResp, error) {
	return withNode(r.ctx, r.owner, "codes/"+exchange.String(), func(c *tdxlib.Client) (*protocol.CodeResp, error) {
		v, e := c.GetCodeAll(exchange)
		if e == nil && (v == nil || len(v.List) == 0) {
			e = fmt.Errorf("empty code partition")
		}
		return v, e
	})
}
func (r nodeRequests) GetKlineDayAll(code string) (*protocol.KlineResp, error) {
	return withNode(r.ctx, r.owner, "daily/"+code, func(c *tdxlib.Client) (*protocol.KlineResp, error) { return c.GetKlineDayAll(code) })
}
func (r nodeRequests) GetKlineDayUntil(code string, f func(*protocol.Kline) bool) (*protocol.KlineResp, error) {
	return withNode(r.ctx, r.owner, "daily-since/"+code, func(c *tdxlib.Client) (*protocol.KlineResp, error) { return c.GetKlineDayUntil(code, f) })
}
func (r nodeRequests) GetGbbq(code string) (*protocol.GbbqResp, error) {
	return withNode(r.ctx, r.owner, "gbbq/"+code, func(c *tdxlib.Client) (*protocol.GbbqResp, error) { return c.GetGbbq(code) })
}
func (r nodeRequests) GetBlockData(file string) ([]*protocol.Block, error) {
	return withNode(r.ctx, r.owner, "block/"+file, func(c *tdxlib.Client) ([]*protocol.Block, error) { return c.GetBlockData(file) })
}
func (r nodeRequests) GetBlockDataWithIndex(file string) ([]*protocol.Block, error) {
	return withNode(r.ctx, r.owner, "block-index/"+file, func(c *tdxlib.Client) ([]*protocol.Block, error) { return c.GetBlockDataWithIndex(file) })
}
func (r nodeRequests) GetTdxHy() ([]*protocol.TdxHy, error) {
	return withNode(r.ctx, r.owner, "industry-assignments", func(c *tdxlib.Client) ([]*protocol.TdxHy, error) { return c.GetTdxHy() })
}
func (r nodeRequests) GetZHBFiles() (map[string][]byte, error) {
	return withNode(r.ctx, r.owner, "zhb.zip", func(c *tdxlib.Client) (map[string][]byte, error) { return c.GetZHBFiles() })
}
func (r nodeRequests) GetReportFile(file string) ([]byte, error) {
	return withNode(r.ctx, r.owner, "report/"+file, func(c *tdxlib.Client) ([]byte, error) {
		v, e := c.GetReportFile(file)
		if e == nil && len(v) == 0 {
			e = fmt.Errorf("empty report file")
		}
		return v, e
	})
}
