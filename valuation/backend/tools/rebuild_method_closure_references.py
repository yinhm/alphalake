"""显式重建两份方法回归输入的四国参考；不在应用读取时转换历史契约。"""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

from data_sources.alphalake import AlphaLakeRequest
from data_sources.damodaran_parsers import country_risk_parser

ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path('valuation/research/current-contract-20260919')
WORKBOOK = Path('internal/source/damodaran/testdata/ctrypremJuly26.xlsx')
CODES = ('300866', '002032')


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def rebuild(output, recorded_at):
    """新目录内生成输入和收据；财务、政策和其余参考不变。"""
    output = Path(output)
    stamp = datetime.fromisoformat(recorded_at)
    if stamp.utcoffset() is None:
        raise ValueError('aware reconstruction time required')
    stamp = stamp.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    packet = country_risk_parser.alphalake_country_snapshot(ROOT/WORKBOOK)
    parser_path = Path(country_risk_parser.__file__).resolve()
    parser_hash = sha256(parser_path.read_bytes())
    expected = json.loads((ROOT/WORKBOOK.with_name('expected-selected-v2.json')).read_bytes())
    if {k:v for k,v in packet.items() if k != 'runtime'} != {k:v for k,v in expected.items() if k != 'runtime'}:
        raise ValueError('reviewed country workbook interpretation changed')
    old_receipt = json.loads((ROOT/SOURCE/'receipt.json').read_bytes())
    records, prepared = [], []
    for code in CODES:
        relative = Path('method-closure-20260912')/f'{code}-request.json.gz'
        source = ROOT/SOURCE/relative
        raw = source.read_bytes()
        evidence = next(r for r in old_receipt['records'] if r['file'] == relative.as_posix())
        if sha256(raw) != evidence['sha256']:
            raise ValueError('archived request hash mismatch')
        request = json.loads(gzip.decompress(raw))
        rebuilt = deepcopy(request)
        references = rebuilt['wacc_binding']['references']
        if references['recorded_cutoff'] is not None:
            raise ValueError('reconstruction must not masquerade as a historical recorded snapshot')
        release, = [r for r in references['releases'] if r['dataset'] == 'country-risk-cn-hk-us-rating-v1']
        if release['artifact_sha256'] != packet['workbook_sha256']:
            raise ValueError('country workbook differs from archived source')
        if datetime.fromisoformat(stamp) < datetime.fromisoformat(release['recorded_at']):
            raise ValueError('reconstruction predates archived release')
        source_rows = {(r['subject_code'], r['metric_code']):r for r in packet['observations']}
        rows = references['country_risk']
        if len(rows) != 10 or len({(r['subject_code'], r['metric_code']) for r in rows}) != 10:
            raise ValueError('expected frozen three-country input')
        for row in rows:
            current = source_rows[(row['subject_code'], row['metric_code'])]
            if any(row[key] != value for key, value in current.items()):
                raise ValueError('existing country source value or provenance changed')
        new_release_id = max(r['release_id'] for r in references['releases']) + 1
        normalization = f"country-risk-decimal12-v1;{parser_hash};{packet['runtime']}"
        # 与reference_publication.go相同的解释签名；记录新解析时点，不伪造旧时点发布。
        content_key = sha256(f"{packet['workbook_sha256']}\n{packet['parser_version']}\n{normalization}".encode())
        release.update(release_id=new_release_id, dataset='country-risk-rating-v2',
                       parser_version=packet['parser_version'], normalization_version=normalization,
                       content_key=content_key, recorded_at=stamp)
        for row in rows:
            row['release_id'] = new_release_id
        next_id = max(r['observation_id'] for r in rows) + 1
        for offset, row in enumerate(r for r in packet['observations'] if r['subject_code'] == 'IL'):
            rows.append(dict(row, observation_id=next_id+offset, release_id=new_release_id,
                             artifact_id=release['artifact_id'], observation_date=packet['observation_date'],
                             method_code='rating', raw_unit='fraction', value_status='reported'))
        AlphaLakeRequest.model_validate(rebuilt)
        body = gzip.compress(encode(rebuilt), mtime=0)
        prepared.append((relative, body))
        records.append(dict(source=(SOURCE/relative).as_posix(), source_sha256=sha256(raw),
                            file=relative.as_posix(), sha256=sha256(body),
                            old_country_rows=10, new_country_rows=13))
    receipt = dict(purpose='current_four_country_regression_inputs_not_historical_publications',
                   recorded_at=stamp, workbook=WORKBOOK.as_posix(), workbook_sha256=packet['workbook_sha256'],
                   parser=parser_path.relative_to(ROOT).as_posix(), parser_sha256=parser_hash,
                   runtime=packet['runtime'], country_scope=['CN', 'HK', 'US', 'IL'],
                   records=records)
    # 禁止覆写任何既有输入；使用新目录，核验后由调用方显式切换路径。
    output.mkdir(parents=True, exist_ok=False)
    for relative, body in prepared:
        target = output/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    (output/'receipt.json').write_bytes(encode(receipt))
    return receipt


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--recorded-at', required=True, help='本次重建实际UTC时点；不得冒用原发布时点')
    args = parser.parse_args()
    print(json.dumps(rebuild(args.output, args.recorded_at), ensure_ascii=False, indent=2))
