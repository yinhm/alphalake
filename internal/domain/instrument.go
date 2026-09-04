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
