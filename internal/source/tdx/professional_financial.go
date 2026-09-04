package tdx

import (
	"context"
	"crypto/md5"
	"encoding/hex"
	"fmt"
	"strings"

	tdxfinancial "github.com/yinhm/alphalake/internal/source/tdx/financial"
)

const ProfessionalFinancialListLocator = "tdxfin/gpcw.txt"

type reportFileClient interface {
	GetReportFile(string) ([]byte, error)
}

func (c *Client) ProfessionalFinancialFileList(ctx context.Context) ([]tdxfinancial.FileEntry, []byte, error) {
	if c == nil || c.raw == nil {
		return nil, nil, fmt.Errorf("TDX client is not initialized")
	}
	return fetchProfessionalFinancialFileList(ctx, c.raw)
}

func (c *Client) ProfessionalFinancialPackage(ctx context.Context, entry tdxfinancial.FileEntry) ([]byte, error) {
	if c == nil || c.raw == nil {
		return nil, fmt.Errorf("TDX client is not initialized")
	}
	return fetchProfessionalFinancialPackage(ctx, c.raw, entry)
}

func fetchProfessionalFinancialFileList(ctx context.Context, c reportFileClient) ([]tdxfinancial.FileEntry, []byte, error) {
	if err := ctx.Err(); err != nil {
		return nil, nil, err
	}
	raw, err := c.GetReportFile(ProfessionalFinancialListLocator)
	if err != nil {
		return nil, nil, fmt.Errorf("fetch TDX professional financial file list: %w", err)
	}
	if err := ctx.Err(); err != nil {
		return nil, nil, err
	}
	entries, err := tdxfinancial.ParseFileList(raw)
	if err != nil {
		return nil, nil, fmt.Errorf("parse TDX professional financial file list: %w", err)
	}
	return entries, raw, nil
}

func fetchProfessionalFinancialPackage(ctx context.Context, c reportFileClient, entry tdxfinancial.FileEntry) ([]byte, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	locator := "tdxfin/" + strings.TrimSpace(entry.Filename)
	raw, err := c.GetReportFile(locator)
	if err != nil {
		return nil, fmt.Errorf("fetch TDX professional financial package %s: %w", entry.Filename, err)
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if entry.Size > 0 && int64(len(raw)) != entry.Size {
		return nil, fmt.Errorf("TDX professional financial package %s size=%d, want %d", entry.Filename, len(raw), entry.Size)
	}
	sum := md5.Sum(raw)
	got := hex.EncodeToString(sum[:])
	if entry.MD5 != "" && !strings.EqualFold(got, entry.MD5) {
		return nil, fmt.Errorf("TDX professional financial package %s md5=%s, want %s", entry.Filename, got, entry.MD5)
	}
	return raw, nil
}
