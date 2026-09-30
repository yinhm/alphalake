"""当前四国输入的可重建性、源金额、历史保留及拒绝边界。"""
from copy import deepcopy
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path

import pytest
from data_sources.alphalake import AlphaLakeRequest
from data_sources.damodaran_parsers.country_risk_parser import alphalake_country_snapshot
from tools import rebuild_method_closure_references as rebuild_tool

ROOT = Path(__file__).resolve().parents[3]
CURRENT = ROOT/'valuation/research/current-references-20260930'


def read(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == '.gz' else raw)


def test_current_country_inputs_preserve_all_other_evidence():
    receipt = read(CURRENT/'receipt.json')
    assert receipt['purpose'] == 'current_four_country_regression_inputs_not_historical_publications'
    assert len(receipt['records']) == 2
    for path_key in ('workbook', 'parser'):
        assert hashlib.sha256((ROOT/receipt[path_key]).read_bytes()).hexdigest() == receipt[path_key+'_sha256']
    packet = alphalake_country_snapshot(ROOT/receipt['workbook'])
    expected_rows = {(r['subject_code'], r['metric_code']):r for r in packet['observations']}
    for entry in receipt['records']:
        source, target = ROOT/entry['source'], CURRENT/entry['file']
        assert hashlib.sha256(source.read_bytes()).hexdigest() == entry['source_sha256']
        assert hashlib.sha256(target.read_bytes()).hexdigest() == entry['sha256']
        old, current = read(source), read(target)
        with pytest.raises(ValueError, match='incomplete/duplicate reference scope'):
            AlphaLakeRequest.model_validate(old)
        AlphaLakeRequest.model_validate(current)
        before, after = (r['wacc_binding']['references'] for r in (old, current))
        # 除国家参考快照外，完整请求（含财务、资本/预测政策和其他参考）逐项不变。
        restored = deepcopy(current)
        restored['wacc_binding']['references'] = before
        assert restored == old
        assert {k:v for k,v in before.items() if k not in ('releases','country_risk')} == {
            k:v for k,v in after.items() if k not in ('releases','country_risk')}
        assert before['recorded_cutoff'] is after['recorded_cutoff'] is None
        old_release, = [r for r in before['releases'] if r['dataset'].startswith('country-risk')]
        new_release, = [r for r in after['releases'] if r['dataset'].startswith('country-risk')]
        assert [r for r in before['releases'] if r != old_release] == [r for r in after['releases'] if r != new_release]
        changed = {'release_id','dataset','parser_version','normalization_version','content_key','recorded_at'}
        assert {k:v for k,v in old_release.items() if k not in changed} == {k:v for k,v in new_release.items() if k not in changed}
        assert new_release['dataset'] == 'country-risk-rating-v2'
        assert new_release['recorded_at'] == receipt['recorded_at']
        assert datetime.fromisoformat(new_release['recorded_at']) > datetime.fromisoformat(before['information_as_of'])
        assert new_release['normalization_version'] == f"country-risk-decimal12-v1;{receipt['parser_sha256']};{receipt['runtime']}"
        key = '\n'.join((receipt['workbook_sha256'], packet['parser_version'], new_release['normalization_version']))
        assert new_release['content_key'] == hashlib.sha256(key.encode()).hexdigest()
        assert len(before['country_risk']) == 10 and len(after['country_risk']) == 13
        assert before['country_risk'] == [dict(row, release_id=old_release['release_id'])
                                         for row in after['country_risk'] if row['subject_code'] != 'IL']
        for row in after['country_risk']:
            expected = expected_rows[(row['subject_code'], row['metric_code'])]
            assert {k:row[k] for k in expected} == expected
        assert {r['source_locator'] for r in after['country_risk'] if r['subject_code'] == 'IL'} == {
            'ERPs by country!D78', 'ERPs by country!E78', 'ERPs by country!F78'}
        for mutation in ('missing_israel', 'duplicate_israel', 'raw_value', 'old_dataset'):
            bad = deepcopy(current)
            references = bad['wacc_binding']['references']
            if mutation == 'missing_israel':
                references['country_risk'] = [r for r in references['country_risk'] if r['subject_code'] != 'IL']
            elif mutation == 'duplicate_israel':
                references['country_risk'][-1] = deepcopy(references['country_risk'][-2])
            elif mutation == 'raw_value':
                references['country_risk'][-1]['raw_value'] = '0.99'
            else:
                next(r for r in references['releases'] if r['dataset'] == 'country-risk-rating-v2')['dataset'] = old_release['dataset']
            with pytest.raises(ValueError):
                AlphaLakeRequest.model_validate(bad)


def test_explicit_reference_rebuild_is_repeatable_and_never_overwrites(tmp_path):
    stamp = read(CURRENT/'receipt.json')['recorded_at']
    first, second = tmp_path/'first', tmp_path/'second'
    assert rebuild_tool.rebuild(first, stamp) == rebuild_tool.rebuild(second, stamp)
    assert {p.relative_to(first):p.read_bytes() for p in first.rglob('*') if p.is_file()} == {
        p.relative_to(second):p.read_bytes() for p in second.rglob('*') if p.is_file()}
    with pytest.raises(FileExistsError):
        rebuild_tool.rebuild(first, stamp)
    with pytest.raises(ValueError, match='aware reconstruction time'):
        rebuild_tool.rebuild(tmp_path/'invalid', '2026-09-30T00:00:00')
    assert not (tmp_path/'invalid').exists()
