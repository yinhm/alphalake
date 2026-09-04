package financial

import (
	"archive/zip"
	"bytes"
	"encoding/binary"
	"math"
	"testing"
)

func TestParseFileList(t *testing.T) {
	entries, err := ParseFileList([]byte("gpcw20260630.zip,0123456789abcdef0123456789abcdef,12345\r\ngpcw20260331.zip,abcdefabcdefabcdefabcdefabcdefab,42\n"))
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 2 || entries[0].Filename != "gpcw20260630.zip" || entries[0].Size != 12345 {
		t.Fatalf("entries=%#v", entries)
	}
}

func TestParsePackageUsesReportSizeForDynamicFieldCountAndPreservesBits(t *testing.T) {
	const fields = 3
	dat := make([]byte, gpcwHeaderSize+gpcwStockItemSize+fields*4)
	binary.LittleEndian.PutUint16(dat[0:2], 1)
	binary.LittleEndian.PutUint32(dat[2:6], 20260630)
	binary.LittleEndian.PutUint16(dat[6:8], 1)
	binary.LittleEndian.PutUint32(dat[12:16], fields*4)
	copy(dat[20:26], []byte("600519"))
	dat[26] = 1
	binary.LittleEndian.PutUint32(dat[27:31], uint32(gpcwHeaderSize+gpcwStockItemSize))
	wantBits := []uint32{
		math.Float32bits(1.5),
		0x80000000, // negative zero must survive losslessly
		0x7fc01234, // NaN payload must survive even though analytical Value is NaN
	}
	for i, bits := range wantBits {
		binary.LittleEndian.PutUint32(dat[31+i*4:31+(i+1)*4], bits)
	}

	var zipBuf bytes.Buffer
	zw := zip.NewWriter(&zipBuf)
	w, err := zw.Create("gpcw20260630.dat")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := w.Write(dat); err != nil {
		t.Fatal(err)
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}

	pkg, err := ParsePackage("gpcw20260630.zip", zipBuf.Bytes())
	if err != nil {
		t.Fatal(err)
	}
	if pkg.Header.ReportSize != fields*4 || len(pkg.Records) != 1 || len(pkg.Records[0].Fields) != fields {
		t.Fatalf("package=%#v", pkg)
	}
	record := pkg.Records[0]
	if record.Code != "600519" || record.MarketMarker != 1 || record.ReportPeriod.Format("2006-01-02") != "2026-06-30" {
		t.Fatalf("record=%#v", record)
	}
	for i, want := range wantBits {
		if record.Fields[i].Bits != want {
			t.Fatalf("field %d bits=%08x want=%08x", i+1, record.Fields[i].Bits, want)
		}
	}
	if record.Fields[0].Value != 1.5 || !math.IsNaN(record.Fields[2].Value) {
		t.Fatalf("values=%#v", record.Fields)
	}
}

func TestParseDatRejectsReportOffsetIntoHeaderTable(t *testing.T) {
	dat := make([]byte, gpcwHeaderSize+gpcwStockItemSize+4)
	binary.LittleEndian.PutUint32(dat[2:6], 20260630)
	binary.LittleEndian.PutUint16(dat[6:8], 1)
	binary.LittleEndian.PutUint32(dat[12:16], 4)
	copy(dat[20:26], []byte("600519"))
	binary.LittleEndian.PutUint32(dat[27:31], 20)
	if _, err := ParseDat("gpcw20260630.zip", "gpcw20260630.dat", dat); err == nil {
		t.Fatal("expected invalid report offset error")
	}
}
