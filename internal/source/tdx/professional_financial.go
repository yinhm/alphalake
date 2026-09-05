package tdx

import (
	"context"
	"crypto/md5"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	tdxfinancial "github.com/yinhm/alphalake/internal/source/tdx/financial"
)

const ProfessionalFinancialListLocator = "tdxfin/gpcw.txt"

const professionalFinancialBaseURL = "http://down.tdx.com.cn:8001/"

type reportFileClient interface {
	GetReportFile(string) ([]byte, error)
}

func (c *Client) ProfessionalFinancialFileList(ctx context.Context) ([]tdxfinancial.FileEntry, []byte, error) {
	if c == nil || c.raw == nil {
		return nil, nil, fmt.Errorf("TDX client is not initialized")
	}
	return fetchProfessionalFinancialFileList(ctx, financialReportFiles{ctx, c.raw, professionalFinancialBaseURL})
}

func (c *Client) ProfessionalFinancialPackage(ctx context.Context, entry tdxfinancial.FileEntry) ([]byte, error) {
	if c == nil || c.raw == nil {
		return nil, fmt.Errorf("TDX client is not initialized")
	}
	return fetchProfessionalFinancialPackage(ctx, financialReportFiles{ctx, c.raw, professionalFinancialBaseURL}, entry)
}

// Some live quotation servers return an empty report-file response for gpcw.
// Use TDX's official download service in that case; size/MD5 checks still run.
type financialReportFiles struct {
	ctx      context.Context
	protocol reportFileClient
	baseURL  string
}

func (f financialReportFiles) GetReportFile(locator string) ([]byte, error) {
	if !strings.HasPrefix(locator, "tdxfin/") || strings.ContainsAny(strings.TrimPrefix(locator, "tdxfin/"), "/\\?#") {
		return nil, fmt.Errorf("invalid financial file locator %q", locator)
	}
	if raw, err := f.protocol.GetReportFile(locator); err == nil && len(raw) > 0 {
		return raw, nil
	}
	ctx, cancel := context.WithTimeout(f.ctx, 45*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, f.baseURL+locator, nil)
	if err != nil {
		return nil, err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("download TDX financial file: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("download TDX financial file %s: %s", locator, resp.Status)
	}
	limit := int64(128 << 20)
	if locator == ProfessionalFinancialListLocator {
		limit = 1 << 20
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, limit+1))
	if err != nil {
		return nil, fmt.Errorf("read TDX financial file: %w", err)
	}
	if len(raw) == 0 || int64(len(raw)) > limit {
		return nil, fmt.Errorf("TDX financial file %s has invalid size %d (limit %d)", locator, len(raw), limit)
	}
	return raw, nil
}

// NormalizeProfessionalFinancialPackage parses one verified raw package into
// provider-neutral records. It preserves the raw six-digit provider code and
// the package's one-byte market marker without assigning unverified semantics
// to that marker or guessing a current exchange from today's code ranges.
// Announcement time remains nil because the gpcw package itself does not
// provide an authoritative announcement timestamp.
func (c *Client) NormalizeProfessionalFinancialPackage(entry tdxfinancial.FileEntry, raw []byte, artifactID int64) ([]domain.ProviderFinancialRecord, error) {
	if artifactID <= 0 {
		return nil, fmt.Errorf("artifact ID must be positive")
	}
	pkg, err := tdxfinancial.ParsePackage(entry.Filename, raw)
	if err != nil {
		return nil, err
	}
	return normalizeProfessionalFinancialRecords(entry, pkg, artifactID), nil
}

func normalizeProfessionalFinancialRecords(entry tdxfinancial.FileEntry, pkg tdxfinancial.Package, artifactID int64) []domain.ProviderFinancialRecord {
	out := make([]domain.ProviderFinancialRecord, 0, len(pkg.Records))
	for _, record := range pkg.Records {
		out = append(out, domain.ProviderFinancialRecord{
			Provider:       Provider,
			ProviderCode:   record.Code,
			MarketMarker:   record.MarketMarker,
			ReportPeriod:   record.ReportPeriod,
			ProviderFields: record.Fields,
			SourceFile:     entry.Filename,
			ArtifactID:     artifactID,
		})
	}
	return out
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
