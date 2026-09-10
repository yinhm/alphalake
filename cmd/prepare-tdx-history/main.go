// 独立研究归档：核验既有下载清单、复用TDX二进制解析，不写标准事实或数据库。
package main

import (
	"crypto/md5"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/yinhm/alphalake/internal/source/tdx/financial"
)

func check(e error) {
	if e != nil {
		panic(e)
	}
}
func hash(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:]) }
func write(path string, b []byte) {
	f, e := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	check(e)
	_, e = f.Write(b)
	check(e)
	check(f.Close())
}
func main() {
	if len(os.Args) != 4 {
		panic("usage: go run ./cmd/prepare-tdx-history study.json source-manifest.json NEW_ARCHIVE_DIR")
	}
	studyRaw, e := os.ReadFile(os.Args[1])
	check(e)
	var study struct {
		Samples []struct {
			Code string `json:"code"`
		} `json:"samples"`
	}
	check(json.Unmarshal(studyRaw, &study))
	codes := map[string]bool{}
	for _, s := range study.Samples {
		codes[s.Code] = true
	}
	if len(codes) == 0 {
		panic("empty sample")
	}
	dir := os.Args[3]
	check(os.Mkdir(dir, 0755)) // 不覆盖已存在的研究归档。
	write(filepath.Join(dir, "study.json"), studyRaw)
	manifestRaw, e := os.ReadFile(os.Args[2])
	check(e)
	var sources []struct {
		File      string `json:"file"`
		Path      string `json:"path"`
		List      string `json:"list"`
		FetchedAt string `json:"fetched_at"`
	}
	check(json.Unmarshal(manifestRaw, &sources))
	if len(sources) == 0 {
		panic("empty source manifest")
	}
	write(filepath.Join(dir, "source-manifest.json"), manifestRaw)
	lists := map[string]string{}
	fields := []int{230, 86, 305, 306, 83, 82, 301, 314, 506, 509, 510, 413}
	artifacts := []any{}
	records := []any{}
	seen := map[string]bool{}
	for _, source := range sources {
		name := source.File
		if seen[name] {
			panic("duplicate source file")
		}
		seen[name] = true
		list, e := os.ReadFile(source.List)
		check(e)
		entries, e := financial.ParseFileList(list)
		check(e)
		sha := hash(list)
		listFile := "list-" + sha + ".txt"
		if _, ok := lists[sha]; !ok {
			write(filepath.Join(dir, listFile), list)
			lists[sha] = listFile
		}
		var entry financial.FileEntry
		for _, v := range entries {
			if v.Filename == name {
				if entry.Filename != "" {
					panic("duplicate list entry")
				}
				entry = v
			}
		}
		if entry.Filename == "" {
			panic("missing source list entry")
		}
		b, e := os.ReadFile(source.Path)
		check(e)
		digest := md5.Sum(b)
		if int64(len(b)) != entry.Size || hex.EncodeToString(digest[:]) != entry.MD5 {
			panic("source size/MD5 mismatch: " + name)
		}
		fetched, e := time.Parse(time.RFC3339Nano, source.FetchedAt)
		check(e)
		if fetched.After(time.Now()) {
			panic("future acquisition time")
		}
		write(filepath.Join(dir, name), b)
		pkg, e := financial.ParsePackage(name, b)
		check(e)
		if pkg.Header.StockCount == 0 {
			panic("empty package: " + name)
		}
		if name != "gpcw"+pkg.Records[0].ReportPeriod.Format("20060102")+".zip" {
			panic("filename/header period differs: " + name)
		}
		artifacts = append(artifacts, map[string]any{"file": name, "sha256": hash(b), "md5": entry.MD5, "size": len(b), "fetched_at": source.FetchedAt, "report_period": pkg.Records[0].ReportPeriod.Format("2006-01-02"), "source": "tdxfin/" + name, "list_sha256": sha})
		for _, r := range pkg.Records {
			if !codes[r.Code] {
				continue
			}
			bits := map[string]uint32{}
			for _, n := range fields {
				if n <= len(r.Fields) {
					bits[fmt.Sprint("FN", n)] = r.Fields[n-1].Bits
				}
			}
			records = append(records, map[string]any{"code": r.Code, "period": r.ReportPeriod.Format("2006-01-02"), "market_marker": r.MarketMarker, "artifact": name, "bits": bits})
		}
	}
	out := map[string]any{"contract_version": "tdx-history-source-v1", "study_sha256": hash(studyRaw), "source_lists": lists, "artifacts": artifacts, "records": records, "boundary": "TDX源证据研究切片；无CNINFO核验、无标准身份/事实认证、无历史修订版本保证"}
	b, e := json.MarshalIndent(out, "", "  ")
	check(e)
	write(filepath.Join(dir, "snapshot.json"), append(b, '\n'))
}
