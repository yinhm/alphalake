// Package macro parses the reviewed NBS and PBC publication tables, not arbitrary HTML.
package macro

import (
	"bytes"
	"fmt"
	"html"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"golang.org/x/text/encoding/simplifiedchinese"
)

var tablePattern = regexp.MustCompile(`(?is)<table\b[^>]*>(.*?)</table\s*>`)
var rowPattern = regexp.MustCompile(`(?is)<tr\b[^>]*>(.*?)</tr\s*>`)
var cellPattern = regexp.MustCompile(`(?is)<t[dh]\b([^>]*)>(.*?)</t[dh]\s*>`)
var tagPattern = regexp.MustCompile(`(?is)<[^>]*>`)
var scriptPattern = regexp.MustCompile(`(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>`)
var spanPattern = regexp.MustCompile(`(?i)(rowspan|colspan)\s*=\s*["']?([0-9]+)`)

type Cell struct {
	Text, Locator string
}
type Table [][]Cell
type Document struct {
	HTML, Text string
	Tables     []Table
	Leads      []string
}

func text(value string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsSpace(r) {
			return -1
		}
		return r
	}, html.UnescapeString(tagPattern.ReplaceAllString(value, "")))
}

func document(raw []byte, tables bool) (Document, error) {
	if len(raw) == 0 || len(raw) > 8<<20 {
		return Document{}, fmt.Errorf("empty/oversized macro page")
	}
	if !utf8.Valid(raw) {
		if !bytes.Contains(bytes.ToLower(raw[:min(len(raw), 3000)]), []byte("charset=gb2312")) && !bytes.Contains(bytes.ToLower(raw[:min(len(raw), 3000)]), []byte("charset=gbk")) {
			return Document{}, fmt.Errorf("unsupported macro encoding")
		}
		decoded, err := simplifiedchinese.GB18030.NewDecoder().Bytes(raw)
		if err != nil || bytes.Contains(decoded, []byte("\xef\xbf\xbd")) {
			return Document{}, fmt.Errorf("invalid GB18030 macro page: %v", err)
		}
		raw = decoded
	}
	d := Document{HTML: string(raw), Text: text(scriptPattern.ReplaceAllString(string(raw), ""))}
	if !tables {
		return d, nil
	}
	for ti, positions := range tablePattern.FindAllStringSubmatchIndex(d.HTML, -1) {
		match := []string{d.HTML[positions[0]:positions[1]], d.HTML[positions[2]:positions[3]]}
		if ti >= 64 || strings.Contains(strings.ToLower(match[1]), "<table") {
			return d, fmt.Errorf("nested/too many macro tables")
		}
		table := Table{}
		pending := map[int]struct {
			cell Cell
			left int
		}{}
		for ri, row := range rowPattern.FindAllStringSubmatch(match[1], -1) {
			if ri >= 2000 {
				return d, fmt.Errorf("too many macro table rows")
			}
			grid := map[int]Cell{}
			for column, p := range pending {
				grid[column] = p.cell
				p.left--
				if p.left == 0 {
					delete(pending, column)
				} else {
					pending[column] = p
				}
			}
			column := 0
			for ci, cell := range cellPattern.FindAllStringSubmatch(row[1], -1) {
				for grid[column].Locator != "" {
					column++
				}
				rows, columns := 1, 1
				for _, span := range spanPattern.FindAllStringSubmatch(cell[1], -1) {
					n, _ := strconv.Atoi(span[2])
					if n < 1 || n > 64 {
						return d, fmt.Errorf("unsupported macro span")
					}
					if strings.EqualFold(span[1], "rowspan") {
						rows = n
					} else {
						columns = n
					}
				}
				if column+columns > 128 {
					return d, fmt.Errorf("too many macro columns")
				}
				c := Cell{Text: text(cell[2]), Locator: fmt.Sprintf("table[%d]/tr[%d]/cell[%d]", ti+1, ri+1, ci+1)}
				for k := range columns {
					target := column + k
					if grid[target].Locator != "" {
						return d, fmt.Errorf("overlapping macro cell")
					}
					grid[target] = c
					if rows > 1 {
						pending[target] = struct {
							cell Cell
							left int
						}{c, rows - 1}
					}
				}
				column += columns
			}
			width := 0
			for k := range grid {
				width = max(width, k+1)
			}
			out := make([]Cell, width)
			for k, c := range grid {
				out[k] = c
			}
			table = append(table, out)
		}
		if len(pending) > 0 {
			return d, fmt.Errorf("incomplete macro rowspan")
		}
		d.Tables = append(d.Tables, table)
		d.Leads = append(d.Leads, text(d.HTML[max(0, positions[0]-3000):positions[0]]))
	}
	if len(d.Tables) == 0 {
		return d, fmt.Errorf("macro publication has no tables")
	}
	return d, nil
}

func rowLabel(row []Cell) string {
	for _, c := range row {
		if c.Text != "" {
			return c.Text
		}
	}
	return ""
}
func headerContains(t Table, needles ...string) bool {
	var s strings.Builder
	for _, row := range t[:min(4, len(t))] {
		for _, cell := range row {
			s.WriteString(cell.Text)
		}
	}
	for _, needle := range needles {
		if !strings.Contains(s.String(), needle) {
			return false
		}
	}
	return true
}
