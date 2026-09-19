"""两家公司主库发布验收；沿用已审核政策，只重新绑定实际源与补充证据。"""
import argparse
from copy import deepcopy
import gzip
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
from datetime import datetime, timezone

from data_sources.alphalake import AlphaLakeRequest, content_hash
from tools.incremental_valuation import evaluate_changed
from tools.verify_nonfinancial_dcf import verify

ROOT = Path(__file__).resolve().parents[3]
ASOF = '2026-09-18T00:00:00Z'
SOURCES = {'300866': ROOT/'valuation/research/reviewed-assets-20260917',
           '002032': ROOT/'valuation/research/reviewed-assets-20260917/supor'}


def bind(request, data):
    request = deepcopy(request)
    request['data'] = data
    notes = {r['item']: r for r in data['supplements']}
    for rule in request['policy']['asset_addbacks']:
        for component in [rule] + ([rule['restricted_component']] if rule.get('restricted_component') else []):
            note = notes[component['item']]
            component.update(import_sha256=note['import_sha256'], evidence_sha256=content_hash(note))
        window = next(w for w in data['windows'] if w['field'] == rule['field'])
        assert len(window['source_fact_ids']) == 1
        fact = next(f for f in data['facts'] if f['fact_id'] == window['source_fact_ids'][0])
        rule['source_artifact_sha256'] = fact['artifact_sha256']
    return request


def export(binary, db, code):
    return json.loads(subprocess.check_output([binary, 'export-valuation', str(db), code,
        '--period', '2026-06-30', '--as-of', ASOF]))


def check_pair(before, after):
    assert before['windows'] == after['windows'], 'financial windows changed'
    assert before['source_conflicts'] == after['source_conflicts']
    strip = lambda data: [{k: v for k, v in f.items() if k not in ('pdf_sha256', 'document_provenance')}
                          for f in data['facts']]
    assert strip(before) == strip(after), 'financial facts changed'


def lifecycle(binary, database, output, assignments):
    """真实库副本撤销／恢复，不在发布库制造试验审核历史。"""
    outcomes = {}
    with tempfile.TemporaryDirectory(prefix='review-lifecycle-', dir=output) as folder:
        copy = Path(folder)/'copy.duckdb'
        shutil.copy2(database, copy)
        manifest = Path(folder)/'action.json'
        def import_records(records):
            manifest.write_text(json.dumps(records, ensure_ascii=False))
            return subprocess.check_output([binary, 'import-supplements', str(copy), str(manifest)], text=True).strip()
        def history(code):
            return json.loads(subprocess.check_output([binary, 'supplement-history', str(copy), code]))
        for code, assignment in assignments.items():
            source = (ROOT/'internal/ingest/testdata/reviewed-assets-2026/supplements.json' if code == '300866'
                      else SOURCES[code]/'supplements.json')
            records = json.loads(source.read_text())
            record = records[0 if code == '300866' else 1]  # 苏泊尔撤销受限额，不能当作零。
            before = export(binary, copy, code)
            note = next(n for n in before['supplements'] if n['item'] == record['item'])
            revoke = dict(record, action='revoke', supersedes_sha256=note['import_sha256'],
                reviewed_at=datetime.now(timezone.utc).isoformat(), review_note='隔离副本验收：撤销后必须缺证拒绝。')
            import_records([revoke])
            assert 'inserted=0' in import_records(records)  # 原发布重放不得复活。
            revoked = export(binary, copy, code)
            assert not any(n['item'] == record['item'] for n in revoked['supplements'])
            try:
                evaluate_changed(AlphaLakeRequest.model_validate(dict(assignment, data=revoked)), None, output/'runs')
            except ValueError:
                pass
            else:
                raise AssertionError('revoked evidence accepted')
            rows = history(code)
            head = next(h for h in rows if h['item'] == record['item'] and h['is_current'])
            restore = dict(record, action='replace', supersedes_sha256=head['import_sha256'],
                reviewed_at=datetime.now(timezone.utc).isoformat(), review_note='隔离副本验收：按相同原文重新审核恢复，旧政策必须重绑。')
            import_records([restore])
            restored = export(binary, copy, code)
            try:
                evaluate_changed(AlphaLakeRequest.model_validate(dict(assignment, data=restored)), None, output/'runs')
            except ValueError:
                pass
            else:
                raise AssertionError('old policy accepted new review')
            result = evaluate_changed(AlphaLakeRequest.model_validate(bind(dict(assignment, data=restored), restored)), None, output/'runs')
            verify(result)
            assert len([h for h in history(code) if h['item'] == record['item']]) == 3
            outcomes[code] = dict(revoke_rejected=True, old_replay_did_not_revive=True,
                restore_requires_policy_rebinding=True, review_actions=3,
                restored_value=result['report']['final']['value_per_share'])
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lifecycle', action='store_true')
    parser.add_argument('baseline')
    parser.add_argument('candidate')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    runs = args.output/'runs'
    os.environ['ALPHALAKE_VALUATION_RUN_DIR'] = str(runs.resolve())
    binary = str(ROOT/'alphalake')
    assignments, receipt = {}, {}
    for code, directory in SOURCES.items():
        before, after = (export(binary, db, code) for db in (args.baseline, args.candidate))
        check_pair(before, after)
        assert len(after['supplements']) - len(before['supplements']) == (1 if code == '300866' else 4)
        current = ROOT/'valuation/research/current-contract-20260919'/directory.relative_to(ROOT/'valuation/research')
        original = json.loads(gzip.decompress((current/'before-request.json.gz').read_bytes()))
        original['data'] = before
        reviewed = bind(json.loads(gzip.decompress((current/'after-request.json.gz').read_bytes())), after)
        baseline = evaluate_changed(AlphaLakeRequest.model_validate(original), None, runs)
        result = evaluate_changed(AlphaLakeRequest.model_validate(reviewed), baseline['refresh']['last_success'], runs)
        assert result['refresh']['action'] == 'recalculated'
        reused = evaluate_changed(AlphaLakeRequest.model_validate(reviewed), result['refresh']['last_success'], runs)
        assert reused['refresh']['action'] == 'reused_unchanged'
        verify(result)
        for key in ('revenue_projections', 'ebit_projections', 'reinvestment_projections', 'fcff_projections', 'value_of_operating_assets'):
            assert baseline['report']['dcf'][key] == result['report']['dcf'][key], key
        for failure in ('missing', 'hash', 'expired'):
            bad = deepcopy(reviewed)
            if failure == 'missing': bad['data']['supplements'] = []
            elif failure == 'hash': bad['policy']['asset_addbacks'][0]['evidence_sha256'] = '0'*64
            else: bad['data']['information_as_of'] = '2026-10-18T00:00:00Z'
            try:
                evaluate_changed(AlphaLakeRequest.model_validate(bad), result['refresh']['last_success'], runs)
            except ValueError:
                pass
            else:
                raise AssertionError(f'accepted {failure}')
        assignments[code] = {k: v for k, v in reviewed.items() if k != 'data'}
        receipt[code] = dict(before_value=baseline['report']['final']['value_per_share'],
            after_value=result['report']['final']['value_per_share'], before_run_id=baseline['run_id'],
            after_run_id=result['run_id'], facts=len(after['facts']), windows=len(after['windows']),
            added_supplements=len(after['supplements'])-len(before['supplements']),
            financial_values_unchanged=True, operating_forecasts_unchanged=True,
            revaluation='recalculated', replay='reused_unchanged', rejected=['missing','hash','expired'])
    policy = dict(policy_version='reviewed-main-assets-20260918',
        review_note='两家公司2026H1已审核资产按主库证据绑定；固定原WACC与经营假设、账面价值代理，非当前市场目标价。', assignments=assignments)
    if args.lifecycle:
        checks = lifecycle(binary, args.candidate, args.output, assignments)
        for code, check in checks.items():
            assert check['restored_value'] == receipt[code]['after_value']
            receipt[code]['lifecycle'] = check
    (args.output/'policy.json').write_text(json.dumps(policy, ensure_ascii=False, indent=2)+'\n')
    (args.output/'valuation-acceptance.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
