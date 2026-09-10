// Package bse parses reviewed official securities code transition evidence.
package bse

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"time"

	"github.com/yinhm/alphalake/internal/source/reference"
)

const (
	Source        = "bse"
	Dataset       = "stock-code-transitions-2025-v1"
	ParserVersion = "bse-code-transitions-2025-v1"
	Script        = "internal/source/bse/parse.py"
)

var URLs = map[string]string{
	"mapping":     "https://www.bse.cn/service/code_mapping.html",
	"pilot-list":  "https://www.bse.cn/important_news/200025487.html",
	"pilot-start": "https://www.bse.cn/important_news/200025603.html",
	"rollout":     "https://www.bse.cn/important_news/200026735.html",
}

type Transition struct {
	OldCode    string `json:"old_code"`
	NewCode    string `json:"new_code"`
	SourceName string `json:"source_name"`
	// 保留原文，部分值是精选层挂牌日，不能据此建立北交所身份起始区间。
	SourceListingDate string `json:"source_listing_date"`
	SwitchDate        string `json:"switch_date"`
	SourceRow         int    `json:"source_row"`
}

type Snapshot struct {
	Contract      string            `json:"contract"`
	ParserVersion string            `json:"parser_version"`
	Runtime       string            `json:"runtime"`
	Sources       map[string]string `json:"sources"`
	Transitions   []Transition      `json:"transitions"`
}

func (s Snapshot) Validate() error {
	if s.Contract != ParserVersion || s.ParserVersion != ParserVersion || s.Runtime == "" || len(s.Sources) != len(URLs) || len(s.Transitions) != 248 {
		return errors.New("incomplete or unsupported BSE transition evidence")
	}
	hash := regexp.MustCompile(`^[0-9a-f]{64}$`)
	for role := range URLs {
		if !hash.MatchString(s.Sources[role]) {
			return errors.New("missing BSE evidence digest")
		}
	}
	code := regexp.MustCompile(`^[0-9]{6}$`)
	newCode := regexp.MustCompile(`^920[0-9]{3}$`)
	old, newCodes, names := map[string]bool{}, map[string]bool{}, map[string]bool{}
	pilots := 0
	for i, r := range s.Transitions {
		if _, err := time.Parse("2006/1/2", r.SourceListingDate); err != nil {
			return errors.New("invalid source listing date string")
		}
		if r.SourceRow != i+1 || !code.MatchString(r.OldCode) || !newCode.MatchString(r.NewCode) || r.OldCode == r.NewCode || old[r.OldCode] || newCodes[r.NewCode] || r.SourceName == "" || names[r.SourceName] {
			return errors.New("invalid BSE transition identity or source row")
		}
		if r.SwitchDate == "2025-05-06" {
			pilots++
		} else if r.SwitchDate != "2025-10-09" {
			return errors.New("unsupported BSE switch date")
		}
		old[r.OldCode], newCodes[r.NewCode], names[r.SourceName] = true, true, true
	}
	if pilots != 6 {
		return errors.New("incomplete BSE pilot scope")
	}
	for c := range old {
		if newCodes[c] {
			return errors.New("BSE old/new code sets overlap")
		}
	}
	return nil
}

func Digest(s Snapshot) string {
	raw, _ := json.Marshal(s)
	return fmt.Sprintf("%x", sha256.Sum256(raw))
}

func Parse(ctx context.Context, python, script string, paths map[string]string) (Snapshot, string, error) {
	var s Snapshot
	if len(paths) != len(URLs) {
		return s, "", errors.New("four BSE evidence paths required")
	}
	for role := range URLs {
		if paths[role] == "" {
			return s, "", errors.New("missing BSE evidence path")
		}
	}
	input, err := json.Marshal(paths)
	if err != nil {
		return s, "", err
	}
	digest, err := reference.Parse(ctx, python, script, string(input), nil, &s)
	if err == nil {
		err = s.Validate()
	}
	return s, digest, err
}
