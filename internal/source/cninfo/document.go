package cninfo

import (
	"errors"
)

// FilingDocumentURL resolves one provider attachment locator to the canonical
// configured CNINFO document host without performing network I/O. The same URL
// is used as immutable artifact source identity before cache lookup.
func (c *Client) FilingDocumentURL(locator string) (string, error) {
	if c == nil {
		return "", errors.New("CNINFO client is nil")
	}
	resolved, err := c.resolveDocumentURL(locator)
	if err != nil {
		return "", err
	}
	return resolved.String(), nil
}
