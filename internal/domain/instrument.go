package domain

import "time"

type InstrumentType string

const (
	InstrumentUnknown InstrumentType = "unknown"
	InstrumentEquity  InstrumentType = "equity"
	InstrumentETF     InstrumentType = "etf"
	InstrumentIndex   InstrumentType = "index"
	InstrumentFund    InstrumentType = "fund"
	InstrumentBond    InstrumentType = "bond"
)

type InstrumentRef struct {
	InstrumentID int64
	Type         InstrumentType
	ExchangeMIC  string
	Currency     string
	Name         string
	ListDate     *time.Time
	DelistDate   *time.Time
}

type Identifier struct {
	InstrumentID int64
	Provider     string
	Type         string
	Value        string
	ValidFrom    *time.Time
	ValidTo      *time.Time
}

// InstrumentObservation is a provider-neutral observation of an instrument and
// one provider identifier that can resolve it into AlphaLake's canonical master.
type InstrumentObservation struct {
	Instrument InstrumentRef
	Identifier Identifier
}

// InstrumentMasterSnapshot is one provider's point-in-time security-master
// observation. Complete means the provider adapter verified every expected
// partition needed to represent the current universe; only complete snapshots
// may close provider identifiers that disappeared since the previous snapshot.
type InstrumentMasterSnapshot struct {
	Source       string
	AsOfDate     time.Time
	Complete     bool
	Observations []InstrumentObservation
}
