package ingest

import (
	"context"
	"database/sql"
	"errors"

	"github.com/yinhm/alphalake/internal/artifact"
	"github.com/yinhm/alphalake/internal/source/bse"
	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

func SyncBSECodeTransitions(ctx context.Context, db *sql.DB, root string, options ReferenceOptions) (ReferenceSummary, error) {
	if options.Script == "" {
		options.Script = bse.Script
	}
	feed := referenceFeed{bse.Source, bse.Dataset, bse.URLs["mapping"], "text/html; charset=utf-8", bse.ParserVersion}
	return syncReference(ctx, db, root, options, feed, func(runID int64, main artifact.Stored) (int64, bool, int, error) {
		paths := map[string]string{"mapping": artifact.Resolve(root, main)}
		ids := map[string]int64{"mapping": main.ArtifactID}
		var releaseID int64
		if options.Offline {
			// 先固定一个发布版本，再按它的证据角色重放，不能混合几个历史版本。
			var mainID int64
			err := db.QueryRowContext(ctx, `WITH latest AS (SELECT release_id FROM meta.dataset_release WHERE source=? AND dataset=? ORDER BY recorded_at DESC,release_id DESC LIMIT 1)
 SELECT r.release_id,a.artifact_id FROM latest r JOIN meta.dataset_release_artifact a USING(release_id) WHERE a.role='data'`, bse.Source, bse.Dataset).Scan(&releaseID, &mainID)
			if err != nil {
				return 0, false, 0, err
			}
			if mainID != main.ArtifactID {
				return 0, false, 0, errors.New("BSE offline release changed during selection")
			}
		}
		for _, role := range []string{"pilot-list", "pilot-start", "rollout"} {
			var stored artifact.Stored
			var err error
			if options.Offline {
				var id int64
				kind := "timing"
				if role == "pilot-list" {
					kind = "publication"
				}
				err = db.QueryRowContext(ctx, `SELECT l.artifact_id FROM meta.dataset_release_artifact l JOIN meta.artifact a USING(artifact_id) WHERE l.release_id=? AND l.role=? AND a.source_locator=?`, releaseID, kind, bse.URLs[role]).Scan(&id)
				if err == nil {
					stored, _, err = artifact.LoadByID(ctx, db, root, id)
				}
			} else {
				proofFeed := feed
				proofFeed.url = bse.URLs[role]
				stored, err = fetchReferenceArtifact(ctx, db, root, options, proofFeed, runID)
			}
			if err != nil {
				return 0, false, 0, err
			}
			paths[role], ids[role] = artifact.Resolve(root, stored), stored.ArtifactID
		}
		snapshot, hash, err := bse.Parse(ctx, defaultPython(options.Python), options.Script, paths)
		if err != nil {
			return 0, false, 0, err
		}
		id, inserted, err := duckstore.PublishBSECodeTransitions(ctx, db, runID, ids, hash, snapshot)
		return id, inserted, len(snapshot.Transitions), err
	})
}
