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

// InstrumentMasterPartition describes one independently verifiable provider
// partition of a security master, such as one exchange. Complete=true grants
// destructive authority only for this partition: identifiers absent from a
// complete partition may eventually be closed, while a failed/incomplete
// partition is frozen and cannot close history.
type InstrumentMasterPartition struct {
	Key          string
	ExchangeMIC  string
	Complete     bool
	Error        string
	Observations []InstrumentObservation
}

// InstrumentMasterSnapshot is one provider's point-in-time security-master
// observation. Observations is the flat union of usable partition rows for
// downstream acquisition loops. Partitions carries the destructive-authority
// boundary. Complete remains as a compatibility/global summary and is true only
// when every expected partition is complete.
type InstrumentMasterSnapshot struct {
	Source       string
	AsOfDate     time.Time
	Complete     bool
	Observations []InstrumentObservation
	Partitions   []InstrumentMasterPartition
}
