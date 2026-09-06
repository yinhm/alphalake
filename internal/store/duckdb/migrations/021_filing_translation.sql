-- 明示英文译本保留公告证据，但不作为中文结构化财务的 PIT 锚点。
-- 与分类器 v3 相同的明确标题标记；升级后 materialize-fundamentals 重建关联及事实。
UPDATE fundamental.filing
SET filing_variant='translation', classifier_version='cninfo-periodic-title-v3'
WHERE source='cninfo' AND report_period IS NOT NULL
  AND (contains(title,'英文版') OR contains(title,'英文译本'));

-- 旧译本及指向它的更正关系失去支持；先清除，目录重放再寻找合格前序。
UPDATE fundamental.filing SET corrects_filing_id=NULL
WHERE filing_variant='translation'
   OR corrects_filing_id IN (SELECT filing_id FROM fundamental.filing WHERE filing_variant='translation');
