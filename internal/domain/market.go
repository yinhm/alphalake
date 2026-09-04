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
