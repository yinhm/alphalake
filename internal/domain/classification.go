package domain

// ClassificationTaxonomy describes one provider classification family. Code is
// stable within the provider adapter and is used to resolve the canonical
// taxonomy row.
type ClassificationTaxonomy struct {
	Source string
	Code   string
	Name   string
	Type   string
}

// ClassificationNodeObservation is one provider node plus its current member
// identifiers. Canonical taxonomy/node/instrument IDs are deliberately resolved
// later by the store/ingest layer.
type ClassificationNodeObservation struct {
	Taxonomy      ClassificationTaxonomy
	SourceNodeCode string
	Name          string
	ParentNodeCode string
	Level         int
	SourceSymbol  string
	Members       []Identifier
}

// ClassificationSnapshot is a complete current observation of one provider
// taxonomy at a point in time. Complete=true means absence of a previously open
// membership can safely be interpreted as removal when temporal diffing it.
type ClassificationSnapshot struct {
	Taxonomy ClassificationTaxonomy
	Nodes    []ClassificationNodeObservation
	Complete bool
}
