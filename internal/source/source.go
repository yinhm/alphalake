package source

import (
	"context"
	"time"
)

type Dataset string

const (
	DatasetInstrument      Dataset = "instrument"
	DatasetDailyOHLCV      Dataset = "daily_ohlcv"
	DatasetCorporateAction Dataset = "corporate_action"
	DatasetClassification  Dataset = "classification"
	DatasetFinancial       Dataset = "financial"
	DatasetFiling          Dataset = "filing"
)

type FetchRequest struct {
	Dataset Dataset
	Start   *time.Time
	End     *time.Time
	Cursor  string
}

type Artifact struct {
	Source        string
	Dataset       Dataset
	SourceLocator string
	FetchedAt     time.Time
	SHA256        string
	ContentLength int64
	MediaType     string
	LocalPath     string
}

type Adapter interface {
	Name() string
	Datasets() []Dataset
	Fetch(context.Context, FetchRequest) ([]Artifact, error)
}
