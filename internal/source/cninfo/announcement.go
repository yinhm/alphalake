package cninfo

import "time"

var chinaDisclosureLocation = time.FixedZone("Asia/Shanghai", 8*60*60)

// announcementAvailability converts the public catalogue's provider milliseconds
// into a disclosure date plus an earliest-safe PIT availability instant. The
// public catalogue establishes a date, not a trustworthy intraday publication
// timestamp, so facts become visible at the next China-calendar-day boundary.
// The original milliseconds remain stored separately as raw provider evidence.
func announcementAvailability(milliseconds int64) (time.Time, time.Time) {
	providerInstant := time.UnixMilli(milliseconds)
	local := providerInstant.In(chinaDisclosureLocation)
	announcementDate := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, time.UTC)
	nextDayLocal := time.Date(local.Year(), local.Month(), local.Day()+1, 0, 0, 0, 0, chinaDisclosureLocation)
	return announcementDate, nextDayLocal.UTC()
}
