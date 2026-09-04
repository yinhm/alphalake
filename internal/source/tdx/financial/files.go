package financial

import (
	"bufio"
	"bytes"
	"encoding/hex"
	"errors"
	"fmt"
	"strconv"
	"strings"
)

type FileEntry struct {
	Filename string
	MD5      string
	Size     int64
}

func ParseFileList(data []byte) ([]FileEntry, error) {
	scanner := bufio.NewScanner(bytes.NewReader(data))
	var out []FileEntry
	lineNo := 0
	for scanner.Scan() {
		lineNo++
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		parts := strings.Split(line, ",")
		if len(parts) != 3 {
			return nil, fmt.Errorf("gpcw list line %d: expected 3 comma-separated fields", lineNo)
		}
		entry := FileEntry{
			Filename: strings.TrimSpace(parts[0]),
			MD5:      strings.ToLower(strings.TrimSpace(parts[1])),
		}
		if !validPackageFilename(entry.Filename) {
			return nil, fmt.Errorf("gpcw list line %d: invalid filename %q", lineNo, entry.Filename)
		}
		if len(entry.MD5) != 32 {
			return nil, fmt.Errorf("gpcw list line %d: invalid md5 %q", lineNo, entry.MD5)
		}
		if _, err := hex.DecodeString(entry.MD5); err != nil {
			return nil, fmt.Errorf("gpcw list line %d: invalid md5 %q: %w", lineNo, entry.MD5, err)
		}
		size, err := strconv.ParseInt(strings.TrimSpace(parts[2]), 10, 64)
		if err != nil || size < 0 {
			return nil, fmt.Errorf("gpcw list line %d: invalid size %q", lineNo, strings.TrimSpace(parts[2]))
		}
		entry.Size = size
		out = append(out, entry)
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("scan gpcw file list: %w", err)
	}
	if len(out) == 0 {
		return nil, errors.New("gpcw file list is empty")
	}
	return out, nil
}

func validPackageFilename(name string) bool {
	if len(name) != len("gpcw20000101.zip") || !strings.HasPrefix(name, "gpcw") || !strings.HasSuffix(strings.ToLower(name), ".zip") {
		return false
	}
	for _, r := range name[4:12] {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}
