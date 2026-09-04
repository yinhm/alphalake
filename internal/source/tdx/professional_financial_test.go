package tdx

import (
	"context"
	"crypto/md5"
	"encoding/hex"
	"testing"

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

func md5Sum(v []byte) []byte {
	sum := md5.Sum(v)
	return sum[:]
}
