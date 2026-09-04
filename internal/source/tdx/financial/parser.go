package financial

import (
	"archive/zip"
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"math"
	"path/filepath"
	"strings"
	"time"

	"github.com/yinhm/alphalake/internal/domain"
)

const (
	gpcwHeaderSize    = 20
	gpcwStockItemSize = 11
	maxDatBytes       = 512 << 20
	maxFieldCount     = 4096
)

type PackageHeader struct {
	VersionOrMarker uint16
	ReportDateRaw   uint32
	StockCount      uint16
	Unknown1        uint32
	ReportSize      uint32
	Unknown2        uint32
}

type Record struct {
	Code         string
	MarketMarker byte
	ReportPeriod time.Time
	Fields       []domain.ProviderFloat32 // index 0 is FN1
}

type Package struct {
	Filename string
	DatName  string
	Header   PackageHeader
	Records  []Record
}

func ParsePackage(filename string, rawZip []byte) (Package, error) {
	var out Package
	filename = strings.TrimSpace(filename)
	if !validPackageFilename(filename) {
		return out, fmt.Errorf("invalid gpcw package filename %q", filename)
	}
	zr, err := zip.NewReader(bytes.NewReader(rawZip), int64(len(rawZip)))
	if err != nil {
		return out, fmt.Errorf("open gpcw zip %q: %w", filename, err)
	}
	var dat *zip.File
	for _, f := range zr.File {
		if strings.EqualFold(filepath.Ext(f.Name), ".dat") {
			if dat != nil {
				return out, fmt.Errorf("gpcw zip %q contains multiple .dat files", filename)
			}
			dat = f
		}
	}
	if dat == nil {
		return out, fmt.Errorf("gpcw zip %q contains no .dat file", filename)
	}
	rc, err := dat.Open()
	if err != nil {
		return out, fmt.Errorf("open gpcw dat %q: %w", dat.Name, err)
	}
	defer rc.Close()
	data, err := io.ReadAll(io.LimitReader(rc, maxDatBytes+1))
	if err != nil {
		return out, fmt.Errorf("read gpcw dat %q: %w", dat.Name, err)
	}
	if len(data) > maxDatBytes {
		return out, fmt.Errorf("gpcw dat %q exceeds %d bytes", dat.Name, maxDatBytes)
	}
	out, err = ParseDat(filename, dat.Name, data)
	if err != nil {
		return Package{}, err
	}
	return out, nil
}

func ParseDat(filename, datName string, data []byte) (Package, error) {
	var out Package
	if len(data) < gpcwHeaderSize {
		return out, errors.New("gpcw dat is shorter than header")
	}
	h := PackageHeader{
		VersionOrMarker: binary.LittleEndian.Uint16(data[0:2]),
		ReportDateRaw:   binary.LittleEndian.Uint32(data[2:6]),
		StockCount:      binary.LittleEndian.Uint16(data[6:8]),
		Unknown1:        binary.LittleEndian.Uint32(data[8:12]),
		ReportSize:      binary.LittleEndian.Uint32(data[12:16]),
		Unknown2:        binary.LittleEndian.Uint32(data[16:20]),
	}
	if h.ReportSize == 0 || h.ReportSize%4 != 0 {
		return out, fmt.Errorf("invalid gpcw report_size %d", h.ReportSize)
	}
	fieldCount := int(h.ReportSize / 4)
	if fieldCount <= 0 || fieldCount > maxFieldCount {
		return out, fmt.Errorf("invalid gpcw field count %d", fieldCount)
	}
	reportPeriod, err := parseYYYYMMDD(h.ReportDateRaw)
	if err != nil {
		return out, fmt.Errorf("invalid gpcw report date %d: %w", h.ReportDateRaw, err)
	}
	headerTableEnd := gpcwHeaderSize + int(h.StockCount)*gpcwStockItemSize
	if headerTableEnd > len(data) {
		return out, fmt.Errorf("gpcw stock table exceeds dat size: need %d, have %d", headerTableEnd, len(data))
	}

	out = Package{Filename: filename, DatName: datName, Header: h, Records: make([]Record, 0, h.StockCount)}
	for i := 0; i < int(h.StockCount); i++ {
		base := gpcwHeaderSize + i*gpcwStockItemSize
		codeBytes := data[base : base+6]
		code := strings.TrimSpace(string(bytes.TrimRight(codeBytes, "\x00")))
		if !sixDigitCode(code) {
			return Package{}, fmt.Errorf("gpcw stock %d has invalid code %q", i, code)
		}
		marker := data[base+6]
		offset := int(binary.LittleEndian.Uint32(data[base+7 : base+11]))
		if offset < headerTableEnd || offset+int(h.ReportSize) > len(data) {
			return Package{}, fmt.Errorf("gpcw stock %s has invalid report offset %d", code, offset)
		}
		fields := make([]domain.ProviderFloat32, fieldCount)
		for j := 0; j < fieldCount; j++ {
			bits := binary.LittleEndian.Uint32(data[offset+j*4 : offset+(j+1)*4])
			fields[j] = domain.ProviderFloat32{Bits: bits, Value: float64(math.Float32frombits(bits))}
		}
		out.Records = append(out.Records, Record{
			Code: code, MarketMarker: marker, ReportPeriod: reportPeriod, Fields: fields,
		})
	}
	return out, nil
}

func parseYYYYMMDD(v uint32) (time.Time, error) {
	y := int(v / 10000)
	m := time.Month((v / 100) % 100)
	d := int(v % 100)
	if y < 1900 || y > 2200 || m < 1 || m > 12 || d < 1 || d > 31 {
		return time.Time{}, errors.New("date components out of range")
	}
	t := time.Date(y, m, d, 0, 0, 0, 0, time.UTC)
	if t.Year() != y || t.Month() != m || t.Day() != d {
		return time.Time{}, errors.New("invalid calendar date")
	}
	return t, nil
}

func sixDigitCode(code string) bool {
	if len(code) != 6 {
		return false
	}
	for _, r := range code {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}
