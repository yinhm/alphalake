package artifact

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"
)

const CNINFODocumentCacheContract = "cninfo-pdf-cache-v1"

var cninfoDocumentURL = regexp.MustCompile(`^https://static\.cninfo\.com\.cn/finalpage/([0-9]{4}-[0-9]{2}-[0-9]{2})/([0-9]+\.(?i:pdf))$`)
var documentSHA = regexp.MustCompile(`^[a-f0-9]{64}$`)

// CNINFODocumentReference is a cache receipt, not a financial fact or a claim
// that the document was independently reviewed. Nil FetchedAt stays unknown.
type CNINFODocumentReference struct {
	Contract  string     `json:"contract"`
	URL       string     `json:"url"`
	SHA256    string     `json:"sha256"`
	LocalPath string     `json:"local_path"`
	FetchedAt *time.Time `json:"fetched_at"`
}

func CNINFODocumentPath(root, sourceURL string) (string, bool) {
	parts := cninfoDocumentURL.FindStringSubmatch(sourceURL)
	if len(parts) != 3 {
		return "", false
	}
	if _, err := time.Parse("2006-01-02", parts[1]); err != nil {
		return "", false
	}
	return filepath.Join(root, "cninfo", "documents", parts[1], parts[2]), true
}

func PublishStoredCNINFODocument(ctx context.Context, db *sql.DB, root, sourceURL string, stored Stored) error {
	if !documentSHA.MatchString(stored.SHA256) {
		return errors.New("invalid stored PDF hash")
	}
	var fetched time.Time
	if err := db.QueryRowContext(ctx, `SELECT fetched_at FROM meta.artifact WHERE artifact_id=?`, stored.ArtifactID).Scan(&fetched); err != nil {
		return err
	}
	local := "cninfo/objects/" + stored.SHA256[:2] + "/" + stored.SHA256 + ".pdf"
	canonical, original := filepath.Join(root, local), Resolve(root, stored)
	if canonical != original {
		if ok, err := fileHasSHA(original, stored.SHA256); err != nil || !ok {
			return errors.New("original CNINFO PDF hash differs")
		}
		from, to := original, canonical
		if valid, err := fileHasSHA(canonical, stored.SHA256); err == nil && valid {
			from, to = canonical, original
		}
		if err := shareDocumentFile(root, from, to); err != nil {
			return err
		}
	}
	return PublishCNINFODocumentReference(root, CNINFODocumentReference{URL: sourceURL, SHA256: stored.SHA256, LocalPath: local, FetchedAt: &fetched})
}

func shareDocumentFile(root, source, target string) error {
	if err := checkDocumentPath(root, source); err != nil {
		return err
	}
	if err := checkDocumentPath(root, target); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	if src, err := os.Stat(source); err != nil {
		return err
	} else if dst, err := os.Stat(target); err == nil && os.SameFile(src, dst) {
		return nil
	}
	pending, err := os.MkdirTemp(filepath.Dir(target), ".pdf-object-link-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(pending)
	if err := os.Link(source, filepath.Join(pending, "source.pdf")); err != nil {
		return err
	}
	return os.Rename(filepath.Join(pending, "source.pdf"), target)
}

// LockCNINFODocument serializes lookup/download/publication for one URL across
// Go and Python, without serializing unrelated document downloads.
func LockCNINFODocument(root, sourceURL string) (func(), error) {
	view, ok := CNINFODocumentPath(root, sourceURL)
	if !ok {
		return nil, errors.New("official CNINFO PDF URL required")
	}
	if err := os.MkdirAll(root, 0o755); err != nil {
		return nil, err
	}
	path := filepath.Join(root, "derived", "pdf-cache", "locks", filepath.Base(filepath.Dir(view)), filepath.Base(view)+".lock")
	if err := checkDocumentPath(root, path); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		return nil, err
	}
	if err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX); err != nil {
		f.Close()
		return nil, err
	}
	return func() { f.Close() }, nil
}

func ReadCNINFODocumentReference(root, sourceURL string) (CNINFODocumentReference, bool, error) {
	var r CNINFODocumentReference
	view, ok := CNINFODocumentPath(root, sourceURL)
	if !ok {
		return r, false, nil
	}
	if err := checkDocumentPath(root, view+".json"); err != nil {
		return r, false, err
	}
	f, err := os.Open(view + ".json")
	if errors.Is(err, os.ErrNotExist) {
		if _, viewErr := os.Lstat(view); !errors.Is(viewErr, os.ErrNotExist) {
			return r, false, errors.New("PDF reference has no provenance receipt")
		}
		return r, false, nil
	}
	if err != nil {
		return r, false, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || info.Size() > 16384 {
		return r, false, errors.New("PDF receipt exceeds size limit")
	}
	decoder := json.NewDecoder(io.LimitReader(f, 16384))
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(&r); err != nil {
		return r, false, err
	}
	if decoder.Decode(new(any)) != io.EOF {
		return r, false, errors.New("trailing PDF receipt JSON")
	}
	if r.Contract != CNINFODocumentCacheContract || r.URL != sourceURL {
		return r, false, errors.New("PDF cache provenance differs from official URL")
	}
	if err = validateDocumentReference(root, r); err != nil {
		return r, false, err
	}
	info, err = os.Lstat(view)
	if err != nil || info.Mode()&os.ModeSymlink == 0 {
		return r, false, errors.New("PDF directory entry must reference canonical object")
	}
	actual, err := filepath.EvalSymlinks(view)
	if err != nil {
		return r, false, err
	}
	canonical, err := filepath.EvalSymlinks(filepath.Join(root, r.LocalPath))
	if err != nil || actual != canonical {
		return r, false, errors.New("PDF directory reference differs from canonical object")
	}
	return r, true, nil
}

func validateDocumentReference(root string, r CNINFODocumentReference) error {
	if !documentSHA.MatchString(r.SHA256) || r.LocalPath != "cninfo/objects/"+r.SHA256[:2]+"/"+r.SHA256+".pdf" {
		return errors.New("PDF cache path/hash is not canonical")
	}
	if r.FetchedAt != nil && (r.FetchedAt.IsZero() || r.FetchedAt.After(time.Now())) {
		return errors.New("valid original PDF retrieval time required")
	}
	path := filepath.Join(root, filepath.FromSlash(r.LocalPath))
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil {
		return err
	}
	resolvedRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		return err
	}
	rel, err := filepath.Rel(resolvedRoot, resolved)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return errors.New("PDF cache leaves workspace")
	}
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 256<<20 {
		return errors.New("PDF cache is not a bounded regular file")
	}
	prefix := make([]byte, 512)
	n, err := f.Read(prefix)
	if err != nil && err != io.EOF {
		return err
	}
	if !bytes.HasPrefix(bytes.TrimSpace(prefix[:n]), []byte("%PDF-")) {
		return errors.New("PDF cache header differs")
	}
	valid, err := fileHasSHA(path, r.SHA256)
	if err != nil || !valid {
		return fmt.Errorf("PDF cache hash differs: %s", r.LocalPath)
	}
	return nil
}

func checkDocumentPath(root, path string) error {
	resolvedRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		return err
	}
	for {
		_, err = os.Lstat(path)
		if err == nil {
			break
		}
		if !errors.Is(err, os.ErrNotExist) || filepath.Dir(path) == path {
			return err
		}
		path = filepath.Dir(path)
	}
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil {
		return err
	}
	resolvedRoot, err = filepath.Abs(resolvedRoot)
	if err != nil {
		return err
	}
	resolved, err = filepath.Abs(resolved)
	if err != nil {
		return err
	}
	rel, err := filepath.Rel(resolvedRoot, resolved)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return errors.New("PDF cache path leaves workspace")
	}
	return nil
}

// PublishCNINFODocumentReference is called with the URL lock held. Cache receipt
// replacement never overwrites an immutable PDF or manufactures an unknown date.
func PublishCNINFODocumentReference(root string, r CNINFODocumentReference) error {
	view, ok := CNINFODocumentPath(root, r.URL)
	if !ok {
		return errors.New("official CNINFO PDF URL required")
	}
	r.Contract = CNINFODocumentCacheContract
	if err := checkDocumentPath(root, view+".json"); err != nil {
		return err
	}
	if err := validateDocumentReference(root, r); err != nil {
		return err
	}
	if prior, exists, err := ReadCNINFODocumentReference(root, r.URL); err == nil && exists && prior.SHA256 == r.SHA256 && prior.FetchedAt != nil {
		if r.FetchedAt == nil || prior.FetchedAt.Before(*r.FetchedAt) {
			r.FetchedAt = prior.FetchedAt
		}
	}
	if err := os.MkdirAll(filepath.Dir(view), 0o755); err != nil {
		return err
	}
	pending, err := os.MkdirTemp(filepath.Dir(view), ".pdf-reference-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(pending)
	link, err := filepath.Rel(filepath.Dir(view), filepath.Join(root, r.LocalPath))
	if err != nil {
		return err
	}
	if err = os.Symlink(link, filepath.Join(pending, "reference.pdf")); err != nil {
		return err
	}
	f, err := os.Create(filepath.Join(pending, "receipt.json"))
	if err != nil {
		return err
	}
	if err = json.NewEncoder(f).Encode(r); err == nil {
		err = f.Sync()
	}
	f.Close()
	if err != nil {
		return err
	}
	if err = os.Rename(filepath.Join(pending, "reference.pdf"), view); err != nil {
		return err
	}
	return os.Rename(filepath.Join(pending, "receipt.json"), view+".json")
}
