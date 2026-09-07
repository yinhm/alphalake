"""固定 F10 原始帧离线复验；研究候选不计入标准事实供给。"""
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import zipfile
import zlib

ROOT = Path(__file__).resolve().parent


def texts():
    manifest = json.loads((ROOT/'manifest.json').read_text())
    blob = (ROOT/'responses.zip').read_bytes()
    assert hashlib.sha256(blob).hexdigest() == manifest['archive_sha256']
    sections = {}
    catalogs = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        requests = sorted(n for n in archive.namelist() if n.endswith('.request'))
        assert len(requests) == 19
        for name in requests:
            req = archive.read(name)
            raw = archive.read(name.replace('.request', '.response'))
            assert req[0] == 12 and raw[:4] == bytes.fromhex('b1cb7400')
            assert req[1:5] == raw[5:9] and req[10:12] == raw[10:12]
            assert struct.unpack_from('<HH', req, 6) == (len(req)-10,)*2
            zipped, length = struct.unpack_from('<HH', raw, 12)
            assert len(raw) == 16+zipped
            data = zlib.decompress(raw[16:]) if zipped != length else raw[16:]
            assert len(data) == length
            kind = struct.unpack_from('<H', req, 10)[0]
            if kind == 13:
                continue  # 握手帧，不作为公司证据。
            market = struct.unpack_from('<H', req, 12)[0]
            code = req[14:20].decode('ascii')
            assert (market, code) in ((0, '123257'), (0, '300866'), (1, '600519'))
            if kind == 719:
                count = struct.unpack_from('<H', data)[0]
                assert len(data) == 2+152*count
                cats = []
                for i in range(count):
                    row = data[2+i*152:2+(i+1)*152]
                    cat = dict(name=row[:64].split(b'\0')[0].decode('gbk'),
                               filename=row[64:144].split(b'\0')[0].decode('ascii'),
                               start=struct.unpack_from('<I', row, 144)[0],
                               length=struct.unpack_from('<I', row, 148)[0])
                    assert cat['filename'] == code+'.txt' and cat['length'] > 0
                    cats.append(cat)
                meta = json.loads(archive.read(code+'.json'))
                assert cats == meta['categories'] and meta['code'] == code and meta['market'] == market
                assert meta['host'] == manifest['host'] and meta['observed_at'].startswith(manifest['observed_date'])
                catalogs[code] = cats
            else:
                assert kind == 720 and len(req) == 114
                filename = req[22:102].split(b'\0')[0].decode('ascii')
                start, requested = struct.unpack_from('<II', req, 102)
                cat, = [c for c in catalogs[code] if c['filename'] == filename and c['start'] <= start < c['start']+c['length']]
                assert len(data) >= 12 and data[2:8].decode('ascii') == code
                got = struct.unpack_from('<H', data, 10)[0]
                assert 0 < got <= requested and len(data) in (12+got, 13+got)
                assert len(data) == 12+got or data[-1] == 0
                key = (code, cat['name'])
                section = sections.setdefault(key, bytearray())
                assert start == cat['start']+len(section) and len(section)+got <= cat['length']
                section.extend(data[12:12+got])
        assert len(sections) == manifest['sections'] == 9
        for (code, name), data in sections.items():
            cat, = [c for c in catalogs[code] if c['name'] == name]
            assert len(data) == cat['length']
    # 字节分块可能切开 GBK 字符，拼齐后才解码，拒绝替换字符。
    return {key: bytes(data).decode('gbk', errors='strict') for key, data in sections.items()}


def prices(text):
    assert re.search(r'标的股票\s*│300866\s*│', text)
    section = text.split('【3.转股价格调整】\r\n', 1)[1].split('〖免责条款〗', 1)[0]
    rows = re.findall(r'│(\d{4}-\d{2}-\d{2})│(\d{4}-\d{2}-\d{2})│[^│]+│\s*([\d.]+)│\s*([\d.]+)│', section)
    assert rows and len({r[1] for r in rows}) == len(rows)
    return sorted((ann, effective, D(before), D(after)) for ann, effective, before, after in rows)


def price_at(rows, period, asof):
    # 研究候选：按条款生效日选择；披露日期按中国次日零点。
    eligible = [r for r in rows if r[1] <= period and
                datetime.fromisoformat(r[0]).replace(tzinfo=timezone.utc)+timedelta(hours=16) <= datetime.fromisoformat(asof)]
    return max(eligible, key=lambda r: r[1])[3] if eligible else None


def verify():
    source = texts()
    bond = source['123257', '转股情况']
    rows = prices(bond)
    assert len(rows) == 8
    with (ROOT.parent/'supplement-review-2026/resolved.csv').open() as f:
        notes = [r for r in csv.DictReader(f) if r['route'] != 'tdx_standard']
    assert len(notes) == 34
    expected, = [r for r in notes if r['item'] == 'extra_conversion_price']
    assert price_at(rows, expected['period'], expected['information_as_of']) == D(expected['value']) == D('108.86')
    assert price_at(rows, '2026-05-25', expected['information_as_of']) == D('110.56')
    assert price_at(rows, '2026-06-30', '2026-05-20T15:59:59+00:00') == D('110.56')
    assert price_at(rows, '2026-06-30', '2026-05-20T16:00:00+00:00') == D('108.86')
    assert price_at(rows, '2026-08-06', expected['information_as_of']) == D('106.72')
    assert price_at(rows, '2025-06-10', expected['information_as_of']) is None
    # 明细窗口只到七月，不能拿最近记录/最新规模回填六月末面值。
    quantities = re.findall(r'│(\d{4}-\d{2}-\d{2})│未转股数量\(张\):(\d+)', bond)
    assert len(quantities) == 30 and min(d for d, _ in quantities) == '2026-07-27'
    assert not [q for d, q in quantities if d <= expected['period']]
    # 数字片段相似不构成语义相同：189.09 此处是激励股份，非远期资产。
    assert '本次拟归属的限制性股票数量：189.0927万股' in source['300866', '资本运作']
    # 名称看似接近的 FN152 六期全为零，不可替代非零到期债券分量。
    packages = sorted((ROOT.parent/'valuation-chain-2026').glob('gpcw*.zip'))
    assert len(packages) == 6
    for path in packages:
        with zipfile.ZipFile(path) as archive:
            data = archive.read(archive.namelist()[0])
        found = False
        for i in range(struct.unpack_from('<H', data, 6)[0]):
            pos = 20+11*i
            if data[pos:pos+6] == b'300866':
                offset = struct.unpack_from('<I', data, pos+7)[0]
                assert struct.unpack_from('<I', data, offset+151*4)[0] == 0
                found = True
        assert found
    # 三组总额约束各存在另一组不同的正分量：单靠总额没有唯一解。
    amounts = {r['item']: D(r['value']) for r in notes
               if r['code'] == '300866' and r['period'] == '2026-06-30'}
    assert amounts['current_bonds'] == D('181516.69')
    for keys in [('current_loans', 'current_bonds', 'current_leases'),
                 ('extra_current_financial_debt', 'extra_current_financial_equity', 'trading_forward_asset'),
                 ('extra_noncurrent_financial_debt', 'extra_noncurrent_financial_equity')]:
        original = [amounts[k] for k in keys]
        alternative = original.copy()
        alternative[0] += 1
        alternative[1] -= 1
        assert alternative != original and min(alternative) > 0 and sum(alternative) == sum(original)
    # 这次发现仅是候选证据，不得偷改成已交付的 TDX 标准输入。
    assert expected['route'] == 'cninfo_note'
    print('通过：19 对 F10 原始帧、9 个完整栏目、8 次转股价历史及日期边界；六月末面值缺失，34 项补充计数不变。')


if __name__ == '__main__':
    verify()
