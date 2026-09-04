package tdx

import (
	"context"
	"crypto/md5"
	"encoding/hex"
	"testing"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
	tdxfinancial "github.com/yinhm/alphalake/internal/source/tdx/financial"
)

type fakeReportFiles map[string][]byte

func (f fakeReportFiles) GetReportFile(name string) ([]byte, error) {
	return append([]byte(nil), f[name]...), nil
}

func TestFetchProfessionalFinancialListAndVerifiedPackage(t *testing.T) {
	pkg := []byte("verified zip bytes")
	sum := md5.Sum(pkg)
	md5hex := hex.EncodeToString(sum[:])
	manifest := []byte("gpcw20260630.zip," + md5hex + ",18\n")
	files := fakeReportFiles{
		ProfessionalFinancialListLocator: manifest,
		"tdxfin/gpcw20260630.zip":      pkg,
	}
	entries, raw, err := fetchProfessionalFinancialFileList(context.Background(), files)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || string(raw) != string(manifest) {
		t.Fatalf("entries/raw=%#v/%q", entries, raw)
	}
	got, err := fetchProfessionalFinancialPackage(context.Background(), files, entries[0])
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(pkg) {
		t.Fatalf("package=%q", got)
	}
}

func TestFetchProfessionalFinancialPackageRejectsIntegrityMismatch(t *testing.T) {
	entry := tdxfinancial.FileEntry{
		Filename: "gpcw20260630.zip",
		MD5:      "00000000000000000000000000000000",
		Size:     3,
	}
	files := fakeReportFiles{"tdxfin/gpcw20260630.zip": []byte("bad")}
	if _, err := fetchProfessionalFinancialPackage(context.Background(), files, entry); err == nil {
		t.Fatal("expected MD5 mismatch error")
	}

	entry.MD5 = hex.EncodeToString(md5Sum([]byte("bad")))
	entry.Size = 4
	if _, err := fetchProfessionalFinancialPackage(context.Background(), files, entry); err == nil {
		t.Fatal("expected size mismatch error")
	}
}

func TestNormalizeProfessionalFinancialRecordsPreservesLegacyRawCodes(t *testing.T) {
	period := time.Date(2005, 6, 30, 0, 0, 0, 0, time.UTC)
	codes := []string{"900901", "200002", "430001", "830001", "870001"}
	pkg := tdxfinancial.Package{Records: make([]tdxfinancial.Record, 0, len(codes))}
	for i, code := range codes {
		pkg.Records = append(pkg.Records, tdxfinancial.Record{
			Code: code, MarketMarker: byte(i + 1), ReportPeriod: period,
			Fields: []domain.ProviderFloat32{{Bits: 0x3f800000, Value: 1}},
		})
	}
	entry := tdxfinancial.FileEntry{Filename: "gpcw20050630.zip"}
	records := normalizeProfessionalFinancialRecords(entry, pkg, 42)
	if len(records) != len(codes) {
		t.Fatalf("records=%d, want %d", len(records), len(codes))
	}
	for i, record := range records {
		if record.Provider != Provider || record.ProviderCode != codes[i] || record.MarketMarker != byte(i+1) {
			t.Fatalf("record %d=%#v", i, record)
		}
		if record.InstrumentID != 0 {
			t.Fatalf("record %d guessed canonical instrument %d", i, record.InstrumentID)
		}
	}
}

func md5Sum(v []byte) []byte {
	sum := md5.Sum(v)
	return sum[:]
}
