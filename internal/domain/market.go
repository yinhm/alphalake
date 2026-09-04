package domain

import "time"

type DailyBar struct {
	InstrumentID int64
	TradeDate    time.Time
	Open         float64
	High         float64
	Low          float64
	Close        float64
	Volume       int64 // canonical shares/units, never TDX hands
	Amount       float64
	UpCount      int64
	DownCount    int64
	Source       string
}

type CorporateAction struct {
	InstrumentID      int64
	ActionDate        time.Time
	ActionType        string
	Source            string
	SourceCategory    int
	SourceRecordID    string
	CashDividendPer10 float64
	RightsPrice       float64
	BonusOrSplitPer10 float64
	RightsPer10       float64
	ScaleFactor       float64
	RawC1             float64
	RawC2             float64
	RawC3             float64
	RawC4             float64
}

// ShareCapital is a point-in-time share-count observation derived only from
// GBBQ categories whose field semantics are known to contain before/after
// float and total share counts.
type ShareCapital struct {
	InstrumentID   int64
	EffectiveDate  time.Time
	FloatShares    int64
	TotalShares    int64
	Source          string
	SourceCategory int
	SourceRecordID  string
}

// CorporateActionObservation is the provider-neutral boundary emitted by a
// source adapter before a provider identifier has been resolved to the
// canonical instrument ID. ShareCapital is optional because not every GBBQ
// category carries trustworthy share-count semantics.
type CorporateActionObservation struct {
	Identifier   Identifier
	Action       CorporateAction
	ShareCapital *ShareCapital
}
