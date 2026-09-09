package ingest

import (
	"context"
	"database/sql"
	"errors"
	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/source/capital"
	"github.com/yinhm/alphalake/internal/source/hkex"
	"github.com/yinhm/alphalake/internal/source/proceeds"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func SyncShareClasses(ctx context.Context, db *sql.DB, root, code string, options ReferenceOptions) (ReferenceSummary, error) {
	url := capital.URL(code)
	if url == "" {
		return ReferenceSummary{}, errors.New("share disclosure not yet reviewed for code")
	}
	if options.Script == "" {
		options.Script = capital.Script
	}
	feed := referenceFeed{capital.Source, capital.Dataset + "/" + code, url, "application/pdf", capital.ParserVersion}
	return syncReference(ctx, db, root, options, feed, func(run int64, a artifact.Stored) (int64, bool, int, error) {
		s, hash, err := capital.Parse(ctx, defaultPython(options.Python), options.Script, artifact.Resolve(root, a))
		if err != nil {
			return 0, false, 0, err
		}
		if s.Code != code {
			return 0, false, 0, errors.New("issuer response mismatch")
		}
		id, new, err := duckstore.PublishShareClasses(ctx, db, run, a.ArtifactID, hash, s)
		return id, new, 3 * len(s.Classes), err
	})
}

func SyncHKEXQuote(ctx context.Context, db *sql.DB, root, day string, options ReferenceOptions) (ReferenceSummary, error) {
	url := hkex.URL(day)
	if url == "" {
		return ReferenceSummary{}, errors.New("exact market date required")
	}
	if options.Script == "" {
		options.Script = hkex.Script
	}
	feed := referenceFeed{hkex.Source, hkex.Dataset, url, "text/html; charset=iso-8859-1", hkex.ParserVersion}
	return syncReference(ctx, db, root, options, feed, func(run int64, a artifact.Stored) (int64, bool, int, error) {
		s, h, e := hkex.Parse(ctx, defaultPython(options.Python), options.Script, artifact.Resolve(root, a))
		if e != nil {
			return 0, false, 0, e
		}
		if s.ObservationDate != day {
			return 0, false, 0, errors.New("HKEX returned different market date")
		}
		id, new, e := duckstore.PublishHKEXQuote(ctx, db, run, a.ArtifactID, h, s)
		return id, new, 1, e
	})
}

func SyncEquityProceeds(ctx context.Context, db *sql.DB, root, event string, options ReferenceOptions) (ReferenceSummary, error) {
	url := proceeds.URL(event)
	if url == "" {
		return ReferenceSummary{}, errors.New("reviewed proceeds event required: ipo or greenshoe")
	}
	if options.Script == "" {
		options.Script = proceeds.Script
	}
	feed := referenceFeed{proceeds.Source, proceeds.Dataset + "/" + event, url, "application/pdf", proceeds.ParserVersion}
	return syncReference(ctx, db, root, options, feed, func(run int64, a artifact.Stored) (int64, bool, int, error) {
		s, hash, err := proceeds.Parse(ctx, defaultPython(options.Python), options.Script, artifact.Resolve(root, a))
		if err != nil {
			return 0, false, 0, err
		}
		if s.Event != event {
			return 0, false, 0, errors.New("proceeds event response mismatch")
		}
		id, inserted, err := duckstore.PublishEquityProceeds(ctx, db, run, a.ArtifactID, hash, s)
		return id, inserted, 1, err
	})
}
