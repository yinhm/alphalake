-- 不删除历史源事实；在当前证据仍冲突时，估值出口不得回退旧版本假装就绪。
-- 以实际观察到的最新包为准，新版本解决冲突后自动解除；历史截止不见未来修复。
CREATE MACRO fundamental.provider_conflicts_asof(as_of_time) AS TABLE
SELECT * EXCLUDE (observation_rank)
FROM (
 SELECT r.*, a.sha256 AS artifact_sha256, a.fetched_at AS observed_at,
 row_number() OVER (
  PARTITION BY r.source,r.provider_code,r.report_period
  ORDER BY a.fetched_at DESC,r.artifact_id DESC
 ) AS observation_rank
 FROM fundamental.provider_record_resolution r JOIN meta.artifact a USING(artifact_id)
 WHERE a.fetched_at<=CAST(as_of_time AS TIMESTAMPTZ)
)
WHERE observation_rank=1 AND starts_with(reason,'conflicting duplicate provider records:');
