package duckdb

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
)

// enrichFinancialSourceBits verifies the immutable package once per revision;
// temporary locator columns never escape the source evidence export.
func enrichFinancialSourceBits(root string, raw string) (string, error) {
	var rows []map[string]json.RawMessage
	if err := json.Unmarshal([]byte(raw), &rows); err != nil {
		return "", err
	}
	if len(rows) == 0 {
		return raw, nil
	}
	var err error
	groups := map[string][]int{}
	for i, r := range rows {
		groups[string(r["artifact_sha256"])] = append(groups[string(r["artifact_sha256"])], i)
	}
	for _, indices := range groups {
		first := rows[indices[0]]
		var path, name, hash string
		var size int64
		for key, dest := range map[string]*string{"local_path": &path, "source_locator": &name, "artifact_sha256": &hash} {
			if err = json.Unmarshal(first[key], dest); err != nil {
				return "", err
			}
		}
		if err = json.Unmarshal(first["content_length"], &size); err != nil {
			return "", err
		}
		pkg, e := ReadFinancialArchive(root, path, name, hash, size)
		if e != nil {
			return "", e
		}
		for _, i := range indices {
			r := rows[i]
			var ordinal int
			var code, field string
			if err = json.Unmarshal(r["source_row"], &ordinal); err != nil {
				return "", err
			}
			if err = json.Unmarshal(r["code"], &code); err != nil {
				return "", err
			}
			if err = json.Unmarshal(r["source_provider_field"], &field); err != nil {
				return "", err
			}
			n, e := strconv.Atoi(strings.TrimPrefix(field, "FN"))
			if e != nil || !sourcePositionName.MatchString(field) || ordinal < 1 || ordinal > len(pkg.Records) || pkg.Records[ordinal-1].Code != code || n < 1 || n > len(pkg.Records[ordinal-1].Fields) {
				return "", fmt.Errorf("invalid source evidence locator")
			}
			r["bits"], _ = json.Marshal(pkg.Records[ordinal-1].Fields[n-1].Bits)
			for _, key := range []string{"source_row", "local_path", "source_locator", "content_length"} {
				delete(r, key)
			}
		}
	}
	encoded, err := json.Marshal(rows)
	return string(encoded), err
}
