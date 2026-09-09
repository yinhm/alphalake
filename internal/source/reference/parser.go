// Package reference shares the bounded Python boundary for reviewed reference feeds.
package reference

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"time"
)

// Parse executes a frozen copy of the parser and its explicit local helpers.
// With no helpers the digest remains SHA256(script), preserving existing releases.
func Parse(ctx context.Context, python, script, raw string, helpers []string, out any) (string, error) {
	code, err := os.ReadFile(script)
	if err != nil {
		return "", err
	}
	h := sha256.New()
	h.Write(code)
	var cmd *exec.Cmd
	if len(helpers) == 0 {
		cmd = exec.CommandContext(ctx, python, "-c", string(code), raw)
	} else {
		dir, err := os.MkdirTemp("", "alphalake-parser-*")
		if err != nil {
			return "", err
		}
		defer os.RemoveAll(dir)
		if err := os.WriteFile(filepath.Join(dir, filepath.Base(script)), code, 0600); err != nil {
			return "", err
		}
		for _, name := range helpers {
			if name != filepath.Base(name) || name == filepath.Base(script) {
				return "", errors.New("invalid parser helper")
			}
			content, err := os.ReadFile(filepath.Join(filepath.Dir(script), name))
			if err != nil {
				return "", err
			}
			h.Write([]byte("\n" + name + "\n"))
			h.Write(content)
			if err := os.WriteFile(filepath.Join(dir, name), content, 0600); err != nil {
				return "", err
			}
		}
		cmd = exec.CommandContext(ctx, python, filepath.Join(dir, filepath.Base(script)), raw)
	}
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	body, err := cmd.Output()
	if err != nil {
		return "", fmt.Errorf("reference parser: %w: %s", err, stderr.String())
	}
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.DisallowUnknownFields()
	if err := dec.Decode(out); err != nil {
		return "", err
	}
	if err := dec.Decode(new(any)); err != io.EOF {
		return "", errors.New("trailing parser output")
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

type Header struct {
	Contract        string `json:"contract"`
	ObservationDate string `json:"observation_date"`
	SHA256          string `json:"sha256"`
	ParserVersion   string `json:"parser_version"`
	Runtime         string `json:"runtime"`
}

func (h Header) Validate(contract, version string) error {
	date, err := time.Parse("2006-01-02", h.ObservationDate)
	if err != nil || date.After(time.Now().UTC()) || h.Contract != contract || h.ParserVersion != version || h.Runtime == "" || !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(h.SHA256) {
		return errors.New("invalid reference header")
	}
	return nil
}

// Decimal12 checks canonical scale and source conversion within half a last digit.
func Decimal12(raw, value string, divisor int64) error {
	if !regexp.MustCompile(`^-?[0-9]+\.[0-9]{12}$`).MatchString(value) {
		return errors.New("noncanonical reference decimal")
	}
	r, ok := new(big.Rat).SetString(raw)
	v, vok := new(big.Rat).SetString(value)
	if !ok || !vok || divisor <= 0 {
		return errors.New("invalid reference number")
	}
	r.Quo(r, big.NewRat(divisor, 1))
	r.Sub(r, v)
	r.Abs(r)
	if r.Cmp(big.NewRat(1, 2000000000000)) > 0 {
		return errors.New("raw/standard precision mismatch")
	}
	return nil
}
